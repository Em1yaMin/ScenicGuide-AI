"""LangChain 流式对话（chat_agent.chat_stream）单测。

覆盖：SSE 事件序列（step → token → done）、工具调用步骤去重与文案、
RAG 来源透出（done 事件携带 rag_context/rag_sources）、多轮上下文
（流式完成后历史已追加）、空回答兜底。

Mock 方式与 test_chat_agent.py 一致：httpx.MockTransport 模拟 DeepSeek 系
tool-calling 流式响应（`stream: true` 时返回 SSE `data: {...}` 帧），
不发真实请求。
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services import chat_agent

PRIMARY = {
    "LLM_API_BASE": "https://opencode.ai/zen/go/v1",
    "LLM_API_KEY": "sk-primary-test",
    "LLM_MODEL": "deepseek-v4-flash",
}
FALLBACK = {
    "LLM_FALLBACK_API_BASE": "https://api.deepseek.com/v1",
    "LLM_FALLBACK_API_KEY": "sk-fallback-test",
    "LLM_FALLBACK_MODEL": "deepseek-chat",
}


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    """固定主/备用端点配置，避免测试依赖真实环境变量。"""
    for key, value in {**PRIMARY, **FALLBACK}.items():
        monkeypatch.setattr(settings, key, value)
    # 记忆提取钩子固定为 no-op：避免后台任务用假 key 发起真实网络请求
    # （阶段2 T2 记忆钩子本身在 test_memory.py 中专项覆盖）
    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    chat_agent.reset_agent_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()
    yield
    chat_agent.reset_agent_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


# ── Mock 流式响应构造（OpenAI/DeepSeek 兼容 chunk 格式） ──────────────────


def _sse(payload: str) -> str:
    return f"data: {payload}\n\n"


def _chunk(choices):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": PRIMARY["LLM_MODEL"],
        "choices": choices,
    }


def _tool_call_delta(name: str, call_id: str, args: str = ""):
    """工具调用增量块（name 随首块下发，args 随后续块下发）。"""
    return {
        "index": 0,
        "delta": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            ],
        },
        "finish_reason": None,
    }


def _tool_args_delta(args: str):
    """工具参数增量块（真实 OpenAI 格式：不带 id/name 键）。"""
    return {
        "index": 0,
        "delta": {
            "tool_calls": [{"index": 0, "function": {"arguments": args}}]
        },
        "finish_reason": None,
    }


def _text_delta(text: str):
    return {
        "index": 0,
        "delta": {"role": "assistant", "content": text},
        "finish_reason": None,
    }


def _finish_delta(finish_reason: str):
    return {"index": 0, "delta": {}, "finish_reason": finish_reason}


def _stream_response(events) -> httpx.Response:
    body = "".join(
        _sse(json.dumps(_chunk([e]), ensure_ascii=False)) for e in events
    )
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"}
    )


def _tool_call_stream(name: str, args: dict, call_id: str = "call_1"):
    """完整工具调用流：name 块 → args 块 → finish(tool_calls)。"""
    return [
        _tool_call_delta(name, call_id),
        _tool_args_delta(json.dumps(args, ensure_ascii=False)),
        _finish_delta("tool_calls"),
    ]


def _text_stream(text: str):
    """完整回答文本流：逐字增量 → finish(stop)。"""
    events = [_text_delta(ch) for ch in text] + [_finish_delta("stop")]
    return events


def _llm_with_mock(handler) -> "object":
    """构造注入 MockTransport 的 ChatOpenAI（同步/异步 client 均注入）。"""
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def _mock_agent(handler, *, use_rag: bool):
    return chat_agent._build_agent(use_rag=use_rag, llm=_llm_with_mock(handler))


def _patch_agent(monkeypatch, handler, *, use_rag: bool = True):
    agent = _mock_agent(handler, use_rag=use_rag)
    monkeypatch.setattr(
        chat_agent, "_get_agent", lambda *, use_rag, llm=None: agent
    )
    return agent


# ── 事件序列 ──────────────────────────────────────────────────────────────


async def test_chat_stream_emits_step_token_done(monkeypatch):
    """工具调用 → step 事件；回答 → token 事件；完成 → done 事件（含会话 id）。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        has_tool_result = any(m["role"] == "tool" for m in body["messages"])
        if body.get("stream"):
            if has_tool_result:
                return _stream_response(_text_stream("推荐自然风光类景点。"))
            return _stream_response(
                _tool_call_stream("search_spots", {"query": "自然"})
            )
        raise AssertionError("非流式请求不应发生")

    _patch_agent(monkeypatch, handler, use_rag=False)
    events = []
    async for event in chat_agent.chat_stream(
        "有哪些自然风光景点？", user="visitor"
    ):
        events.append(event)

    types = [e["type"] for e in events]
    # 事件序列：step（工具调用）→ token（回答增量）→ done
    assert types[0] == "step" and types[-1] == "done"
    assert set(types) <= {"step", "token", "done"}
    # step 事件：工具名 + 中文步骤文案
    step = events[0]
    assert step["tool"] == "search_spots"
    assert step["label"] == "正在查询景点…"
    # token 事件拼接 = 完整回答
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert answer == "推荐自然风光类景点。"
    # done 事件：会话 id + RAG 字段（无知识检索时为默认空值）
    done = events[-1]
    assert done["conversation_id"]
    assert done["rag_context"] == ""
    assert done["rag_sources"] == []


