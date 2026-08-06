"""长期记忆服务（app/services/memory_service.py）单测。

覆盖：
1. 记忆条目 schema（MemoryEntry 字段/事实类型校验）
2. 写入/检索按 user 隔离（user_memory 命名空间，互不串扰）
3. 同事实覆盖更新（记忆不膨胀）与每用户条数上限淘汰
4. 游客（visitor / guest-*）不写长期记忆
5. LLM 提取（mock LLM）：JSON 解析/代码块剥离/非法输出容错/异常静默降级
6. 对话钩子：chat()/chat_stream() 完成后调度提取；游客跳过
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
from app.services.memory_service import (
    FACT_TYPES,
    MemoryEntry,
    extract_and_store,
    extract_facts,
    is_guest_user,
)

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
    """固定 LLM 配置 + 清理 chat_agent 进程内会话（与 test_chat_agent.py 一致）。"""
    for key, value in {**PRIMARY, **FALLBACK}.items():
        monkeypatch.setattr(settings, key, value)
    chat_agent.reset_agent_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._generated_conversation_ids.clear()
    yield
    chat_agent.reset_agent_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._generated_conversation_ids.clear()


# ── 记忆条目 schema ───────────────────────────────────────────────────────


def test_memory_entry_schema_and_fact_type_validation():
    """schema：字段齐全；非法 fact_type 归一为 fact；空内容剥离。"""
    entry = MemoryEntry(user="u1", fact_type="weird", content="  喜欢拍照  ")
    assert entry.fact_type == "fact"
    assert entry.content == "喜欢拍照"
    assert entry.timestamp > 0  # 未传时间戳时自动填充当前时间
    for ft in FACT_TYPES:
        assert MemoryEntry(fact_type=ft, content="x").fact_type == ft

    data = entry.to_dict()
    assert set(data) == {
        "user", "fact_type", "content", "source_conversation_id", "timestamp",
    }
    restored = MemoryEntry.from_dict(data)
    assert restored.user == "u1"
    assert restored.content == "喜欢拍照"


def test_is_guest_user():
    """游客判定：visitor / guest- 前缀 / 空串为游客；登录用户 id 不是。"""
    assert is_guest_user(None) is True
    assert is_guest_user("") is True
    assert is_guest_user("visitor") is True
    assert is_guest_user("guest-abc123") is True
    assert is_guest_user("guest") is False
    assert is_guest_user("1") is False
    assert is_guest_user("user-42") is False


# ── 写入 / 检索 / 覆盖 / 隔离 ─────────────────────────────────────────────


async def test_save_and_search_isolated_by_user():
    """按 user 隔离：u1 的记忆不会被 u2 检索到，count 各自独立。"""
    await memory_service.save_user_memory("u1", [
        MemoryEntry(user="u1", fact_type="preference", content="喜欢拍照", timestamp=100),
        MemoryEntry(user="u1", fact_type="fact", content="带老人小孩", timestamp=101),
    ])
    await memory_service.save_user_memory("u2", [
        MemoryEntry(user="u2", fact_type="preference", content="偏好半日游", timestamp=200),
    ])

    assert await memory_service.count_user_memory("u1") == 2
    assert await memory_service.count_user_memory("u2") == 1

    # 逐事实检索：u1 只命中自己的记忆（query 与事实有 token 重叠时）
    r1a = await memory_service.search_user_memory("u1", "喜欢拍照", top_k=5)
    assert {e.content for e in r1a} == {"喜欢拍照"}
    r1b = await memory_service.search_user_memory("u1", "带老人小孩", top_k=5)
    assert {e.content for e in r1b} == {"带老人小孩"}
    assert all(e.user == "u1" for e in r1a + r1b)
    assert all(e.fact_type in FACT_TYPES for e in r1a + r1b)

    # 隔离：u1 用 u2 的记忆句检索，命中不了 u2 的条目
    r2 = await memory_service.search_user_memory("u1", "偏好半日游", top_k=5)
    assert {e.content for e in r2} == set()

    # u2 只命中自己的记忆
    r3 = await memory_service.search_user_memory("u2", "偏好半日游", top_k=5)
    assert {e.content for e in r3} == {"偏好半日游"}
    assert all(e.user == "u2" for e in r3)


async def test_save_overwrites_same_fact():
    """同 user 同事实覆盖更新：重复写入不膨胀，timestamp/来源更新为最新。"""
    first = await memory_service.save_user_memory("u1", [
        MemoryEntry(user="u1", fact_type="preference", content="喜欢拍照",
                    source_conversation_id="c1", timestamp=100),
    ])
    assert first["written"] == 1
    assert first["total"] == 1

    second = await memory_service.save_user_memory("u1", [
        MemoryEntry(user="u1", fact_type="preference", content="喜欢拍照！",
                    source_conversation_id="c2", timestamp=200),
    ])
    assert second["written"] == 1
    assert second["overwritten"] == 1
    assert second["total"] == 1  # 覆盖而非追加

    assert await memory_service.count_user_memory("u1") == 1
    entries = await memory_service.search_user_memory("u1", "喜欢拍照", top_k=5)
    assert len(entries) == 1
    assert entries[0].content == "喜欢拍照！"
    assert entries[0].source_conversation_id == "c2"
    assert entries[0].timestamp == 200


async def test_save_guest_skipped():
    """游客不写长期记忆：visitor / guest-* 直接跳过，user_memory 保持为空。"""
    for user in ("visitor", "guest-token123", ""):
        result = await memory_service.save_user_memory(user, [
            MemoryEntry(user=user, content="喜欢拍照"),
        ])
        assert result["skipped"] is True
        assert result["written"] == 0
    # 存储中没有任何记忆（含游客条目）
    store = memory_service._memory_store()
    assert store.count() == 0


async def test_cap_evicts_oldest(monkeypatch):
    """每用户条数上限：超限淘汰 timestamp 最旧的条目（防记忆膨胀）。"""
    monkeypatch.setattr(settings, "MEMORY_MAX_PER_USER", 3)
    await memory_service.save_user_memory("u1", [
        MemoryEntry(user="u1", content=f"事实{i}", timestamp=float(i))
        for i in range(1, 6)
    ])
    assert await memory_service.count_user_memory("u1") == 3
    remaining = await memory_service.search_user_memory("u1", "事实", top_k=10)
    # 淘汰最早的两条（timestamp=1,2），保留 3/4/5
    assert {e.content for e in remaining} == {"事实3", "事实4", "事实5"}


# ── LLM 提取 ──────────────────────────────────────────────────────────────


class _FakeLLM:
    """最小 ChatOpenAI 替身：invoke 返回带 content 的对象或抛异常。"""

    def __init__(self, content=None, error=None):
        self._content = content
        self._error = error

    def invoke(self, *args, **kwargs):
        if self._error:
            raise self._error
        return type("R", (), {"content": self._content})()


def test_extract_facts_parses_json_with_fence():
    """LLM 输出带 markdown 围栏的 JSON 数组 → 正确解析，事实类型归一。"""
    llm = _FakeLLM(content=(
        "```json\n"
        '[{"fact_type": "preference", "content": "喜欢拍照"},'
        '{"fact_type": "fact", "content": "带老人小孩"},'
        '{"fact_type": "weird", "content": "坏类型"}]'
        "\n```"
    ))
    facts = extract_facts("我喜欢拍照，还要带老人小孩", "好的，为您推荐……", llm=llm)
    assert len(facts) == 3
    assert facts[0].fact_type == "preference"
    assert facts[0].content == "喜欢拍照"
    assert facts[1].fact_type == "fact"
    assert facts[2].fact_type == "fact"  # 非法类型归一


def test_extract_facts_invalid_output_returns_empty():
    """LLM 输出非 JSON / 无数组 / 空 → 返回空列表（静默降级）。"""
    for bad in ("", "随便说点啥", "```json\n{not an array}\n```", "[]"):
        facts = extract_facts("你好", "你好呀", llm=_FakeLLM(content=bad))
        assert facts == []


def test_extract_facts_llm_error_returns_empty():
    """LLM 调用抛异常 → 返回空列表（不抛错，静默降级）。"""
    facts = extract_facts("你好", "你好呀", llm=_FakeLLM(error=RuntimeError("boom")))
    assert facts == []


def test_extract_facts_skips_empty_query_or_answer():
    """空 query/answer 不调用 LLM。"""
    assert extract_facts("", "答", llm=_FakeLLM(content="[]")) == []
    assert extract_facts("问", "", llm=_FakeLLM(content="[]")) == []


# ── extract_and_store（钩子主体：异步 + 静默降级） ─────────────────────────


async def test_extract_and_store_writes_memory(monkeypatch):
    """钩子主体：LLM 提取到事实 → 写入 user_memory（按 user 隔离）。"""
    llm = _FakeLLM(content=json.dumps(
        [{"fact_type": "preference", "content": "喜欢拍照"}], ensure_ascii=False
    ))
    monkeypatch.setattr(memory_service, "get_chat_llm", lambda **kw: llm)

    result = await extract_and_store("u1", "cid-1", "我喜欢拍照", "推荐了拍照路线")
    assert result["written"] == 1
    assert result["skipped"] is False
    entries = await memory_service.search_user_memory("u1", "喜欢拍照")
    assert len(entries) == 1
    assert entries[0].content == "喜欢拍照"
    assert entries[0].source_conversation_id == "cid-1"
    assert entries[0].user == "u1"


async def test_extract_and_store_guest_skipped(monkeypatch):
    """钩子主体：游客不产生长期记忆（不调用 LLM）。"""
    called = {"llm": 0}

    def fake_llm(**kw):
        called["llm"] += 1
        raise AssertionError("游客不应触发 LLM 提取")

    monkeypatch.setattr(memory_service, "get_chat_llm", fake_llm)
    for user in ("visitor", "guest-abc"):
        result = await extract_and_store(user, "cid", "我喜欢拍照", "推荐")
        assert result["skipped"] is True
    assert called["llm"] == 0
    assert memory_service._memory_store().count() == 0


async def test_extract_and_store_silent_degrade(monkeypatch):
    """钩子主体：LLM 异常/解析失败 → 静默降级，不写记忆、不抛错。"""
    def fake_llm(**kw):
        raise RuntimeError("LLM 服务不可用")

    monkeypatch.setattr(memory_service, "get_chat_llm", fake_llm)
    result = await extract_and_store("u1", "cid", "我喜欢拍照", "推荐")
    assert result["written"] == 0
    assert result["skipped"] is True
    assert memory_service._memory_store().count() == 0


async def test_extract_and_store_disabled_by_config(monkeypatch):
    """MEMORY_ENABLED=false → 跳过提取（不调用 LLM）。"""
    monkeypatch.setattr(settings, "MEMORY_ENABLED", False)

    def fake_llm(**kw):
        raise AssertionError("配置关闭时不应调用 LLM")

    monkeypatch.setattr(memory_service, "get_chat_llm", fake_llm)
    result = await extract_and_store("u1", "cid", "我喜欢拍照", "推荐")
    assert result["skipped"] is True


# ── 对话钩子（chat / chat_stream 完成后调度） ─────────────────────────────


def _completion(choices):
    return {
        "id": "chatcmpl-mem",
        "object": "chat.completion",
        "created": 1700000000,
        "model": PRIMARY["LLM_MODEL"],
        "choices": choices,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _text_choice(text: str):
    return {
        "index": 0,
        "finish_reason": "stop",
        "message": {"role": "assistant", "content": text},
    }


def _sse(payload: str) -> str:
    return f"data: {payload}\n\n"


def _chunk(choices):
    return {
        "id": "chatcmpl-mem",
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
    events = [_text_delta(ch) for ch in text] + [_finish_delta("stop")]
    return events


def _mock_agent(handler, *, use_rag: bool):
    """用注入 MockTransport 的 mock LLM 构建 agent（与 test_chat_agent.py 一致）。"""
    transport = httpx.MockTransport(handler)
    llm = get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )
    return chat_agent._build_agent(use_rag=use_rag, llm=llm)


def _patch_agent(monkeypatch, handler, *, use_rag: bool = False):
    agent = _mock_agent(handler, use_rag=use_rag)
    monkeypatch.setattr(chat_agent, "_get_agent", lambda *, use_rag, llm=None: agent)
    return agent


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """等待后台任务满足谓词（尽力轮询，超时抛断言）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("后台记忆提取任务未在超时内执行")


