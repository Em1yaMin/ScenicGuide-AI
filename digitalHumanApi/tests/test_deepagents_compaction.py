"""会话历史上下文压缩（app/services/deepagents/compaction.py + agent.py 集成）单测。

覆盖：
1. 压缩引擎（maybe_compact）：未达阈值不压缩（LLM 零调用）/达阈值压缩为
   摘要 + 保留窗口/摘要 prompt 要求保留关键约束（带老人小孩类）/LLM 失败
   静默降级/开关关闭跳过/摘要缺失异常形态防御
2. 会话集成（回答后触发）：chat()/chat_stream() 跨过阈值后会话历史首条
   变为摘要 SystemMessage，后续轮次消息携带摘要；未达阈值不重复压缩
3. 持久化：压缩结果写 SQLite（system 角色），重启后懒恢复还原摘要
4. 并发安全：快照后并发追加新轮次时放弃写回（不覆盖新消息）
5. memory_store.replace_messages 存储层行为

不依赖真实 LLM：注入假摘要 LLM（记录调用、返回固定摘要文本），
对话 agent 注入假 agent（记录输入）。
"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from app.core import memory_store
from app.core.config import settings
from app.services import chat_agent
from app.services.deepagents import agent as dg_agent
from app.services.deepagents import compaction


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


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeSummaryLLM:
    """假摘要 LLM：记录调用，返回固定摘要文本。

    需模拟 SummarizationMiddleware 依赖的模型接口：`_llm_type` 属性
    （token 计数估算用）与异步 `ainvoke(prompt, config=...)`（返回带
    `.text` 的响应对象）。
    """

    def __init__(self, summary_text: str = "游客带老人小孩出行，喜欢拍照，半日游"):
        self._text = summary_text
        self._llm_type = "openai-chat"
        self.calls = []

    async def ainvoke(self, prompt: str, config=None):
        self.calls.append(prompt)
        return _FakeResponse(self._text)


class _FakeAgent:
    """非流式假 agent：记录输入，返回固定回答。"""

    def __init__(self, answer="这是最终回答"):
        self.inputs = None
        self.config = None
        self.result_messages = [AIMessage(content=answer)]

    async def ainvoke(self, inputs, config=None):
        self.inputs = inputs
        self.config = config
        return {"messages": list(self.result_messages)}


class _FakeStreamAgent:
    """流式假 agent：记录输入，产出预设 chunk 流。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.inputs = None

    async def astream(self, inputs, stream_mode="messages", config=None):
        self.inputs = inputs
        assert stream_mode == "messages"
        for chunk in self._chunks:
            yield chunk, {}


def _patch_agent(monkeypatch, agent):
    """注入假 agent（绕过模块级缓存 get_agent）。"""
    monkeypatch.setattr(dg_agent, "get_agent", lambda *, use_rag=True, llm=None: agent)


def _small_thresholds(monkeypatch, trigger=6, keep=4):
    """压缩阈值调小（测试快速跨阈值，默认 24 条 ≈ 12 轮）。"""
    monkeypatch.setattr(settings, "AGENT_COMPACT_TRIGGER_MESSAGES", trigger)
    monkeypatch.setattr(settings, "AGENT_COMPACT_KEEP_MESSAGES", keep)


# ── 压缩引擎（maybe_compact） ─────────────────────────────────────────────


def _history(n_rounds: int, prefix: str = "轮") -> list:
    """构造 n 轮 Q&A 历史（2 条/轮）。"""
    history = []
    for i in range(n_rounds):
        history.append(HumanMessage(content=f"{prefix}问{i}"))
        history.append(AIMessage(content=f"{prefix}答{i}"))
    return history


async def test_below_threshold_no_compaction():
    """未达阈值：不压缩、LLM 零调用。"""
    fake_llm = _FakeSummaryLLM()
    result = await compaction.maybe_compact(_history(5), fake_llm)  # 10 条 < 24
    assert result is None
    assert fake_llm.calls == []


async def test_compaction_produces_summary_and_keeps_recent():
    """达阈值：压缩早期轮次为摘要 SystemMessage，保留最近 N 条完整消息。"""
    fake_llm = _FakeSummaryLLM("游客带老人小孩出行，喜欢拍照，半日游")
    history = _history(13)  # 26 条 ≥ 24
    result = await compaction.maybe_compact(history, fake_llm)
    assert result is not None
    # 首条为摘要 SystemMessage（中文包装 + 摘要正文，关键约束不丢）
    first = result[0]
    assert first.type == "system"
    assert "带老人小孩" in first.content
    assert "拍照" in first.content
    # 保留最近 10 条（5 轮）完整消息
    assert len(result) == 1 + 10
    assert [m.content for m in result[1:]] == [m.content for m in history[-10:]]
    assert len(fake_llm.calls) == 1


