"""deepagents 装配层 A3 主链路切换——会话管理 + 记忆钩子 + 归属判定 单测。

A2 试点时装配层不管理会话；A3 起为对话主链路，本文件验证 A3 补齐的
会话能力（与 chat_agent 主链路行为一致，短期记忆/会话管理改造前后兼容）：

1. 会话管理（非流式）：conversation_id 生成与透出、SQLite 落盘、
   多轮上下文组装（第二轮请求体携带首轮历史）
2. 重启恢复：清空进程内状态 + 同文件重建存储层后，同 id 续聊恢复历史
   （短期记忆持久化行为与改造前一致）；归属判定随 source 恢复
   （has_conversation=True，回落 Dify 时传空串）
3. 记忆钩子：chat()/chat_stream() 完成后调度长期记忆提取（A2 已有能力，
   A3 确认与主链路钩子接线：user/cid/query/answer 透传；游客跳过）
4. 流式：done 事件携带 conversation_id；历史在 done 前落盘；
   次轮续聊恢复上下文
5. 跨模块集成：装配层生成的会话 id 登记在 chat_agent 归属集合
   （路由层回落 Dify 的会话 ID 移交判定无需改动）

不依赖真实 LLM：注入假 agent（记录输入并产出构造好的消息流）。
"""

import asyncio
import time

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from app.core import memory_store
from app.services import chat_agent
from app.services.deepagents import agent as dg_agent


@pytest.fixture(autouse=True)
def _pin_session_and_memory(monkeypatch):
    """隔离会话存储 + 记忆提取钩子 no-op（默认），清理进程内缓存。"""
    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


# ── Mock 辅助 ─────────────────────────────────────────────────────────────


class _FakeAgent:
    """非流式假 agent：记录输入/config，返回固定消息列表。"""

    def __init__(self, answer="这是最终回答"):
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
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: agent)


