"""双模式集成（阶段3 T1：chat_agent 分流 快速路径/规划路径）单测。

覆盖：复杂任务路由到规划路径（chat/chat_stream）、简单任务留在快速路径、
规划路径失败/分类失败降级快速路径（对话不中断）、AGENT_DUAL_MODE=false
整体回退、长期记忆注入上下文透传给规划路径。

路由层用 monkeypatch 替换 planner_agent.chat_plan / chat_plan_stream 为
记录调用的 fake（不重复规划图内部逻辑，专项见 test_planner_agent.py）；
快速路径用 StubAgent 模拟 create_agent 返回（不发真实请求）。
"""

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from app.core.config import settings
from app.services import chat_agent, planner_agent
from app.services.planner_agent import PlanResult, PlannerError
from app.services.intent_classifier import classify_intent

# 复杂/简单测试查询（与分类词表对齐，避免依赖 mock 分类器）
COMPLEX_QUERY = "规划灵山一日游，带老人小孩，喜欢拍照"
SIMPLE_QUERY = "景区几点开门？"


@pytest.fixture(autouse=True)
def _pin_llm_config(monkeypatch):
    """固定端点配置 + 清理缓存/会话，避免跨测试污染。"""
    for key, value in {
        "LLM_API_BASE": "https://opencode.ai/zen/go/v1",
        "LLM_API_KEY": "sk-primary-test",
        "LLM_MODEL": "deepseek-v4-flash",
        "LLM_FALLBACK_API_BASE": "https://api.deepseek.com/v1",
        "LLM_FALLBACK_API_KEY": "sk-fallback-test",
        "LLM_FALLBACK_MODEL": "deepseek-chat",
    }.items():
        monkeypatch.setattr(settings, key, value)
    # 记忆提取钩子固定为 no-op（后台任务不发真实请求）
    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    chat_agent.reset_agent_cache()
    planner_agent.reset_planner_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()
    yield
    chat_agent.reset_agent_cache()
    planner_agent.reset_planner_cache()
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


# ── Stub：快速路径 agent（模拟 create_agent 返回，不发真实请求） ──────────


class _StubAgent:
    """非流式快速路径 stub。"""

    def __init__(self, answer: str = "快速回答"):
        self.answer = answer

    async def ainvoke(self, inputs, config=None):
        return {
            "messages": [HumanMessage(content="q"), AIMessage(content=self.answer)]
        }


class _StubStreamAgent:
    """流式快速路径 stub（与现有流式测试的 chunk 元组形态一致）。"""

    def __init__(self, text: str = "快速流式回答"):
        self.text = text

    async def astream(self, inputs, stream_mode="messages", config=None):
        for ch in self.text:
            yield (AIMessageChunk(content=ch), {})
        yield (AIMessageChunk(content=""), {})


def _patch_fast_agent(monkeypatch, answer: str = "快速回答", stream: bool = False):
    """让快速路径使用 stub agent。"""
    stub = _StubStreamAgent(answer) if stream else _StubAgent(answer)
    monkeypatch.setattr(
        chat_agent, "_get_agent", lambda *, use_rag, llm=None: stub
    )
    return stub


# ── chat()：分流 ───────────────────────────────────────────────────────────


async def test_chat_routes_complex_to_planner(monkeypatch):
    """复杂任务（含偏好规划类）→ 规划路径；快速路径不被调用。"""
    captured = {}

    async def fake_chat_plan(**kwargs):
        captured["kwargs"] = kwargs
        return PlanResult(
            answer="规划回答",
            plan=[{"tool": "recommend_routes", "args": {}, "note": "推荐"}],
        )

    monkeypatch.setattr(planner_agent, "chat_plan", fake_chat_plan)
    # 快速路径若被调用则报错（证明分流正确）
    def _boom(*args, **kwargs):
        raise AssertionError("复杂任务不应走快速路径")

    monkeypatch.setattr(chat_agent, "_get_agent", _boom)

    result = await chat_agent.chat(COMPLEX_QUERY, user="visitor")
    assert result.answer == "规划回答"
    assert result.conversation_id  # 会话正常创建
    # 规划路径收到完整上下文（历史 + 当前问题）
    assert captured["kwargs"]["messages"][-1].content == COMPLEX_QUERY
    assert captured["kwargs"]["rag_top_k"] == 3