async def test_summary_prompt_requires_key_constraints():
    """摘要 prompt 显式要求保留关键约束（人群/时间/预算/兴趣等）。"""
    prompt = compaction._SUMMARY_PROMPT
    assert "{messages}" in prompt
    assert "带老人" in prompt
    assert "带小孩" in prompt
    assert "预算" in prompt
    assert "拍照" in prompt


async def test_llm_failure_skips_compaction():
    """LLM 调用失败：跳过压缩（返回 None，历史原样），不抛异常。"""
    class _BrokenLLM(_FakeSummaryLLM):
        async def ainvoke(self, prompt, config=None):
            raise RuntimeError("summary 服务不可用")

    history = _history(13)
    result = await compaction.maybe_compact(history, _BrokenLLM())
    assert result is None


async def test_disabled_skips_compaction(monkeypatch):
    """开关关闭：不压缩（LLM 零调用）。"""
    monkeypatch.setattr(settings, "AGENT_COMPACT_ENABLED", False)
    fake_llm = _FakeSummaryLLM()
    result = await compaction.maybe_compact(_history(13), fake_llm)
    assert result is None
    assert fake_llm.calls == []


async def test_abnormal_summary_output_skips(monkeypatch):
    """摘要缺失的异常形态（中间件返回空）防御：跳过压缩。"""
    fake_llm = _FakeSummaryLLM("   ")
    history = _history(13)
    result = await compaction.maybe_compact(history, fake_llm)
    assert result is None


# ── 会话集成（回答后触发） ────────────────────────────────────────────────


async def test_chat_compacts_after_threshold(monkeypatch):
    """chat() 回答后触发：跨过阈值轮次后会话历史首条为摘要 SystemMessage，
    后续轮次模型输入携带摘要；未达阈值不重复压缩（LLM 调用次数 = 触发次数）。"""
    _small_thresholds(monkeypatch, trigger=6, keep=4)
    fake_llm = _FakeSummaryLLM("游客带老人小孩出行")
    _patch_agent(monkeypatch, _FakeAgent())

    r1 = await dg_agent.chat("问1", user="u1", llm=fake_llm)
    cid = r1["conversation_id"]
    for i in range(2, 7):
        await dg_agent.chat(f"问{i}", user="u1", conversation_id=cid, llm=fake_llm)

    with chat_agent._conversations_lock:
        history = list(chat_agent._conversations[cid])
    # 6 轮共 12 条 → 触发 3 次压缩（第 3/4/5/6 轮后达阈值），最终 = 摘要 + 4 条
    assert history[0].type == "system"
    assert "带老人小孩" in history[0].content
    assert len(history) == 1 + 4
    # 第 3 轮后开始压缩：共触发 4 次（每轮达阈值即压一次，无重复压缩）
    assert len(fake_llm.calls) == 4


async def test_chat_next_turn_includes_summary(monkeypatch):
    """压缩后下一轮模型输入包含摘要 SystemMessage（上下文连续性）。"""
    _small_thresholds(monkeypatch, trigger=6, keep=4)
    fake_llm = _FakeSummaryLLM("游客带老人小孩出行")
    fake = _FakeAgent()
    _patch_agent(monkeypatch, fake)

    r1 = await dg_agent.chat("问1", user="u1", llm=fake_llm)
    cid = r1["conversation_id"]
    await dg_agent.chat("问2", user="u1", conversation_id=cid, llm=fake_llm)
    await dg_agent.chat("问3", user="u1", conversation_id=cid, llm=fake_llm)
    await dg_agent.chat("问4", user="u1", conversation_id=cid, llm=fake_llm)
    msgs = fake.inputs["messages"]
    # 第 4 轮输入：摘要 system + 保留轮次 + 当前问题
    assert msgs[0].type == "system"
    assert "带老人小孩" in msgs[0].content
    assert msgs[-1].type == "human"
    assert msgs[-1].content == "问4"


async def test_chat_stream_compacts_and_done_unchanged(monkeypatch):
    """chat_stream() 回答后触发压缩；done 事件仍为最后一个事件、契约不变。"""
    _small_thresholds(monkeypatch, trigger=6, keep=4)
    fake_llm = _FakeSummaryLLM("游客带老人小孩出行")
    _patch_agent(monkeypatch, _FakeStreamAgent([AIMessageChunk(content="流式回答")]))

    events = []
    async for event in dg_agent.chat_stream("问1", user="u1", llm=fake_llm):
        events.append(event)
    cid = events[-1]["conversation_id"]
    for i in range(2, 5):
        async for event in dg_agent.chat_stream(
            f"问{i}", user="u1", conversation_id=cid, llm=fake_llm
        ):
            events.append(event)
    assert events[-1]["type"] == "done"
    assert events[-1]["conversation_id"] == cid
    # done 契约字段不变
    assert set(events[-1].keys()) == {
        "type", "conversation_id", "rag_context", "rag_sources", "intent",
    }
    with chat_agent._conversations_lock:
        history = list(chat_agent._conversations[cid])
    assert history[0].type == "system"
    assert "带老人小孩" in history[0].content


