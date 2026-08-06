"""长期记忆检索注入（阶段2 T3：chat_agent 对话前注入）单测。

覆盖：
1. 登录用户首轮对话：检索到的记忆被注入 agent 上下文（含事实类型标签与时效标记）
2. 无记忆 / 游客：不注入（请求与阶段1 一致）
3. 检索异常：静默降级，对话正常进行、不抛错
4. 多轮会话仅首轮注入（次轮不重复检索/注入）
5. 时效过滤（MEMORY_MAX_AGE_DAYS 超龄记忆不注入）与总开关（MEMORY_INJECT_ENABLED=false）
6. 流式链路（chat_stream）同样注入
7. 端到端：真实 user_memory 写入后，登录用户首轮对话注入该记忆
"""

import asyncio
import json
import time

import httpx
import pytest

from app.core import rag
from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services import chat_agent, memory_service
from app.services.memory_service import MemoryEntry

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

# 注入上下文首行（识别记忆 SystemMessage 用）
_MEMORY_HEADER = "以下是该用户此前对话中表达过的已知信息"


@pytest.fixture(autouse=True)
def _isolate_memory_store(tmp_path, monkeypatch):
    """每个测试使用独立的 user_memory 持久化目录（避免污染真实 rag_data 与跨测试串扰）。"""
    monkeypatch.setattr(rag, "RAG_DATA_DIR", tmp_path)
    # 清空向量库进程内单例缓存（按 collection+dir 重建，保证拿到 tmp 目录的存储）
    with rag._vectorstore_lock:
        rag._vectorstores.clear()
    yield
    with rag._vectorstore_lock:
        rag._vectorstores.clear()


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    """固定 LLM 配置 + 记忆提取钩子 no-op + 清理 chat_agent 进程内会话。"""
    for key, value in {**PRIMARY, **FALLBACK}.items():
        monkeypatch.setattr(settings, key, value)
    # 记忆提取钩子固定为 no-op：避免后台任务用假 key 发起真实网络请求
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


# ── Mock 辅助（与 test_chat_agent.py 一致） ───────────────────────────────


