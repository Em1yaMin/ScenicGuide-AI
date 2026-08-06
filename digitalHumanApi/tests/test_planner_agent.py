"""规划路径（app/services/planner_agent.py）单测。

覆盖：规划生成（llm.astream 累积 + JSON 解析，MockTransport 模拟流式响应）、
多步执行（真实工具调用本地 JSON / 规则推荐，不发网络请求）、总结生成（流式）、
SSE 事件序列（step/token/done）、RAG 来源透出、规划失败/未知工具/总结为空的
安全降级（PlannerError 上抛）、工具执行失败不中断后续步骤。

Mock 说明：图内模型调用被 langgraph 统一转为流式（stream=true），因此
handler 按首条 system 消息内容区分规划节点（含「规划引擎」提示）与总结节点
（含总结提示），两者均返回 SSE 文本流。
"""

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services import chat_agent, planner_agent
from app.services.planner_agent import PlannerError

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
    """固定端点配置 + 清理图缓存，避免跨测试污染。"""
    for key, value in {**PRIMARY, **FALLBACK}.items():
        monkeypatch.setattr(settings, key, value)
    # 记忆提取钩子固定为 no-op（规划路径不直接调用，双保险）
    async def _noop_memory(*args, **kwargs):
        return {"written": 0, "skipped": True}

    monkeypatch.setattr(chat_agent, "_memory_extract_hook", _noop_memory)
    planner_agent.reset_planner_cache()
    yield
    planner_agent.reset_planner_cache()


# ── Mock 响应构造（与 test_chat_agent.py 同风格） ─────────────────────────


def _sse(payload: str) -> str:
    return f"data: {payload}\n\n"


def _chunk(text: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 1700000000,
        "model": PRIMARY["LLM_MODEL"],
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": None,
            }
        ],
    }


def _text_stream(text: str) -> httpx.Response:
    body = "".join(
        _sse(json.dumps(_chunk(ch), ensure_ascii=False)) for ch in text
    )
    body += "data: [DONE]\n\n"
    return httpx.Response(
        200, text=body, headers={"content-type": "text/event-stream"}
    )


