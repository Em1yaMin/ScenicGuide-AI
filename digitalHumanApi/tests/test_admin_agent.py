"""管理员 agent 装配层（批次6 B4）单测。

覆盖：
1. chat：假 agent 收到 is_admin=True + 消息注入；返回 answer/conversation_id
   （空 conversation_id 生成新 uuid、非空透传回显）
2. chat_stream：SSE 契约事件序列（step → token → done）、工具调用去重、
   suppress_tokens 语义（工具调用消息内联 content 抑制）、done 携带
   conversation_id
3. agent 缓存：get_agent 默认 LLM 走缓存（同一实例）；注入 llm 独立构建；
   reset_agent_cache 清空

不依赖真实 LLM：注入假 agent（记录输入并产出构造好的消息流），
与 test_deepagents_main 同模式。
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from app.services.admin_agent import agent as admin_agent


@pytest.fixture(autouse=True)
def _reset_agent_cache():
    """每个测试重置 agent 缓存（防构造耗时与跨测试状态污染）。"""
    admin_agent.reset_agent_cache()
    yield
    admin_agent.reset_agent_cache()


# ── Mock 辅助 ─────────────────────────────────────────────────────────────


class _FakeAgent:
    """非流式假 agent：记录输入/config，返回固定消息列表。"""

    def __init__(self, answer="这是管理回复"):
        self.inputs = None
        self.config = None
        self.result_messages = [AIMessage(content=answer)]

    async def ainvoke(self, inputs, config=None):
        self.inputs = inputs
        self.config = config
        return {"messages": list(self.result_messages)}


class _FakeStreamAgent:
    """流式假 agent：记录输入/config，产出预设 chunk 流（消息模式）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.inputs = None
        self.config = None

    async def astream(self, inputs, stream_mode="messages", config=None):
        self.inputs = inputs
        self.config = config
        assert stream_mode == "messages"
        for chunk in self._chunks:
            yield chunk, {}


def _patch_agent(monkeypatch, agent):
    """注入假 agent（绕过模块级缓存 get_agent）。"""
    monkeypatch.setattr(
        admin_agent, "get_agent", lambda *, llm=None: agent
    )


def _text_chunk(text: str) -> AIMessageChunk:
    return AIMessageChunk(content=text)


# ── chat（非流式） ────────────────────────────────────────────────────────


async def test_chat_injects_admin_context_and_messages(monkeypatch):
    """chat：假 agent 收到 is_admin=True + 消息列表；recursion_limit 收紧。"""
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)
    result = await admin_agent.chat("查询知识库")
    assert result["answer"] == "这是管理回复"
    assert result["conversation_id"]
    # 图输入：messages + is_admin（工具 admin 上下文校验数据源）
    assert fake.inputs["is_admin"] is True
    msgs = fake.inputs["messages"]
    assert len(msgs) == 1
    assert msgs[0].type == "human"
    assert msgs[0].content == "查询知识库"
    # recursion_limit 显式收紧（与 deepagents 装配常量一致）
    assert fake.config["recursion_limit"] == admin_agent._MAX_AGENT_STEPS


async def test_chat_conversation_id_echo_and_generate(monkeypatch):
    """chat：非空 conversation_id 透传回显；空串生成新 uuid。"""
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)
    r1 = await admin_agent.chat("你好", conversation_id="admin-cid-1")
    assert r1["conversation_id"] == "admin-cid-1"
    r2 = await admin_agent.chat("你好")
    assert r2["conversation_id"]
    assert r2["conversation_id"] != "admin-cid-1"


async def test_chat_extracts_last_non_tool_ai_message(monkeypatch):
    """chat：回答提取最后一条非工具 AI 消息（工具链后最终作答）。"""
    fake = _FakeAgent()
    fake.result_messages = [
        AIMessage(content="", tool_calls=[{"name": "query_knowledge", "args": {}, "id": "c1"}]),
        AIMessage(content="最终回答内容"),
    ]
    _patch_agent(monkeypatch, fake)
    result = await admin_agent.chat("查一下知识库")
    assert result["answer"] == "最终回答内容"


# ── chat_stream（流式 SSE 契约） ─────────────────────────────────────────