def _completion(choices):
    return {
        "id": "chatcmpl-inj",
        "object": "chat.completion",
        "created": 1700000000,
        "model": PRIMARY["LLM_MODEL"],
        "choices": choices,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _text_choice(text: str):
    return {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": text},
    }


def _llm_with_mock(handler) -> "object":
    """构造注入 MockTransport 的 ChatOpenAI（同步/异步 client 均注入 mock）。"""
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def _patch_agent(monkeypatch, handler, *, use_rag: bool = False):
    """让 chat()/chat_stream() 使用注入 mock LLM 的 agent（绕过模块级缓存）。"""
    agent = chat_agent._build_agent(use_rag=use_rag, llm=_llm_with_mock(handler))
    monkeypatch.setattr(chat_agent, "_get_agent", lambda *, use_rag, llm=None: agent)
    return agent


def _memory_context_in(body) -> "str | None":
    """从请求体 messages 中提取记忆注入 SystemMessage（无则返回 None）。"""
    for m in body["messages"]:
        if m.get("role") == "system" and _MEMORY_HEADER in m.get("content", ""):
            return m["content"]
    return None


def _entry(content: str, fact_type: str = "fact", age_days: float = 1.0) -> MemoryEntry:
    """构造一条未过期的记忆条目（timestamp = 当前时间 - age_days 天）。"""
    return MemoryEntry(
        user="u1",
        fact_type=fact_type,
        content=content,
        timestamp=time.time() - age_days * 86400.0,
    )


# ── 注入逻辑（mock search_user_memory） ───────────────────────────────────


async def test_inject_first_turn_with_memory(monkeypatch):
    """登录用户首轮：检索到的记忆注入为 system 上下文（含类型标签与时效标记）。"""
    entries = [
        _entry("喜欢拍照", fact_type="preference", age_days=3),
        _entry("带老人小孩", fact_type="fact", age_days=15),
    ]

    async def fake_search(user, query, top_k=3):
        assert user == "u1"
        return entries

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("为您推荐拍照路线。")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    result = await chat_agent.chat("推荐适合拍照的路线", user="u1")

    assert result.answer == "为您推荐拍照路线。"
    ctx = _memory_context_in(bodies[0])
    assert ctx is not None
    # 内容、类型标签、时效标记齐全
    assert "喜欢拍照" in ctx
    assert "带老人小孩" in ctx
    assert "[偏好]" in ctx
    assert "[事实]" in ctx
    assert "（3天前）" in ctx
    assert "（15天前）" in ctx


async def test_inject_no_memory_no_change(monkeypatch):
    """无记忆（检索返回空）：请求与阶段1 一致，不注入任何上下文。"""
    searched = []

    async def fake_search(user, query, top_k=3):
        searched.append(query)
        return []

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("你好", user="u1")

    assert _memory_context_in(bodies[0]) is None
    assert searched == ["你好"]  # 仍以用户当前问题为查询检索了一次


async def test_inject_guest_skipped(monkeypatch):
    """游客对话：不触发检索、不注入任何记忆（行为与阶段1 一致）。"""
    async def fake_search(user, query, top_k=3):
        raise AssertionError("游客不应触发长期记忆检索")

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("你好", user="visitor")
    await chat_agent.chat("你好", user="guest-token123")

    for body in bodies:
        assert _memory_context_in(body) is None


async def test_inject_exception_silent_degrade(monkeypatch):
    """检索异常：静默降级，对话正常进行、不注入、不抛错。"""
    async def fake_search(user, query, top_k=3):
        raise RuntimeError("memory store boom")

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    result = await chat_agent.chat("你好", user="u1")

    assert result.answer == "答"
    assert _memory_context_in(bodies[0]) is None


async def test_inject_only_first_turn(monkeypatch):
    """多轮会话仅首轮注入：次轮（有历史）不再检索/注入。"""
    search_calls = []

    async def fake_search(user, query, top_k=3):
        search_calls.append(query)
        return [_entry("喜欢拍照", fact_type="preference", age_days=2)]

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        n = len(bodies)
        if n == 1:
            return httpx.Response(200, json=_completion([_text_choice("第一轮回答")]))
        return httpx.Response(200, json=_completion([_text_choice("第二轮回答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    r1 = await chat_agent.chat("第一问", user="u1")
    r2 = await chat_agent.chat("第二问", user="u1", conversation_id=r1.conversation_id)

    assert r2.answer == "第二轮回答"
    assert _memory_context_in(bodies[0]) is not None   # 首轮注入
    assert _memory_context_in(bodies[1]) is None       # 次轮不注入
    assert len(search_calls) == 1                      # 仅首轮检索一次


async def test_inject_filters_expired_memory(monkeypatch):
    """时效过滤：超过 MEMORY_MAX_AGE_DAYS 的陈旧记忆不注入。"""
    monkeypatch.setattr(settings, "MEMORY_MAX_AGE_DAYS", 30)
    entries = [
        _entry("喜欢拍照", fact_type="preference", age_days=3),     # 新鲜
        _entry("带老人小孩", fact_type="fact", age_days=90),        # 过期
    ]

    async def fake_search(user, query, top_k=3):
        return entries

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("推荐路线", user="u1")

    ctx = _memory_context_in(bodies[0])
    assert ctx is not None
    assert "喜欢拍照" in ctx
    assert "带老人小孩" not in ctx


async def test_inject_disabled_by_config(monkeypatch):
    """MEMORY_INJECT_ENABLED=false：总开关关闭，不检索、不注入。"""
    monkeypatch.setattr(settings, "MEMORY_INJECT_ENABLED", False)

    async def fake_search(user, query, top_k=3):
        raise AssertionError("注入总开关关闭时不应检索")

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("你好", user="u1")

    assert _memory_context_in(bodies[0]) is None


# ── 流式链路 ──────────────────────────────────────────────────────────────


def _sse(payload: str) -> str:
    return f"data: {payload}\n\n"


def _chunk(choices):
    return {
        "id": "chatcmpl-inj",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": PRIMARY["LLM_MODEL"],
        "choices": choices,
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


def _text_stream(text: str):
    """完整回答文本流：逐字增量 → finish(stop)。"""
    return [_text_delta(ch) for ch in text] + [_finish_delta("stop")]


async def test_inject_stream_first_turn(monkeypatch):
    """流式链路：首轮注入记忆上下文；次轮不注入。"""
    search_calls = []

    async def fake_search(user, query, top_k=3):
        search_calls.append(query)
        return [_entry("喜欢拍照", fact_type="preference", age_days=2)]

    monkeypatch.setattr(memory_service, "search_user_memory", fake_search)
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        n = len(bodies)
        if n == 1:
            return _stream_response(_text_stream("流式回答一"))
        return _stream_response(_text_stream("流式回答二"))

    _patch_agent(monkeypatch, handler, use_rag=False)
    events1 = []
    cid = ""
    async for event in chat_agent.chat_stream("第一问", user="u1"):
        events1.append(event)
        if event["type"] == "done":
            cid = event["conversation_id"]
    events2 = []
    async for event in chat_agent.chat_stream("第二问", user="u1", conversation_id=cid):
        events2.append(event)

    assert events1[-1]["type"] == "done"
    assert events2[-1]["type"] == "done"
    assert _memory_context_in(bodies[0]) is not None   # 首轮注入
    assert _memory_context_in(bodies[1]) is None       # 次轮不注入
    assert len(search_calls) == 1


# ── 端到端（真实 user_memory 向量库） ─────────────────────────────────────


async def test_inject_end_to_end_with_real_store(monkeypatch):
    """端到端：真实 user_memory 写入后，登录用户首轮对话注入该记忆（含时效标记）。"""
    await memory_service.save_user_memory("u1", [
        MemoryEntry(
            user="u1", fact_type="preference", content="喜欢拍照",
            timestamp=time.time() - 3 * 86400.0,
        ),
    ])
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("好的，为您推荐。")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    result = await chat_agent.chat("推荐适合拍照的路线", user="u1")

    assert result.answer == "好的，为您推荐。"
    ctx = _memory_context_in(bodies[0])
    assert ctx is not None
    assert "喜欢拍照" in ctx
    assert "[偏好]" in ctx
    assert "（3天前）" in ctx


async def test_inject_end_to_end_other_user_isolated(monkeypatch):
    """端到端：u1 的记忆不会被 u2 注入（按 user 隔离）。"""
    await memory_service.save_user_memory("u1", [
        MemoryEntry(
            user="u1", fact_type="preference", content="喜欢拍照",
            timestamp=time.time() - 86400.0,
        ),
    ])
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("推荐适合拍照的路线", user="u2")

    assert _memory_context_in(bodies[0]) is None
