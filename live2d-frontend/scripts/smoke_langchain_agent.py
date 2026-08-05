#!/usr/bin/env python
"""LangChainAgent 引擎冒烟脚本 — 验证 ADH 数字人走本后端 LangChain 链路（T4 验收证据）。

对本后端 /api/v1/dialogue/chat 执行一轮真实对话（流式），验证：
  1. SSE 流式响应可解析出文本增量（message 事件）
  2. conversation_id 事件可获取会话 ID
  3. 非流式 ChatResponse JSON 兼容
  4. LangChainAgent 引擎类可正常驱动上述链路（与 /adh/agent/infer 同路径）

注意：后端流式端点（T3）未落地时第 1 步将 FAIL（后端返回阻塞 JSON 而非 SSE），
属预期；引擎侧第 2 步因内置阻塞 JSON 回退解析仍应 PASS——链路在 T3 前后均可用。

用法（复用 ADH venv 运行，需本后端已启动）:
    ./venv/Scripts/python.exe scripts/smoke_langchain_agent.py                      # 默认 http://127.0.0.1:8000
    ./venv/Scripts/python.exe scripts/smoke_langchain_agent.py --url http://x.x.x.x:8000 --query "介绍一下这个景区" --token <jwt>

退出码 0 表示全部通过；任一验证失败退出码 1。
"""

import argparse
import asyncio
import json
import sys

import httpx

from digitalHuman.agent.core.langchainAgent import LangChainAgent
from digitalHuman.utils import TextMessage
from digitalHuman.utils.configParser import CONFIG_ROOT_PATH, parseConfig

PASS = 0
FAIL = 1


def _report(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return ok


async def smoke_stream_http(url: str, token: str, user: str, query: str) -> bool:
    """直接验证后端 SSE 契约（事件结构/增量文本/会话 ID）。"""
    print(f"\n=== 1) 后端流式接口 SSE 契约（POST {url}/api/v1/dialogue/chat）===")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {
        "query": query,
        "user": user,
        "streaming": True,
        "use_rag": True,
        "rag_top_k": 3,
    }
    ok = True
    text_parts = []
    conversation_id = ""
    try:
        async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
            async with client.stream("POST", url.rstrip("/") + "/api/v1/dialogue/chat",
                                     headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    ok &= _report(f"HTTP {resp.status_code}", False, body[:200])
                    return ok
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or line in ("data: [DONE]", "[DONE]"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip())
                    event_type = data.get("type", data.get("event", ""))
                    if data.get("conversation_id"):
                        conversation_id = data["conversation_id"]
                    if event_type == "token" and data.get("text"):
                        text_parts.append(data["text"])
                    elif event_type == "message" and data.get("answer"):
                        text_parts.append(data["answer"])
                    elif event_type == "step":
                        print(f"  [step] {data.get('tool') or data.get('name', '')}")
                    elif event_type == "error":
                        print(f"  [error] {data.get('message', '')} fallback={data.get('fallback')}")
    except Exception as e:
        return _report("后端流式调用", False, str(e))
    answer = "".join(text_parts)
    print(f"  answer: {answer[:200]!r}")
    ok &= _report("流式文本增量非空", bool(answer), f"{len(text_parts)} chunks")
    ok &= _report("conversation_id 事件可取", bool(conversation_id), conversation_id)
    return ok


async def smoke_agent_engine(url: str, token: str, user: str, query: str) -> bool:
    """经 LangChainAgent 引擎（ADH 实际路径）驱动一轮流式对话。"""
    print("\n=== 2) LangChainAgent 引擎流式对话（/adh/agent 同路径）===")
    cfg = parseConfig(f"{CONFIG_ROOT_PATH}/agents/langchainAgent.yaml")
    agent = LangChainAgent(cfg)
    settings = {
        "LLM_API_URL": url,
        "LLM_API_TOKEN": token,
        "LLM_API_USER": user,
        "LLM_USE_RAG": "true",
        "LLM_RAG_TOP_K": "3",
    }
    ok = True
    try:
        print("  createConversation...")
        conversation_id = await agent.createConversation(streaming=True, **settings)
        ok &= _report("createConversation 返回会话 ID", bool(conversation_id), conversation_id)
        print("  run(streaming=True)...")
        outputs = []
        async for text in agent.run(TextMessage(data=query), streaming=True,
                                    conversation_id=conversation_id, **settings):
            outputs.append(text)
        answer = "".join(outputs)
        print(f"  answer: {answer[:200]!r}")
        ok &= _report("引擎流式输出非空", bool(answer), f"{len(outputs)} chunks")
    except Exception as e:
        return _report("引擎流式对话", False, str(e))
    return ok


async def smoke_blocking(url: str, token: str, user: str, query: str) -> bool:
    """非流式 ChatResponse JSON 兼容。"""
    print("\n=== 3) 非流式响应（streaming=false）===")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"query": query, "user": user, "streaming": False}
    try:
        async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
            resp = await client.post(url.rstrip("/") + "/api/v1/dialogue/chat",
                                     headers=headers, json=payload)
        if resp.status_code != 200:
            return _report(f"HTTP {resp.status_code}", False, resp.text[:200])
        data = resp.json()
        ok = bool(data.get("answer"))
        return _report("非流式 answer 非空", ok, f"conversation_id={data.get('conversation_id', '')}")
    except Exception as e:
        return _report("非流式调用", False, str(e))


async def main() -> int:
    parser = argparse.ArgumentParser(description="LangChainAgent 引擎冒烟")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="本后端地址（默认 http://127.0.0.1:8000）")
    parser.add_argument("--token", default="", help="可选 Bearer JWT（登录/游客 token）")
    parser.add_argument("--user", default="visitor", help="用户标识（默认 visitor）")
    parser.add_argument("--query", default="你好，请介绍一下景区", help="测试问题")
    args = parser.parse_args()

    print(f"冒烟目标: {args.url}  user={args.user}")
    results = [
        await smoke_stream_http(args.url, args.token, args.user, args.query),
        await smoke_agent_engine(args.url, args.token, args.user, args.query),
        await smoke_blocking(args.url, args.token, args.user, args.query),
    ]
    passed = sum(results)
    print(f"\n结果: {passed}/{len(results)} 通过")
    return PASS if passed == len(results) else FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