async def test_chat_stream_event_sequence_step_token_done(monkeypatch):
    """chat_stream：step → token → done 事件序列，done 携带 conversation_id。"""
    chunks = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "c1", "name": "query_knowledge", "args": "{}", "index": 0}],
        ),
        ToolMessage(content='{"total": 1}', tool_call_id="c1", name="query_knowledge"),
        _text_chunk("查"),
        _text_chunk("询完成"),
    ]
    fake = _FakeStreamAgent(chunks)
    _patch_agent(monkeypatch, fake)
    events = [e async for e in admin_agent.chat_stream("查知识库", conversation_id="cid-s")]
    assert [e["type"] for e in events] == ["step", "token", "token", "done"]
    assert events[0] == {
        "type": "step",
        "tool": "query_knowledge",
        "label": "正在查询知识库…",
    }
    assert events[1]["text"] == "查"
    assert events[2]["text"] == "询完成"
    assert events[-1]["conversation_id"] == "cid-s"
    assert fake.inputs["is_admin"] is True


async def test_chat_stream_suppress_tokens_for_tool_message(monkeypatch):
    """chat_stream：工具调用消息的内联 content 抑制（suppress_tokens 语义）。"""
    chunks = [
        # 工具调用 chunk 内联了正文文本（A1 实测模型会输出碎文本）——必须抑制
        AIMessageChunk(
            content="正在查询知识库…",
            tool_call_chunks=[{"id": "c1", "name": "query_knowledge", "args": "{}", "index": 0}],
        ),
        AIMessageChunk(content="", tool_call_chunks=[{"id": "c1", "name": "query_knowledge", "args": "{}", "index": 1}]),
        ToolMessage(content='{"total": 1}', tool_call_id="c1", name="query_knowledge"),
        _text_chunk("回答"),
    ]
    fake = _FakeStreamAgent(chunks)
    _patch_agent(monkeypatch, fake)
    events = [e async for e in admin_agent.chat_stream("查一下")]
    texts = [e["text"] for e in events if e["type"] == "token"]
    assert texts == ["回答"]  # 内联碎文本未透出


async def test_chat_stream_step_dedup_by_call_id(monkeypatch):
    """chat_stream：同一工具调用拆分的多个 chunk 只发一次 step（按 call id 去重）。"""
    chunks = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "c1", "name": "query_knowledge", "args": "{}", "index": 0}],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "c1", "name": "query_knowledge", "args": "{}", "index": 1}],
        ),
        _text_chunk("完成"),
    ]
    fake = _FakeStreamAgent(chunks)
    _patch_agent(monkeypatch, fake)
    events = [e async for e in admin_agent.chat_stream("查")]
    steps = [e for e in events if e["type"] == "step"]
    assert len(steps) == 1
    assert steps[0]["tool"] == "query_knowledge"


async def test_chat_stream_unknown_tool_label_fallback(monkeypatch):
    """chat_stream：未知工具名使用兜底文案。"""
    chunks = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"id": "c9", "name": "unknown_tool", "args": "{}", "index": 0}],
        ),
        _text_chunk("好"),
    ]
    fake = _FakeStreamAgent(chunks)
    _patch_agent(monkeypatch, fake)
    events = [e async for e in admin_agent.chat_stream("x")]
    assert events[0]["label"] == "正在调用工具 unknown_tool…"


# ── agent 缓存 ────────────────────────────────────────────────────────────


async def test_get_agent_cached_and_reset(monkeypatch):
    """get_agent：默认 LLM 走模块级缓存（同一实例）；注入 llm 独立构建。"""
    fake_llm = object()
    calls = []

    def fake_build(llm=None):
        calls.append(llm)
        return object()

    monkeypatch.setattr(admin_agent, "_build_agent", fake_build)
    a1 = admin_agent.get_agent()
    a2 = admin_agent.get_agent()
    assert a1 is a2  # 缓存复用
    assert calls == [None]
    # 注入 llm：独立构建不缓存
    b1 = admin_agent.get_agent(llm=fake_llm)
    b2 = admin_agent.get_agent(llm=fake_llm)
    assert b1 is not b2
    # reset 后重建
    admin_agent.reset_agent_cache()
    a3 = admin_agent.get_agent()
    assert a3 is not a1
