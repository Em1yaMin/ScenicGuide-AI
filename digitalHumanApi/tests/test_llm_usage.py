"""LLM 调用层埋点（UsageTrackingChatOpenAI，批次7 C5）单测。

经 llm.py 工厂构造（USAGE_TRACKING_ENABLED 默认 true 时包装为
UsageTrackingChatOpenAI），用 httpx.MockTransport 模拟端点响应：

- 非流式：响应 usage 字段 → 记账 input/output/duration/链路/用户
- 流式：usage-only chunk（stream_options.include_usage）→ 记账
- 无 usage 的响应/调用异常 → 不记账
- 调用行为不变（回答/思考剥离/流式合并与未包装一致）
- USAGE_TRACKING_ENABLED=false → 不包装（零行为差异）
"""
import json

import httpx
import pytest

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services.usage_tracker import (
    CHAIN_DIALOGUE,
    CHAIN_OTHER,
    usage_context,
    usage_tracker,
)

_CHAT_COMPLETION = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "created": 1700000000,
    "model": "deepseek-v4-flash",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "最终回答"},
        }
    ],
    "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    },
}


@pytest.fixture(autouse=True)
def _reset_tracker():
    usage_tracker.reset()
    yield
    usage_tracker.reset()


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    monkeypatch.setattr(settings, "LLM_API_BASE", "https://opencode.ai/zen/go/v1")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(settings, "LLM_FALLBACK_API_KEY", "")


def _llm(handler):
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


# ── 非流式埋点 ──


def test_invoke_records_usage_with_context():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CHAT_COMPLETION)

    llm = _llm(handler)
    with usage_context(CHAIN_DIALOGUE, "u-1"):
        resp = llm.invoke("你好")
    assert resp.content == "最终回答"  # 调用行为不变

    assert usage_tracker.pending_count() == 1
    pending = usage_tracker._queue.get_nowait()
    assert pending["model"] == "deepseek-v4-flash"
    assert pending["input_tokens"] == 10
    assert pending["output_tokens"] == 20
    assert pending["total_tokens"] == 30
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-1"
    assert pending["duration_ms"] >= 0


def test_ainvoke_records_usage():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CHAT_COMPLETION)

    import asyncio

    async def run():
        llm = _llm(handler)
        with usage_context(CHAIN_DIALOGUE, "u-2"):
            resp = await llm.ainvoke("你好")
        assert resp.content == "最终回答"

    asyncio.run(run())
    pending = usage_tracker._queue.get_nowait()
    assert pending["input_tokens"] == 10
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-2"


def test_invoke_without_context_defaults_other():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CHAT_COMPLETION)

    _llm(handler).invoke("你好")
    pending = usage_tracker._queue.get_nowait()
    assert pending["chain"] == CHAIN_OTHER
    assert pending["user"] is None


def test_invoke_without_usage_not_recorded():
    """端点不返回 usage 字段（非流式）：跳过记账（宁缺毋滥）。"""
    no_usage = dict(_CHAT_COMPLETION)
    no_usage.pop("usage")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=no_usage)

    _llm(handler).invoke("你好")
    assert usage_tracker.pending_count() == 0


def test_invoke_error_not_recorded():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(Exception):
        _llm(handler).invoke("你好")
    assert usage_tracker.pending_count() == 0