def _text_chunk(text: str) -> AIMessageChunk:
    return AIMessageChunk(content=text)


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """等待后台任务满足谓词（尽力轮询，超时抛断言）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("后台记忆提取任务未在超时内执行")


def _messages_input(fake) -> list:
    """agent 收到的 messages 列表（含会话历史 + 当前问题）。"""
    return fake.inputs["messages"]


# ── 会话管理（非流式） ────────────────────────────────────────────────────


async def test_chat_generates_and_persists_conversation_id(monkeypatch):
    """空 conversation_id → 生成新 id 并透出；SQLite 落盘（短期记忆持久化）。"""
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)
    result = await dg_agent.chat("你好", user="u1")
    assert result["conversation_id"]
    # 会话已落盘：SQLite 中可查（进程内缓存命中即可，重启恢复另测）
    session = memory_store.get_store().load_session(result["conversation_id"])
    assert session is not None
    assert len(session["messages"]) == 2  # human + ai
    assert session["user"] == "u1"


async def test_chat_multi_turn_history_assembled(monkeypatch):
    """同 conversation_id 第二轮：历史组装进 messages（多轮上下文）。"""
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)
    r1 = await dg_agent.chat("第一问", user="u1")
    cid = r1["conversation_id"]
    assert len(_messages_input(fake)) == 1  # 首轮仅当前问题

    await dg_agent.chat("第二问", user="u1", conversation_id=cid)
    msgs = _messages_input(fake)
    assert [m.type for m in msgs] == ["human", "ai", "human"]
    assert msgs[0].content == "第一问"
    assert msgs[1].content == "这是最终回答"
    assert msgs[2].content == "第二问"


async def test_chat_restart_recovers_history(monkeypatch, tmp_path):
    """模拟重启：清空进程内状态 + 同文件重建存储层后，同 id 续聊恢复历史。"""
    db_path = str(tmp_path / "memory.db")
    memory_store.reset_store(db_path=db_path)

    fake1 = _FakeAgent(answer="第一轮回答")
    _patch_agent(monkeypatch, fake1)
    r1 = await dg_agent.chat("第一问", user="u1")
    cid = r1["conversation_id"]

    # 模拟重启：进程内状态清空，存储层用同一文件重建
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()
    memory_store.reset_store(db_path=db_path)

    fake2 = _FakeAgent(answer="第二轮回答")
    _patch_agent(monkeypatch, fake2)
    r2 = await dg_agent.chat("第二问", user="u1", conversation_id=cid)
    assert r2["conversation_id"] == cid
    # 第二轮 messages 携带第一轮历史（human/ai/human）
    msgs = _messages_input(fake2)
    assert [m.type for m in msgs] == ["human", "ai", "human"]
    assert msgs[0].content == "第一问"
    # SQLite 侧累计两轮问答（4 条消息）
    session = memory_store.get_store().load_session(cid)
    assert session is not None
    assert len(session["messages"]) == 4
    # 归属判定随 source 恢复：重启后该 id 仍登记为装配层生成（回落传空串）
    assert chat_agent.has_conversation(cid) is True


async def test_chat_appends_history_and_schedules_memory_hook(monkeypatch):
    """对话完成后：历史落盘 + 长期记忆提取钩子调度（user/cid/query/answer）。"""
    calls = []

    async def recorder(user, conversation_id, query, answer):
        calls.append((user, conversation_id, query, answer))

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", recorder)
    fake = _FakeAgent(answer="钩子回答")
    _patch_agent(monkeypatch, fake)
    result = await dg_agent.chat("我喜欢拍照", user="u1")
    await _wait_for(lambda: bool(calls))
    assert calls[0] == ("u1", result["conversation_id"], "我喜欢拍照", "钩子回答")


async def test_chat_memory_hook_skips_guest(monkeypatch):
    """游客对话不调度记忆提取（与主链路一致）。"""
    calls = []

    async def recorder(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", recorder)
    _patch_agent(monkeypatch, _FakeAgent())
    await dg_agent.chat("你好", user="visitor")
    await asyncio.sleep(0.05)
    assert calls == []


# ── 会话管理（流式） ──────────────────────────────────────────────────────


async def test_chat_stream_done_carries_cid_and_persists(monkeypatch):
    """流式：done 事件携带生成的 conversation_id；历史在 done 前落盘。"""
    fake = _FakeStreamAgent([_text_chunk("流式"), _text_chunk("回答")])
    _patch_agent(monkeypatch, fake)
    events = [e async for e in dg_agent.chat_stream("你好", user="u1")]
    done = events[-1]
    assert done["type"] == "done"
    assert done["conversation_id"]
    # 历史已落盘（done 前追加，客户端收到 done 即关闭也不丢上下文）
    session = memory_store.get_store().load_session(done["conversation_id"])
    assert session is not None
    assert len(session["messages"]) == 2
    assert session["messages"][0]["role"] == "human"
    assert session["messages"][0]["content"] == "你好"
    assert session["messages"][1]["role"] == "ai"
    assert session["messages"][1]["content"] == "流式回答"


async def test_chat_stream_multi_turn_history(monkeypatch):
    """流式次轮续聊：同 conversation_id 恢复历史并组装进 messages。"""
    fake1 = _FakeStreamAgent([_text_chunk("首轮回答")])
    _patch_agent(monkeypatch, fake1)
    events1 = [e async for e in dg_agent.chat_stream("第一问", user="u1")]
    cid = events1[-1]["conversation_id"]

    fake2 = _FakeStreamAgent([_text_chunk("次轮回答")])
    _patch_agent(monkeypatch, fake2)
    events2 = [e async for e in dg_agent.chat_stream("第二问", user="u1", conversation_id=cid)]
    assert events2[-1]["conversation_id"] == cid
    msgs = _messages_input(fake2)
    assert [m.type for m in msgs] == ["human", "ai", "human"]
    assert msgs[0].content == "第一问"


async def test_chat_stream_schedules_memory_hook(monkeypatch):
    """流式 done 事件后调度记忆提取（与 chat_agent 主链路一致）。"""
    calls = []

    async def recorder(user, conversation_id, query, answer):
        calls.append((user, conversation_id, query, answer))

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", recorder)
    fake = _FakeStreamAgent([_text_chunk("流式回答")])
    _patch_agent(monkeypatch, fake)
    events = [e async for e in dg_agent.chat_stream("我喜欢拍照", user="u1")]
    cid = events[-1]["conversation_id"]
    await _wait_for(lambda: bool(calls))
    assert calls[0] == ("u1", cid, "我喜欢拍照", "流式回答")


# ── 跨模块集成（路由回落归属判定） ────────────────────────────────────────


async def test_generated_cid_registered_for_fallback(monkeypatch):
    """装配层生成的会话 id 登记在 chat_agent 归属集合：
    路由层回落 Dify 时 has_conversation 判定（传空串）无需改动。"""
    _patch_agent(monkeypatch, _FakeAgent())
    result = await dg_agent.chat("你好", user="u1")
    cid = result["conversation_id"]
    assert chat_agent.has_conversation(cid) is True
    # 客户端传入的 id（Dify 生成）复用建会不登记：回落原样透传
    reused = await dg_agent.chat("你好", user="u1", conversation_id="dify-cid")
    assert reused["conversation_id"] == "dify-cid"
    assert chat_agent.has_conversation("dify-cid") is False