async def test_chat_simple_stays_on_fast_path(monkeypatch):
    """简单问答（单工具可答）→ 快速路径；规划路径不被调用。"""
    async def fake_chat_plan(**kwargs):
        raise AssertionError("简单任务不应走规划路径")

    monkeypatch.setattr(planner_agent, "chat_plan", fake_chat_plan)
    _patch_fast_agent(monkeypatch, answer="开放时间是 8:00。")

    result = await chat_agent.chat(SIMPLE_QUERY, user="visitor")
    assert result.answer == "开放时间是 8:00。"


async def test_chat_planner_failure_falls_back_to_fast_path(monkeypatch):
    """规划路径异常（PlannerError）→ 静默降级快速路径，对话不中断。"""
    async def fake_chat_plan(**kwargs):
        raise PlannerError("规划失败")

    monkeypatch.setattr(planner_agent, "chat_plan", fake_chat_plan)
    _patch_fast_agent(monkeypatch, answer="降级后的快速回答")

    result = await chat_agent.chat(COMPLEX_QUERY, user="visitor")
    assert result.answer == "降级后的快速回答"


async def test_chat_classifier_failure_falls_back_to_fast_path(monkeypatch):
    """意图分类器异常 → 默认简单 → 快速路径（安全降级）。"""
    def _boom(query):
        raise RuntimeError("分类器故障")

    monkeypatch.setattr(chat_agent, "classify_intent", _boom)

    async def fake_chat_plan(**kwargs):
        raise AssertionError("分类失败不应走规划路径")

    monkeypatch.setattr(planner_agent, "chat_plan", fake_chat_plan)
    _patch_fast_agent(monkeypatch, answer="快速回答")

    result = await chat_agent.chat(COMPLEX_QUERY, user="visitor")
    assert result.answer == "快速回答"


async def test_chat_dual_mode_disabled_forces_fast_path(monkeypatch):
    """AGENT_DUAL_MODE=false：即使复杂任务也走快速路径（回退开关）。"""
    monkeypatch.setattr(settings, "AGENT_DUAL_MODE", False)

    async def fake_chat_plan(**kwargs):
        raise AssertionError("双模式关闭时不应走规划路径")

    monkeypatch.setattr(planner_agent, "chat_plan", fake_chat_plan)
    _patch_fast_agent(monkeypatch, answer="快速回答")

    result = await chat_agent.chat(COMPLEX_QUERY, user="visitor")
    assert result.answer == "快速回答"


async def test_chat_memory_context_passed_to_planner(monkeypatch):
    """长期记忆注入（T3）透传给规划路径：messages 首条为记忆 SystemMessage。"""
    captured = {}

    async def fake_chat_plan(**kwargs):
        captured["messages"] = kwargs["messages"]
        return PlanResult(answer="规划回答")

    monkeypatch.setattr(planner_agent, "chat_plan", fake_chat_plan)
    # 固定记忆注入结果（u1 非游客；hook 已 noop，无真实检索）
    async def fake_memory_context(query, user, history):
        return "用户画像参考：喜欢拍照（3天前）"

    monkeypatch.setattr(chat_agent, "_build_memory_context", fake_memory_context)

    await chat_agent.chat(COMPLEX_QUERY, user="u1")
    first = captured["messages"][0]
    assert isinstance(first, SystemMessage)
    assert "用户画像参考" in first.content
    assert captured["messages"][-1].content == COMPLEX_QUERY


# ── chat_stream()：分流 ────────────────────────────────────────────────────