async def test_chat_hook_schedules_extraction(monkeypatch):
    """非流式 chat() 完成后调度记忆提取（登录用户，传 user/cid/query/answer）。"""
    calls = []

    async def recorder(user, conversation_id, query, answer):
        calls.append((user, conversation_id, query, answer))

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", recorder)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion([_text_choice("这是回答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    result = await chat_agent.chat("我喜欢拍照", user="u1")
    await _wait_for(lambda: bool(calls))
    assert calls[0] == ("u1", result.conversation_id, "我喜欢拍照", "这是回答")


async def test_chat_hook_skips_guest(monkeypatch):
    """游客对话不调度记忆提取（visitor 直接跳过）。"""
    calls = []

    async def recorder(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", recorder)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion([_text_choice("答")]))

    _patch_agent(monkeypatch, handler, use_rag=False)
    await chat_agent.chat("你好", user="visitor")
    await asyncio.sleep(0.05)
    assert calls == []


async def test_chat_stream_hook_schedules_extraction(monkeypatch):
    """流式 chat_stream() done 事件后调度记忆提取。"""
    calls = []

    async def recorder(user, conversation_id, query, answer):
        calls.append((user, conversation_id, query, answer))

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", recorder)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body.get("stream"), "流式请求必须带 stream=true"
        return _stream_response(_text_stream("流式回答"))

    _patch_agent(monkeypatch, handler, use_rag=False)
    events = []
    async for event in chat_agent.chat_stream("我喜欢拍照", user="u1"):
        events.append(event)
    assert events[-1]["type"] == "done"
    await _wait_for(lambda: bool(calls))
    assert calls[0][0] == "u1"
    assert calls[0][2] == "我喜欢拍照"
    assert calls[0][3] == "流式回答"