def test_request_payload_unchanged_with_tracking():
    """埋点不改变 LLM 调用行为：请求体与未包装一致（max_tokens 注入不受影响）。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_CHAT_COMPLETION)

    llm = get_chat_llm(
        max_tokens=32,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    llm.invoke("你好")
    assert seen["body"]["max_tokens"] == 32
    assert "stream_options" not in seen["body"]  # 非流式不附加
    assert usage_tracker.pending_count() == 1


# ── 流式埋点 ──


def _stream_sse(chunks, usage=None):
    """构造 SSE 响应体：内容分块 + 可选的 usage-only chunk。"""
    body_chunks = []
    for i, content in enumerate(chunks):
        body_chunks.append(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": content},
                        "finish_reason": "stop" if i == len(chunks) - 1 else None,
                    }
                ],
            }
        )
    if usage is not None:
        body_chunks.append(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "deepseek-v4-flash",
                "choices": [],
                "usage": usage,
            }
        )
    sse = "".join(
        f"data: {json.dumps(c)}\n\n" for c in body_chunks
    ) + "data: [DONE]\n\n"
    return sse


def _sse_handler(sse: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=sse.encode(),
        )

    return handler


def test_stream_records_usage_from_final_chunk():
    """流式：usage-only chunk 的 usage_metadata 被采集记账（内容流不变）。"""
    sse = _stream_sse(
        ["你", "好"],
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    llm = _llm(_sse_handler(sse))
    with usage_context(CHAIN_DIALOGUE, "u-3"):
        collected = [c for c in llm.stream("你好")]
    # 流式内容合并不受影响
    assert "".join(c.content for c in collected) == "你好"
    # 流式请求附带 stream_options.include_usage（请求体行为，见下方断言）

    pending = usage_tracker._queue.get_nowait()
    assert pending["input_tokens"] == 10
    assert pending["output_tokens"] == 5
    assert pending["total_tokens"] == 15
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-3"


def test_stream_without_usage_not_recorded():
    """流式端点不返回 usage（网关不支持）：跳过记账，流不受影响。"""
    sse = _stream_sse(["你", "好"], usage=None)
    llm = _llm(_sse_handler(sse))
    collected = [c for c in llm.stream("你好")]
    assert "".join(c.content for c in collected) == "你好"
    assert usage_tracker.pending_count() == 0


def test_astream_records_usage():
    import asyncio

    async def run():
        sse = _stream_sse(
            ["你", "好"],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        llm = _llm(_sse_handler(sse))
        chunks = []
        with usage_context(CHAIN_DIALOGUE, "u-4"):
            async for c in llm.astream("你好"):
                chunks.append(c)
        assert "".join(c.content for c in chunks) == "你好"

    asyncio.run(run())
    pending = usage_tracker._queue.get_nowait()
    assert pending["input_tokens"] == 10
    assert pending["output_tokens"] == 5
    assert pending["user"] == "u-4"


def test_stream_request_includes_stream_options():
    """流式请求携带 stream_options.include_usage（USAGE_STREAM_USAGE=true）。"""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_stream_sse(["好"], usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}).encode(),
        )

    list(_llm(handler).stream("你好"))
    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}

def test_stream_usage_disabled_no_stream_options(monkeypatch):
    """USAGE_STREAM_USAGE=false：不请求流式 usage（兼容不支持的网关）。"""
    monkeypatch.setattr(settings, "USAGE_STREAM_USAGE", False)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_stream_sse(["好"]).encode(),
        )

    list(_llm(handler).stream("你好"))
    assert "stream_options" not in seen["body"]
    assert usage_tracker.pending_count() == 0


# ── 开关：不包装 ──


def test_tracking_disabled_no_wrapping(monkeypatch):
    """USAGE_TRACKING_ENABLED=false：工厂返回原生 ChatOpenAI（零额外行为）。"""
    monkeypatch.setattr(settings, "USAGE_TRACKING_ENABLED", False)
    llm = get_chat_llm()
    from langchain_openai import ChatOpenAI

    assert type(llm) is ChatOpenAI


# ── 端到端：deepagents 对话链路接线（真实 agent + mock LLM） ─────────────


def _pin_chat_session(monkeypatch):
    """隔离会话存储 + 记忆提取钩子 no-op（deepagents.chat 依赖，同主链路测试）。"""
    from app.services import chat_agent

    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


def test_deepagents_chat_records_dialogue_usage(monkeypatch):
    """端到端：deepagents 非流式对话（真实 agent + mock LLM）记账 chain=对话 + user。

    图内模型调用（非流式 _agenerate）经 wrapper 采集 usage；usage_context
    在 deepagents.chat 接线（user 透传）。
    """
    import asyncio

    import httpx

    from app.services.deepagents import agent as dg_agent

    _pin_chat_session(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_CHAT_COMPLETION)

    transport = httpx.MockTransport(handler)
    llm = get_chat_llm(
        max_tokens=64,
        timeout=30,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )
    asyncio.run(dg_agent.chat("你好", user="u-e2e", llm=llm))

    pending = usage_tracker._queue.get_nowait()
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-e2e"
    assert pending["model"] == "deepseek-v4-flash"
    assert pending["input_tokens"] == 10
    assert pending["output_tokens"] == 20


def test_deepagents_chat_stream_records_dialogue_usage(monkeypatch):
    """端到端：deepagents 流式对话（SSE 主链路）记账 chain=对话 + user。

    图内模型调用被流式化（_astream），usage 从 usage-only chunk 采集；
    usage_context 跨生成器 yield 持有（客户端中断也不泄漏上下文）。
    """
    import asyncio

    import httpx

    from app.services.deepagents import agent as dg_agent

    _pin_chat_session(monkeypatch)

    sse = _stream_sse(
        ["最终回答"],
        usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=sse.encode(),
        )

    transport = httpx.MockTransport(handler)
    llm = get_chat_llm(
        max_tokens=64,
        timeout=30,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )

    async def run():
        events = []
        async for event in dg_agent.chat_stream("你好", user="u-e2e-stream", llm=llm):
            events.append(event)
        return events

    events = asyncio.run(run())
    assert events[-1]["type"] == "done"
    assert "最终回答" in "".join(e.get("text", "") for e in events)

    pending = usage_tracker._queue.get_nowait()
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-e2e-stream"
    assert pending["input_tokens"] == 10
    assert pending["output_tokens"] == 20