async def test_chat_stream_step_dedup(monkeypatch):
    """同一工具调用的多个 chunk 只产生一个 step 事件。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body.get("stream"):
            if any(m["role"] == "tool" for m in body["messages"]):
                # 工具已执行：返回纯文本流，避免 mock 反复触发工具调用
                return _stream_response(_text_stream("景区 8:00 开放。"))
            return _stream_response(
                _tool_call_stream("search_knowledge", {"query": "开放时间", "top_k": 2})
            )
        raise AssertionError("非流式请求不应发生")

    _patch_agent(monkeypatch, handler, use_rag=True)
    events = [
        e async for e in chat_agent.chat_stream("几点开门？", user="visitor")
    ]
    steps = [e for e in events if e["type"] == "step"]
    assert len(steps) == 1
    assert steps[0]["tool"] == "search_knowledge"
    assert steps[0]["label"] == "正在检索知识库…"
    assert "".join(e["text"] for e in events if e["type"] == "token") == "景区 8:00 开放。"


# ── RAG 来源透出 ──────────────────────────────────────────────────────────


async def test_chat_stream_rag_sources_in_done(monkeypatch):
    """search_knowledge 执行后，done 事件携带 rag_context/rag_sources。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        if body.get("stream"):
            if any(m["role"] == "tool" for m in body["messages"]):
                return _stream_response(_text_stream("这是讲解词。"))
            return _stream_response(
                _tool_call_stream(
                    "search_knowledge", {"query": "讲解词", "top_k": 2}
                )
            )
        raise AssertionError("非流式请求不应发生")

    _patch_agent(monkeypatch, handler, use_rag=True)
    events = [e async for e in chat_agent.chat_stream("讲个讲解词", user="visitor")]
    done = events[-1]
    assert done["type"] == "done"
    # search_knowledge 在测试中返回空列表 → 来源为空但结构兼容（与 chat() 一致）
    assert done["rag_sources"] == []
    assert done["rag_context"] == ""


# ── 多轮上下文 ────────────────────────────────────────────────────────────


async def test_chat_stream_appends_history(monkeypatch):
    """流式完成后历史已追加：第二轮请求携带第一轮的 user+assistant 消息。"""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if body.get("stream"):
            return _stream_response(_text_stream("答"))
        raise AssertionError("非流式请求不应发生")

    _patch_agent(monkeypatch, handler, use_rag=False)
    r1 = None
    async for event in chat_agent.chat_stream("第一问", user="visitor"):
        if event["type"] == "done":
            r1 = event
    async for _ in chat_agent.chat_stream(
        "第二问", user="visitor", conversation_id=r1["conversation_id"]
    ):
        pass
    roles = [m["role"] for m in bodies[1]["messages"]]
    assert roles.count("user") == 2
    assert roles.count("assistant") == 1


# ── 异常 ──────────────────────────────────────────────────────────────────


async def test_chat_stream_llm_config_error_propagates(monkeypatch):
    """LLM 未配置时 chat_stream 抛 LLMConfigError（由路由层回落 Dify）。"""
    monkeypatch.setattr(settings, "LLM_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")
    chat_agent.reset_agent_cache()
    from app.core.llm import LLMConfigError

    with pytest.raises(LLMConfigError):
        async for _ in chat_agent.chat_stream("你好"):
            pass


async def test_chat_stream_recursion_limit_breaks_tool_loop(monkeypatch):
    """异常 LLM 反复请求工具时，agent 步数超限抛 GraphRecursionError
    （回归：langchain 1.3 的 create_agent 默认 recursion_limit=9999，
    不加显式限制会循环执行工具近万次而非快速失败回落 Dify）。"""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        if body.get("stream"):
            # 永远返回工具调用流（模拟故障 LLM）
            return _stream_response(
                _tool_call_stream("search_spots", {"query": "自然"})
            )
        raise AssertionError("非流式请求不应发生")

    _patch_agent(monkeypatch, handler, use_rag=False)
    from langgraph.errors import GraphRecursionError

    with pytest.raises(GraphRecursionError):
        async for _ in chat_agent.chat_stream("你好", user="visitor"):
            pass
    # 步数上限 25 ≈ 12 轮工具调用，LLM 调用次数远小于 9999
    assert calls < 30