async def test_compaction_persists_and_survives_restart(monkeypatch, tmp_path):
    """压缩结果 SQLite 落盘（system 角色）；模拟重启懒恢复后摘要仍在。"""
    _small_thresholds(monkeypatch, trigger=6, keep=4)
    db_path = str(tmp_path / "memory.db")
    memory_store.reset_store(db_path=db_path)
    fake_llm = _FakeSummaryLLM("游客带老人小孩出行")
    _patch_agent(monkeypatch, _FakeAgent())

    r1 = await dg_agent.chat("问1", user="u1", llm=fake_llm)
    cid = r1["conversation_id"]
    for i in range(2, 4):
        await dg_agent.chat(f"问{i}", user="u1", conversation_id=cid, llm=fake_llm)

    # 库中已含 system 角色摘要消息
    session = memory_store.get_store().load_session(cid)
    assert session is not None
    roles = [m["role"] for m in session["messages"]]
    assert roles[0] == "system"
    assert "带老人小孩" in session["messages"][0]["content"]

    # 模拟重启：进程内状态清空 + 同文件重建存储层
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()
    memory_store.reset_store(db_path=db_path)

    fake2 = _FakeAgent()
    _patch_agent(monkeypatch, fake2)
    await dg_agent.chat("问4", user="u1", conversation_id=cid, llm=fake_llm)
    # 重启后模型输入恢复出摘要 SystemMessage（_deserialize_history 支持 system）
    msgs = fake2.inputs["messages"]
    assert msgs[0].type == "system"
    assert "带老人小孩" in msgs[0].content


async def test_concurrent_append_skips_writeback(monkeypatch):
    """并发安全：压缩计算期间有新轮次追加 → 放弃写回（不覆盖新消息）。"""
    _small_thresholds(monkeypatch, trigger=6, keep=4)
    cid = chat_agent._get_or_create_conversation("", "u1")
    for i in range(3):
        chat_agent._append_history(cid, f"问{i}", f"答{i}", "u1")  # 6 条达阈值

    # 压缩计算返回前，并发的另一轮对话完成追加（快照后、写回前）
    async def _racing_compact(history, llm):
        with chat_agent._conversations_lock:
            chat_agent._conversations[cid].append(HumanMessage(content="并发问"))
            chat_agent._conversations[cid].append(AIMessage(content="并发答"))
        return [
            SystemMessage(content="（会话早期轮次摘要）：并发摘要"),
            HumanMessage(content="保留问"),
        ]

    monkeypatch.setattr(compaction, "maybe_compact", _racing_compact)
    await dg_agent._maybe_compact_history(cid, llm=None)
    with chat_agent._conversations_lock:
        history = list(chat_agent._conversations[cid])
    # 写回被放弃：新轮次保留，未被压缩结果覆盖
    assert history[-1].content == "并发答"
    assert history[-2].content == "并发问"
    assert history[0].type == "human"


# ── memory_store.replace_messages（存储层） ───────────────────────────────


def test_replace_messages_store_behavior():
    """replace_messages：整体替换（含 system 角色）、不刷新 TTL、会话缺失返回 False。"""
    store = memory_store.get_store()
    store.create_session("c1", user="u1", source=memory_store.SOURCE_CLIENT, now=1000.0)
    store.append_messages("c1", [("human", "q"), ("ai", "a")], max_messages=20, now=1000.0)
    before = store.load_session("c1", now=1000.0)
    ttl_before = before["ttl_expires_at"]

    assert store.replace_messages(
        "c1",
        [("system", "摘要"), ("human", "q2"), ("ai", "a2")],
        now=1500.0,
    ) is True
    after = store.load_session("c1", now=1500.0)
    assert [m["role"] for m in after["messages"]] == ["system", "human", "ai"]
    assert after["messages"][0]["content"] == "摘要"
    # TTL 不被刷新（压缩只是改写内容，不构成活跃行为）
    assert after["ttl_expires_at"] == ttl_before

    # 会话不存在 → False
    assert store.replace_messages("ghost", [("human", "x")], now=1500.0) is False