def _llm_with_mock(handler) -> "object":
    """构造注入 MockTransport 的 ChatOpenAI（同步/异步 client 均注入）。"""
    transport = httpx.MockTransport(handler)
    return get_chat_llm(
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def _dual_handler(plan_steps, summary_text: str = "", plan_text: str = None):
    """构造区分规划/总结节点的 mock handler（图内模型调用统一 stream=true，
    按首条 system 消息内容区分：规划提示含「规划引擎」，总结提示含「组合问题」）。

    plan_text: 覆盖默认的步骤 JSON 文本（如空计划 / 非 JSON / 500 场景）。
    summary_text: 总结节点流式文本（空串 → LLM 抛无分块错误 → PlannerError）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        first_sys = next(
            (m.get("content", "") for m in body.get("messages", []) if m.get("role") == "system"),
            "",
        )
        if "规划引擎" in first_sys:
            payload = plan_text if plan_text is not None else json.dumps(
                {"steps": plan_steps}, ensure_ascii=False
            )
            return _text_stream(payload)
        return _text_stream(summary_text)

    return handler


def _messages(query: str = "规划一日游，带老人小孩，喜欢拍照"):
    """构造规划路径输入：历史 + 记忆注入 + 当前问题。"""
    return [
        SystemMessage(content="用户画像参考：喜欢拍照（3天前）"),
        HumanMessage(content=query),
    ]


# ── 全链路：规划 → 多步执行 → 总结（非流式） ──────────────────────────────


async def test_chat_plan_full_flow():
    """规划生成 3 步计划 → 逐步骤执行真实工具 → 总结回答。"""
    plan_steps = [
        {"tool": "search_routes", "args": {"query": "一日游"}, "note": "查找一日游路线"},
        {"tool": "filter_by_tags", "args": {"tags": "亲子"}, "note": "按亲子筛选路线"},
        {"tool": "recommend_routes", "args": {"preference": "带老人小孩，喜欢拍照，一日游"}, "note": "整体推荐"},
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "为您推荐以下行程安排：上午赏花海，下午逛小镇。"))
    result = await planner_agent.chat_plan(
        _messages(),
        tools=chat_agent._select_tools(use_rag=True),
        llm=llm,
    )
    # 总结回答
    assert "行程安排" in result.answer
    # 计划包含 3 步，且按原顺序执行
    assert [s["tool"] for s in result.plan] == [
        "search_routes",
        "filter_by_tags",
        "recommend_routes",
    ]
    # RAG 字段结构兼容（无 search_knowledge 步骤 → 默认空）
    assert result.rag_context == ""
    assert result.rag_sources == []


async def test_chat_plan_async():
    """chat_plan 为 async 接口（图经 ainvoke 执行）。"""
    plan_steps = [
        {"tool": "search_routes", "args": {"query": "亲子"}, "note": "查亲子路线"}
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "好的，为您整理路线信息。"))
    result = await planner_agent.chat_plan(
        _messages("推荐亲子路线"),
        tools=chat_agent._select_tools(use_rag=False),
        llm=llm,
    )
    assert "路线" in result.answer
    assert result.plan[0]["tool"] == "search_routes"


async def test_chat_plan_executes_tools_with_async_run_inside():
    """执行阶段经 BaseTool.ainvoke 线程池执行（recommend_routes 内含
    asyncio.run 的场景安全，不报 running loop 错误）。"""
    plan_steps = [
        {"tool": "recommend_routes", "args": {"preference": "喜欢自然风光"}, "note": "推荐"}
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "推荐路线完成。"))
    result = await planner_agent.chat_plan(
        _messages("推荐一条自然风光的路线"),
        tools=chat_agent._select_tools(use_rag=False),
        llm=llm,
    )
    assert result.plan[0]["tool"] == "recommend_routes"
    assert result.answer == "推荐路线完成。"


# ── RAG 来源透出 ──────────────────────────────────────────────────────────


async def test_chat_plan_rag_sources_from_search_knowledge():
    """执行结果中 search_knowledge 的检索结果透出到 rag_context/rag_sources
    （与快速路径契约一致）。测试环境知识库索引可能为空 → 结构兼容断言。"""
    plan_steps = [
        {"tool": "search_knowledge", "args": {"query": "讲解词", "top_k": 2}, "note": "查讲解词"},
        {"tool": "search_routes", "args": {"query": "亲子"}, "note": "查路线"},
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "回答完成。"))
    result = await planner_agent.chat_plan(
        _messages(),
        tools=chat_agent._select_tools(use_rag=True),
        llm=llm,
    )
    assert isinstance(result.rag_sources, list)
    assert isinstance(result.rag_context, str)


# ── 流式 SSE 事件序列 ──────────────────────────────────────────────────────


async def test_chat_plan_stream_event_sequence():
    """SSE 事件序列：step(规划中) → step(每个工具执行) → step(总结中)
    → token(总结增量) → done(会话 id + RAG 字段)。"""
    plan_steps = [
        {"tool": "search_routes", "args": {"query": "一日游"}, "note": "查路线"},
        {"tool": "search_spots", "args": {"query": "自然"}, "note": "查景点"},
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "推荐行程安排如下。"))
    events = [
        e
        async for e in planner_agent.chat_plan_stream(
            _messages(),
            conversation_id="cid-plan",
            tools=chat_agent._select_tools(use_rag=False),
            labels=chat_agent._STEP_LABELS,
            llm=llm,
        )
    ]

    types = [e["type"] for e in events]
    assert types[0] == "step" and types[-1] == "done"
    assert set(types) <= {"step", "token", "done"}

    steps = [e for e in events if e["type"] == "step"]
    # 规划中 → 2 个工具执行 → 总结中
    assert [s["tool"] for s in steps] == [
        "plan",
        "search_routes",
        "search_spots",
        "summarize",
    ]
    assert steps[0]["label"] == "正在为您规划行程…"
    assert steps[1]["label"] == "正在查询路线…"
    assert steps[2]["label"] == "正在查询景点…"
    assert steps[3]["label"] == "正在生成行程安排…"

    # token 拼接 = 总结回答（且必须出现在总结步骤事件之后）
    token_idx = [i for i, e in enumerate(events) if e["type"] == "token"]
    summarize_idx = [
        i for i, e in enumerate(events) if e["type"] == "step" and e["tool"] == "summarize"
    ][0]
    assert token_idx and token_idx[0] > summarize_idx
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert answer == "推荐行程安排如下。"

    done = events[-1]
    assert done["type"] == "done"
    assert done["conversation_id"] == "cid-plan"
    assert done["rag_context"] == ""
    assert done["rag_sources"] == []


async def test_chat_plan_stream_rag_sources_in_done():
    """流式 done 事件携带 search_knowledge 的 RAG 来源（结构兼容）。"""
    plan_steps = [
        {"tool": "search_knowledge", "args": {"query": "讲解词", "top_k": 2}, "note": "查讲解词"}
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "这是讲解词总结。"))
    events = [
        e
        async for e in planner_agent.chat_plan_stream(
            _messages("讲个讲解词"),
            conversation_id="cid",
            tools=chat_agent._select_tools(use_rag=True),
            labels=chat_agent._STEP_LABELS,
            llm=llm,
        )
    ]
    done = events[-1]
    assert done["type"] == "done"
    assert isinstance(done["rag_sources"], list)
    assert isinstance(done["rag_context"], str)


# ── 降级：规划失败 / 无效步骤 / 总结为空 / LLM 故障 ───────────────────────


async def test_chat_plan_empty_plan_raises():
    """规划结果为空 → PlannerError（上层降级快速路径）。"""
    llm = _llm_with_mock(_dual_handler([], plan_text=json.dumps({"steps": []})))
    with pytest.raises(PlannerError):
        await planner_agent.chat_plan(
            _messages(),
            tools=chat_agent._select_tools(use_rag=False),
            llm=llm,
        )


async def test_chat_plan_unknown_tool_steps_filtered():
    """规划包含未知工具 + 一个合法步骤：未知步骤被过滤，合法步骤执行。"""
    plan_steps = [
        {"tool": "book_ticket", "args": {}, "note": "预订（越界，应被过滤）"},
        {"tool": "search_routes", "args": {"query": "亲子"}, "note": "查亲子路线"},
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "回答完成。"))
    result = await planner_agent.chat_plan(
        _messages(),
        tools=chat_agent._select_tools(use_rag=False),
        llm=llm,
    )
    assert [s["tool"] for s in result.plan] == ["search_routes"]


async def test_chat_plan_all_invalid_steps_raise():
    """全部步骤非法（未知工具，如预订/支付）→ PlannerError。"""
    plan_steps = [
        {"tool": "book_ticket", "args": {}, "note": "预订"},
        {"tool": "pay_order", "args": {}, "note": "支付"},
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps))
    with pytest.raises(PlannerError):
        await planner_agent.chat_plan(
            _messages(),
            tools=chat_agent._select_tools(use_rag=False),
            llm=llm,
        )


async def test_chat_plan_plan_json_not_parseable_raises():
    """规划输出非 JSON（夹带文字且正则无法提取）→ PlannerError。"""
    llm = _llm_with_mock(_dual_handler([], plan_text="抱歉，我无法生成计划。"))
    with pytest.raises(PlannerError):
        await planner_agent.chat_plan(
            _messages(),
            tools=chat_agent._select_tools(use_rag=False),
            llm=llm,
        )


async def test_chat_plan_empty_summary_raises():
    """总结回答为空（流式无分块）→ PlannerError（上层降级，不产出空回答）。"""
    plan_steps = [
        {"tool": "search_routes", "args": {"query": "亲子"}, "note": "查路线"}
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, summary_text=""))
    with pytest.raises(PlannerError):
        await planner_agent.chat_plan(
            _messages(),
            tools=chat_agent._select_tools(use_rag=False),
            llm=llm,
        )


async def test_chat_plan_llm_error_raises():
    """规划节点 LLM 请求失败（500）→ PlannerError（包装，上层降级）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    llm = _llm_with_mock(handler)
    with pytest.raises(PlannerError):
        await planner_agent.chat_plan(
            _messages(),
            tools=chat_agent._select_tools(use_rag=False),
            llm=llm,
        )


# ── 工具执行失败不中断后续步骤 ─────────────────────────────────────────────


async def test_chat_plan_tool_failure_continues():
    """单个工具执行抛异常：记录错误、继续后续步骤、总结仍产出回答。"""
    @tool
    def boom_tool(query: str) -> str:
        """总是失败的测试工具。"""
        raise RuntimeError("boom")

    @tool
    def ok_tool(query: str) -> str:
        """正常测试工具。"""
        return json.dumps({"name": "路线A"}, ensure_ascii=False)

    plan_steps = [
        {"tool": "boom_tool", "args": {"query": "x"}, "note": "失败步骤"},
        {"tool": "ok_tool", "args": {"query": "y"}, "note": "成功步骤"},
    ]
    llm = _llm_with_mock(_dual_handler(plan_steps, "回答完成。"))
    result = await planner_agent.chat_plan(
        _messages(),
        tools=[boom_tool, ok_tool],
        llm=llm,
    )
    assert result.answer == "回答完成。"
    assert result.plan[0]["tool"] == "boom_tool"
    assert result.plan[1]["tool"] == "ok_tool"
