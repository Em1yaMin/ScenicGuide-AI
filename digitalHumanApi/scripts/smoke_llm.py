#!/usr/bin/env python
"""LLM 接入层冒烟脚本 — DeepSeek 思考格式兼容性验证（阶段1 验收证据）。

对配置的对话 LLM 端点（默认 opencode-go + deepseek-v4-flash）执行一轮真实对话，
验证四件事：
  1. 基本调用：invoke 返回正常 content
  2. reasoning_content 剥离：思考内容不泄漏到 LangChain 响应
  3. 流式调用：思考阶段 chunk 为空串、不报错，最终可合并出完整回答
  4. max_tokens 生效：请求体含原生 max_tokens 字段且长度限制被端点遵守
     （langchain-openai 默认发送 max_completion_tokens，DeepSeek 系端点会静默忽略）

用法（复用项目 .env 配置，也可用环境变量覆盖）:
    LLM_API_KEY=sk-xxx python scripts/smoke_llm.py            # 主端点
    LLM_FALLBACK_API_KEY=sk-xxx python scripts/smoke_llm.py --fallback  # 备用端点

退出码 0 表示全部通过；任一验证失败退出码 1。

实测结论记录：doc/llm-adapter-smoke-test.md
"""

import argparse
import json
import sys
from typing import List

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import settings
from app.core.llm import create_chat_llm, get_chat_llm

PASS = 0
FAIL = 1


def _report(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def smoke_basic(llm, question: str) -> bool:
    """基本调用 + reasoning_content 剥离验证。"""
    print(f"\n=== 1) 基本调用（含 reasoning_content 剥离检查）===")
    resp = llm.invoke(question)
    print(f"content: {resp.content!r}")
    kwargs_json = json.dumps(resp.additional_kwargs, ensure_ascii=False, default=str)
    leaked = "reasoning" in kwargs_json.lower()
    print(f"additional_kwargs: {kwargs_json[:200]}")
    usage = resp.response_metadata.get("token_usage") or {}
    print(
        f"usage: completion={usage.get('completion_tokens')} "
        f"reasoning={((usage.get('completion_tokens_details') or {}).get('reasoning_tokens'))}"
    )
    ok = bool(resp.content) and not leaked
    return _report("基本调用 content 非空且思考未泄漏", ok)


def smoke_stream(llm, question: str) -> bool:
    """流式调用 + 思考空 chunk 验证。"""
    print(f"\n=== 2) 流式调用（思考阶段空 chunk）===")
    chunks: List[str] = []
    empty_thinking_chunks = 0
    for chunk in llm.stream(question):
        if chunk.content:
            chunks.append(chunk.content)
        else:
            empty_thinking_chunks += 1
    merged = "".join(chunks)
    print(f"思考阶段空 chunk 数: {empty_thinking_chunks}")
    print(f"合并 content: {merged!r}")
    ok = bool(merged)
    return _report("流式正常、思考 chunk 被自动丢弃", ok)


def smoke_max_tokens(llm, question: str, limit: int) -> bool:
    """max_tokens 字段注入与生效验证（经工厂 extra_body 注入原生 max_tokens）。"""
    print(f"\n=== 3) max_tokens={limit} 生效验证 ===")
    captured: dict = {}

    class SpyTransport(httpx.BaseTransport):
        def handle_request(self, request):
            captured["body"] = json.loads(request.content)
            with httpx.Client(base_url=llm.openai_api_base, timeout=60) as c:
                return c.send(request)

    spy_llm = create_chat_llm(
        api_base=llm.openai_api_base,
        api_key=llm.openai_api_key.get_secret_value(),
        model=llm.model_name,
        max_tokens=limit,
        timeout=60,
        http_client=httpx.Client(transport=SpyTransport()),
    )
    resp = spy_llm.invoke(question)
    body = captured.get("body", {})
    print(f"请求体字段: {sorted(body.keys())}")
    print(f"max_tokens={body.get('max_tokens')}  max_completion_tokens={body.get('max_completion_tokens')}")
    usage = resp.response_metadata.get("token_usage") or {}
    completion = usage.get("completion_tokens")
    print(f"实际 completion_tokens={completion}（含思考）")
    field_ok = body.get("max_tokens") == limit and "max_completion_tokens" not in body
    limit_ok = completion is not None and completion <= limit
    return _report(
        "请求体含原生 max_tokens 且端点遵守长度限制",
        field_ok and limit_ok,
        f"字段={field_ok} 限制={limit_ok}",
    )


def smoke_multiturn(llm) -> bool:
    """多轮对话验证（历史 assistant 消息不带 reasoning_content 是否被端点接受）。"""
    print(f"\n=== 4) 多轮对话（历史不带 reasoning_content）===")
    messages = [
        HumanMessage(content="黄山在哪里？"),
        AIMessage(content="黄山在安徽省南部。"),
        HumanMessage(content="有什么好玩的？"),
    ]
    try:
        resp = llm.invoke(messages)
        print(f"content: {resp.content[:100]!r}...")
        return _report("多轮对话正常（端点容错，不强制回传 reasoning_content）", True)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return _report(
            "多轮对话（官方 DeepSeek API 需自定义 reasoning_content 回传，见冒烟文档）",
            False,
            str(exc)[:200],
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="对话 LLM 兼容性冒烟")
    parser.add_argument(
        "--fallback", action="store_true", help="使用备用端点（LLM_FALLBACK_* 配置）"
    )
    parser.add_argument("--question", default="用一句话介绍杭州西湖")
    parser.add_argument(
        "--max-tokens", type=int, default=64, help="长度限制验证值（思考+输出总长）"
    )
    args = parser.parse_args()

    llm = get_chat_llm(
        use_fallback=args.fallback,
        max_tokens=None,  # 冒烟 3 单独验证注入；1/2/4 不限制
        temperature=0.7,
        timeout=60,
    )
    endpoint = "备用端点" if args.fallback else "主端点"
    print(
        f"冒烟端点: {endpoint}\n"
        f"  base = {llm.openai_api_base}\n"
        f"  model = {llm.model_name}\n"
    )

    results = [
        smoke_basic(llm, args.question),
        smoke_stream(llm, args.question),
        smoke_max_tokens(llm, args.question, args.max_tokens),
        smoke_multiturn(llm),
    ]
    all_ok = all(results)
    print(f"\n=== 冒烟{'全部通过' if all_ok else '存在失败'} ===")
    return PASS if all_ok else FAIL


if __name__ == "__main__":
    sys.exit(main())
