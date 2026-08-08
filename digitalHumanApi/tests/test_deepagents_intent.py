"""deepagents 装配层（A2 试点 → A3 主链路）——意图分类双模式挂载 + 轻量摘要单测。

覆盖：
1. 意图分流：简单问答不注入规划指令；复杂任务注入 _PLAN_INSTRUCTION
   （prompt 级规划，A1 实测有效；分类失败默认简单 = 不注入）
2. 轻量用户摘要：游客/无数据不注入；昵称 + 记忆条数概览注入（防"忘记
   调用"记忆工具）；DB/计数异常静默降级
3. chat() 入口：分流结果透出（intent 字段）、messages 组装正确、
   user 传入图状态（A3 起含会话管理：conversation_id 生成/落盘/透出）
"""

import pytest
from langchain_core.messages import AIMessage

from app.services import chat_agent, memory_service
from app.services.deepagents import agent as dg_agent
from app.services.deepagents.agent import _PLAN_INSTRUCTION


@pytest.fixture(autouse=True)
def _pin_session_and_memory(monkeypatch):
    """隔离会话存储 + 记忆提取钩子 no-op（A3：chat() 接入会话管理与钩子）。

    - 清理进程内会话缓存，避免跨测试污染
    - 记忆提取钩子固定为 no-op：避免后台任务在测试环境触发真实 LLM 调用
    """
    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


# ── 意图分流（_build_messages） ───────────────────────────────────────────


def test_simple_intent_no_plan_instruction():
    """简单问答（单工具可答）：不注入规划指令，消息 = 当前问题。"""
    messages = dg_agent._build_messages("灵山几点开门？", complex_intent=False)
    assert len(messages) == 1
    assert messages[0].type == "human"
    assert "规划" not in messages[0].content


def test_complex_intent_injects_plan_instruction():
    """复杂任务（多步规划）：注入规划指令（prompt 级规划，双模式挂载）。"""
    messages = dg_agent._build_messages(
        "帮我规划灵山一日游，带老人小孩", complex_intent=True
    )
    assert len(messages) == 2
    assert messages[0].type == "system"
    assert _PLAN_INSTRUCTION in messages[0].content
    assert messages[1].type == "human"


def test_is_complex_intent_reuses_classifier():
    """意图判定复用现有规则分类器：复杂词命中 → True，简单 → False。"""
    assert dg_agent._is_complex_intent("帮我规划灵山一日游") is True
    assert dg_agent._is_complex_intent("你好") is False
    assert dg_agent._is_complex_intent("") is False
    # 纯偏好表达不判复杂（与 chat_agent 双模式词表约束一致）
    assert dg_agent._is_complex_intent("我喜欢拍照") is False


def test_messages_order_summary_before_plan():
    """消息顺序：摘要 → 规划指令 → 当前问题（DeepSeek 兼容多 system 消息）。"""
    messages = dg_agent._build_messages(
        "规划行程", complex_intent=True, light_summary="摘要文本"
    )
    assert messages[0].content == "摘要文本"
    assert _PLAN_INSTRUCTION in messages[1].content


# ── 轻量用户摘要 ──────────────────────────────────────────────────────────


async def test_light_summary_guest_empty(monkeypatch):
    """游客/空用户：不注入摘要。"""
    monkeypatch.setattr(dg_agent, "_fetch_nickname", _fake_nickname("小明"))
    assert await dg_agent._build_light_summary("visitor") == ""
    assert await dg_agent._build_light_summary("guest-abc") == ""
    assert await dg_agent._build_light_summary("") == ""


async def test_light_summary_with_nickname_and_count(monkeypatch):
    """登录用户：昵称 + 记忆条数概览注入（防'忘记调用'记忆工具）。"""
    monkeypatch.setattr(dg_agent, "_fetch_nickname", _fake_nickname("小明"))
    monkeypatch.setattr(memory_service, "count_user_memory", _fake_count(5))
    summary = await dg_agent._build_light_summary("1")
    assert "昵称：小明" in summary
    assert "长期记忆：5 条" in summary
    assert "recall_user_memory" in summary  # 引导模型调用记忆工具


async def test_light_summary_no_data_empty(monkeypatch):
    """无昵称且无记忆：不注入（避免空摘要噪声）。"""
    monkeypatch.setattr(dg_agent, "_fetch_nickname", _fake_nickname(""))
    monkeypatch.setattr(memory_service, "count_user_memory", _fake_count(0))
    assert await dg_agent._build_light_summary("1") == ""


async def test_light_summary_degrades_on_error(monkeypatch):
    """DB/计数异常：静默降级（不抛错、不阻断对话）。"""

    async def _boom_nickname(user):
        raise RuntimeError("db down")

    async def _boom_count(user):
        raise RuntimeError("store down")

    monkeypatch.setattr(dg_agent, "_fetch_nickname", _boom_nickname)
    monkeypatch.setattr(memory_service, "count_user_memory", _boom_count)
    assert await dg_agent._build_light_summary("1") == ""


def _fake_nickname(value: str):
    async def _inner(user: str) -> str:
        return value

    return _inner


def _fake_count(value: int):
    async def _inner(user: str) -> int:
        return value

    return _inner


# ── chat() 入口（分流 + user 传递） ───────────────────────────────────────


class _FakeAgent:
    """记录输入/config 并返回固定消息列表的假 agent（不触发真实 LLM）。"""

    def __init__(self):
        self.inputs = None
        self.config = None
        self.result_messages = [AIMessage(content="这是最终回答")]

    async def ainvoke(self, inputs, config=None):
        self.inputs = inputs
        self.config = config
        return {"messages": list(self.result_messages)}


async def test_chat_passes_user_and_intent(monkeypatch):
    """chat()：user 传入图状态、recursion_limit 显式收紧、intent 透出。"""
    fake = _FakeAgent()
    monkeypatch.setattr(
        dg_agent, "get_agent", lambda *, use_rag=True, llm=None: fake
    )
    # 复杂意图：messages 含规划指令
    result = await dg_agent.chat("帮我规划灵山一日游", user="user_7")
    assert result["answer"] == "这是最终回答"
    assert result["intent"] == "complex"
    # A3 会话管理：conversation_id 生成并透出（非空）
    assert result["conversation_id"]
    assert fake.inputs["user"] == "user_7"
    assert fake.config["recursion_limit"] == dg_agent._MAX_AGENT_STEPS
    msgs = fake.inputs["messages"]
    assert _PLAN_INSTRUCTION in msgs[0].content
    # 简单意图：不注入规划指令
    result2 = await dg_agent.chat("你好", user="user_7")
    assert result2["intent"] == "simple"
    msgs2 = fake.inputs["messages"]
    assert all("规划" not in m.content for m in msgs2)