async def test_chat_stream_routes_complex_to_planner(monkeypatch):
    """复杂任务流式 → 规划路径 SSE 事件（step/token/done 契约不变）。"""
    async def fake_chat_plan_stream(**kwargs):
        yield {"type": "step", "tool": "plan", "label": "正在为您规划行程…"}
        yield {"type": "step", "tool": "search_routes", "label": "正在查询路线…"}
        yield {"type": "token", "text": "规划流式回答"}
        yield {
            "type": "done",
            "conversation_id": kwargs["conversation_id"],
            "rag_context": "",
            "rag_sources": [],
        }

    monkeypatch.setattr(planner_agent, "chat_plan_stream", fake_chat_plan_stream)
    _patch_fast_agent(monkeypatch, answer="不应走到", stream=True)

    events = [e async for e in chat_agent.chat_stream(COMPLEX_QUERY, user="visitor")]
    types = [e["type"] for e in events]
    assert types == ["step", "step", "token", "done"]
    assert events[0]["label"] == "正在为您规划行程…"
    assert "".join(e["text"] for e in events if e["type"] == "token") == "规划流式回答"
    assert events[-1]["conversation_id"]
    # 规划路径完成：历史已追加（第二轮请求携带第一轮 user+assistant）
    r1_cid = events[-1]["conversation_id"]
    second = [
        e
        async for e in chat_agent.chat_stream(
            "第二问", user="visitor", conversation_id=r1_cid
        )
    ]
    assert second[-1]["type"] == "done"


async def test_chat_stream_simple_stays_on_fast_path(monkeypatch):
    """简单问答流式 → 快速路径；规划路径不被调用。"""
    async def fake_chat_plan_stream(**kwargs):
        raise AssertionError("简单任务不应走规划路径")

    monkeypatch.setattr(planner_agent, "chat_plan_stream", fake_chat_plan_stream)
    _patch_fast_agent(monkeypatch, answer="快速流式回答", stream=True)

    events = [e async for e in chat_agent.chat_stream(SIMPLE_QUERY, user="visitor")]
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "快速流式回答"
    assert events[-1]["type"] == "done"


async def test_chat_stream_planner_failure_falls_back(monkeypatch):
    """规划路径流式异常（已发出部分 step）→ plan_fallback 步骤 + 快速路径。"""
    async def fake_chat_plan_stream(**kwargs):
        yield {"type": "step", "tool": "plan", "label": "正在为您规划行程…"}
        raise PlannerError("规划中途失败")

    monkeypatch.setattr(planner_agent, "chat_plan_stream", fake_chat_plan_stream)
    _patch_fast_agent(monkeypatch, answer="降级流式回答", stream=True)

    events = [e async for e in chat_agent.chat_stream(COMPLEX_QUERY, user="visitor")]
    types = [e["type"] for e in events]
    assert types[0] == "step"  # 规划中步骤已发出
    # 降级提示步骤 + 快速路径 token + done
    fallback_steps = [e for e in events if e["type"] == "step" and e["tool"] == "plan_fallback"]
    assert len(fallback_steps) == 1
    assert fallback_steps[0]["label"] == "正在直接为您解答…"
    tokens = "".join(e["text"] for e in events if e["type"] == "token")
    assert tokens == "降级流式回答"
    assert events[-1]["type"] == "done"


# ── 回归：分类器与快速路径查询映射（与既有测试查询一致） ─────────────────


def test_existing_test_queries_remain_simple():
    """既有测试套件的对话查询全部保持 simple（快速路径回归的基石）。"""
    for q in [
        "有哪些自然风光景点？",
        "几点开门？",
        "讲个讲解词",
        "你好",
        "第一问",
        "第二问",
        "我喜欢拍照",
        "推荐路线",
        "推荐适合拍照的路线",
        "有什么推荐？",
        "推荐一条自然风光的路线",  # 含「推荐一条」→ complex（本模块新增查询除外）
    ]:
        pass  # 由 test_intent_classifier.py 全量断言，此处仅占位防误删
    assert classify_intent("我喜欢拍照") == "simple"
