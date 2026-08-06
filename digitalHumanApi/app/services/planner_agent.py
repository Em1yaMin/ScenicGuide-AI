"""复杂任务规划路径（阶段3 T1 双模式：LangGraph 显式图）。

船长决策 2026-08-06 方案B：复杂任务走「规划 → 多步执行 → 总结」三阶段
显式图，与快速路径（create_agent 单层工具循环）并存。入口分流见
`app/services/chat_agent.py` 的 `chat()` / `chat_stream()`。

实现方案选择说明：

- **为什么自定义 StateGraph 而非复用 create_agent**：create_agent 是「模型
  自行决定每一步调哪个工具」的单层循环，无法把「先生成计划、再逐条执行、
  最后统一总结」的三阶段结构显式建模，也无法在总结前展示完整执行步骤
  （SSE 可视化思考过程）。方案B 要求显式的规划图，故自建 StateGraph
  （langgraph 1.2 自带能力，零新依赖）
- **规划阶段用 astream 累积 + JSON 解析**：图内模型调用会被 langgraph 统一
  转为流式（stream=True），`with_structured_output` 的输出解析器不兼容流式
  分块（实测报错），故规划节点用 `llm.astream` 累积后手动解析 JSON
  （`_parse_plan`：JSON 优先 + 正则提取兜底，跨 DeepSeek/OpenAI 兼容端点
  均可用，不依赖 response_format 支持）；规划失败/为空抛 `PlannerError`
  由上层降级快速路径
- **执行阶段确定性逐步骤调用**：复用 `chat_agent._TOOLS`（6 个结构化直查
  + 知识检索 + 规则推荐），经 `BaseTool.ainvoke` 在 asyncio 线程池执行
  （同步工具内含 `asyncio.run` 的场景安全）；单步失败不中断，记录错误
  继续后续步骤
- **总结阶段流式聚合**：`llm.astream` 逐 token 产出最终回答（与快速路径
  SSE 契约一致），经 langgraph `stream_mode="messages"` 携带
  `langgraph_node == "summarize"` 的元数据区分
- **安全边界与双轨回落不变**：工具集不含预订/账号/支付类操作，system
  prompt 引导人工客服；本模块任何异常上抛，由 chat_agent 层回落快速路径、
  再由路由层回落 Dify
"""
import json
import logging
import re
import threading
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from app.core.llm import get_chat_llm
from app.services.rag_service import build_rag_context

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

# 对话输出上限（token）与 LLM 调用超时：与快速路径（chat_agent）一致
_PLANNER_MAX_TOKENS = 2048
_PLANNER_TIMEOUT = 60
# 规划步骤数上限（防止异常 LLM 规划出超长步骤序列）
_MAX_PLAN_STEPS = 6
# 单步工具输出注入总结 prompt 的最大长度（控制 token 开销）
_MAX_STEP_OUTPUT_LEN = 1200
# 图执行步数上限（plan + ≤6 步执行 + summarize，余量充足防死循环）
_PLANNER_RECURSION_LIMIT = 32
# 知识检索工具名（RAG 来源透出用）
_KNOWLEDGE_TOOL = "search_knowledge"


class PlannerError(RuntimeError):
    """规划路径失败（规划生成失败/结果为空/总结为空）。

    由上层（chat_agent）捕获后降级快速路径，最终由路由层兜底 Dify。
    """


# ── 规划 schema（工具调用式结构化输出） ──────────────────────────────────


class PlanStep(BaseModel):
    """单步规划：一个工具调用。"""

    tool: str = Field(description="工具名（必须是可用工具之一）")
    args: Dict[str, Any] = Field(description="工具参数（必须符合工具签名）")
    note: str = Field(description="该步骤的目的说明（中文，一句话）")


class Plan(BaseModel):
    """规划结果：按顺序执行的步骤序列。"""

    steps: List[PlanStep] = Field(description="步骤序列（按依赖顺序）")


# ── 图状态 ────────────────────────────────────────────────────────────────


class PlannerState(TypedDict):
    """规划图状态。

    messages: 完整对话上下文（历史 + 长期记忆注入 + 当前问题，由调用方组装）
    plan: 规划步骤 [{tool, args, note}]
    step_index: 当前待执行步骤下标
    results: 已执行步骤的结果 [{tool, args, note, output, ok}]
    answer: 总结阶段生成的最终回答
    """

    messages: List[BaseMessage]
    plan: List[Dict[str, Any]]
    step_index: int
    results: List[Dict[str, Any]]
    answer: str


# ── Prompt 构造 ───────────────────────────────────────────────────────────


def _build_plan_prompt(tools: List[Any]) -> str:
    """规划阶段 system prompt（含可用工具清单，供 LLM 生成合法步骤）。"""
    tool_lines = []
    for t in tools:
        args_desc = (
            ", ".join(f"{k}={v.get('type', '')}" for k, v in (t.args or {}).items())
            or "无参数"
        )
        desc = (t.description or "").strip().splitlines()[0]
        tool_lines.append(f"- {t.name}({args_desc})：{desc}")
    tools_text = "\n".join(tool_lines)
    return (
        "你是「景区导览」AI 数字人助手的规划引擎。用户提出了需要多步查询/组合的"
        "复杂问题（如规划一日游行程、组合多个景点与路线、结合人群偏好推荐）。\n\n"
        f"可用工具（按需调用，每个步骤只调用一个工具）：\n{tools_text}\n\n"
        "请生成一份按顺序执行的步骤计划，要求：\n"
        "1. 每步只调用一个工具，args 必须严格符合该工具的参数名\n"
        "2. 步骤按依赖顺序：先查候选景点/路线（search_spots / search_routes / "
        "filter_by_tags），需要详情时再查详情（get_spot_detail / get_route_detail，"
        "仅当 id 可从上下文得知时使用）；知识类问题用 search_knowledge；识别到"
        "游览偏好（人群/时间/主题）且需要整体路线推荐时用 recommend_routes\n"
        f"3. 步骤数不超过 {_MAX_PLAN_STEPS} 步，覆盖用户全部需求点"
        "（人群偏好、时间长度、主题等）\n"
        "4. 每步 note 用一句中文说明该步骤的目的\n"
        "5. 预订、购票、账号、支付等操作类需求不提供工具，不要规划这类步骤\n"
        "\n"
        "严格输出一个 JSON 对象（不要输出任何其他文字，不要用 markdown 代码块）：\n"
        '{"steps": [{"tool": "工具名", "args": {"参数名": "参数值"}, '
        '"note": "目的说明"}]}'
    )


_SUMMARY_SYSTEM_PROMPT = """你是「景区导览」的 AI 数字人助手。用户提出了需要多步查询的组合问题，以下提供了按计划执行的各步骤及其工具返回结果。请基于这些真实数据，组织一份完整、亲切、结构清晰的中文回答：

1. 优先引用工具返回的真实数据（景点/路线名称、标签、时长、难度、描述、推荐理由等），禁止编造不存在的景点或路线
2. 工具未找到数据时，如实说明「暂时没有相关资料」，不要猜测
3. 若用户提到人群偏好（如带老人小孩、亲子、情侣、喜欢拍照等），回答中给出针对性的建议
4. 多景点/多阶段行程用清晰的列表或小节组织（如「上午」「下午」或「第一天」「第二天」）
5. 安全边界：预订、购票、账号、支付等操作类需求本助手不直接办理，需人工确认——礼貌说明并引导游客联系人工客服"""


def _format_results(results: List[Dict[str, Any]]) -> str:
    """执行结果 → 总结阶段的输入文本（控制长度，防 token 膨胀）。"""
    lines = ["以下是按计划执行的各步骤及工具返回结果："]
    for i, r in enumerate(results, 1):
        args_text = json.dumps(r.get("args") or {}, ensure_ascii=False)
        note = r.get("note") or ""
        output = (r.get("output") or "")[:_MAX_STEP_OUTPUT_LEN]
        lines.append(f"步骤{i}：调用 {r.get('tool')}({args_text}) —— {note}")
        lines.append(f"返回：{output}")
    return "\n".join(lines)


# ── 图构建 ────────────────────────────────────────────────────────────────


def _parse_plan(text: str) -> Optional[Plan]:
    """从 LLM 输出解析规划结果（JSON 优先，正则提取兜底，失败返回 None）。

    图内模型调用会被 langgraph 统一转为流式（stream=True），因此规划阶段
    不用 with_structured_output（其输出解析器不兼容流式分块），而是
    llm.astream 累积后在此手动解析——跨端点（DeepSeek/OpenAI 兼容）更健壮。
    """
    if not text or not text.strip():
        return None
    try:
        return Plan(**json.loads(text))
    except Exception:
        pass
    # 正则提取首个 JSON 对象块（模型可能夹带多余文字）
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return Plan(**json.loads(m.group(0)))
        except Exception:
            return None
    return None


def _validate_steps(
    steps: List[Any], tools_by_name: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """校验/清洗规划步骤：仅保留合法工具名与 dict 参数，截断到上限。

    返回空列表表示规划无效（由调用方抛 PlannerError 降级）。
    """
    valid: List[Dict[str, Any]] = []
    for s in steps:
        name = getattr(s, "tool", "") or ""
        if name not in tools_by_name:
            logger.warning("规划步骤包含未知工具（忽略）: %s", name)
            continue
        args = getattr(s, "args", None) or {}
        if not isinstance(args, dict):
            logger.warning("规划步骤参数非法（忽略）: tool=%s args=%r", name, args)
            continue
        valid.append(
            {
                "tool": name,
                "args": args,
                "note": (getattr(s, "note", "") or "")[:100],
            }
        )
        if len(valid) >= _MAX_PLAN_STEPS:
            break
    return valid


def _build_graph(*, tools: List[Any], llm: Any) -> Any:
    """构建规划路径编译图（plan → execute 循环 → summarize）。

    节点均为 async，工具经 BaseTool.ainvoke 在线程池执行；总结节点
    用 llm.astream 流式产出（供 stream_mode="messages" 透出 token）。
    """
    tools_by_name = {t.name: t for t in tools}
    plan_prompt = _build_plan_prompt(tools)

    async def plan_node(state: PlannerState) -> Dict[str, Any]:
        chunks: List[str] = []
        try:
            # 图内模型调用被 langgraph 统一转为流式：经 astream 累积后解析 JSON
            # （不用 with_structured_output，见 _parse_plan 注释）
            async for chunk in llm.astream(
                [SystemMessage(content=plan_prompt)] + list(state["messages"])
            ):
                content = getattr(chunk, "content", "")
                if isinstance(content, str) and content:
                    chunks.append(content)
        except Exception as e:
            raise PlannerError(f"规划生成失败: {e}") from e
        plan_obj = _parse_plan("".join(chunks))
        steps = _validate_steps(plan_obj.steps if plan_obj else [], tools_by_name)
        if not steps:
            raise PlannerError("规划结果为空或全部步骤无效")
        logger.info(
            "规划路径: 生成 %d 步计划: %s",
            len(steps),
            [s["tool"] for s in steps],
        )
        return {"plan": steps, "step_index": 0, "results": []}

    async def execute_node(state: PlannerState) -> Dict[str, Any]:
        idx = state["step_index"]
        if idx >= len(state["plan"]):
            # 防御：无更多步骤（正常流程不会发生）
            return {}
        step = state["plan"][idx]
        tool = tools_by_name.get(step["tool"])
        output, ok = "", False
        if tool is not None:
            try:
                # BaseTool.ainvoke：同步工具在线程池执行（内部 asyncio.run 安全）
                output = await tool.ainvoke(step["args"])
                ok = True
            except Exception as e:
                logger.warning(
                    "规划步骤工具执行失败（继续后续步骤）: tool=%s err=%s",
                    step["tool"],
                    e,
                )
                output = json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)
        else:
            output = json.dumps(
                {"error": f"未知工具: {step['tool']}"}, ensure_ascii=False
            )
        results = state["results"] + [
            {
                "tool": step["tool"],
                "args": step["args"],
                "note": step.get("note", ""),
                "output": output,
                "ok": ok,
            }
        ]
        return {"step_index": idx + 1, "results": results}

    def route(state: PlannerState) -> str:
        if state["step_index"] < len(state["plan"]):
            return "execute"
        return "summarize"

    async def summarize_node(state: PlannerState) -> Dict[str, Any]:
        messages = (
            [SystemMessage(content=_SUMMARY_SYSTEM_PROMPT)]
            + list(state["messages"])
            + [HumanMessage(content=_format_results(state["results"]))]
        )
        chunks: List[str] = []
        try:
            async for chunk in llm.astream(messages):
                content = getattr(chunk, "content", "")
                if isinstance(content, str) and content:
                    chunks.append(content)
        except Exception as e:
            # 总结失败（含流式无分块的 ValueError）→ 统一转 PlannerError
            raise PlannerError(f"总结生成失败: {e}") from e
        answer = "".join(chunks).strip()
        if not answer:
            raise PlannerError("总结生成结果为空")
        return {"answer": answer, "results": state["results"]}

    graph = StateGraph(PlannerState)
    graph.add_node("plan", plan_node)
    graph.add_node("execute", execute_node)
    graph.add_node("summarize", summarize_node)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "execute")
    graph.add_conditional_edges(
        "execute", route, {"execute": "execute", "summarize": "summarize"}
    )
    graph.add_edge("summarize", END)
    return graph.compile()


# ── 图缓存（默认 LLM 按工具集缓存；测试注入 llm 时独立构建） ──────────────

_graph_cache: Dict[Tuple[str, ...], Any] = {}
_graph_lock = threading.Lock()


def _get_graph(tools: List[Any], llm: Optional[Any] = None) -> Any:
    """获取规划图实例：默认 LLM 按工具名集合缓存；测试注入 llm 时新建。"""
    if llm is not None:
        return _build_graph(tools=tools, llm=llm)
    key = tuple(t.name for t in tools)
    with _graph_lock:
        if key not in _graph_cache:
            _graph_cache[key] = _build_graph(
                tools=tools,
                llm=get_chat_llm(
                    max_tokens=_PLANNER_MAX_TOKENS, timeout=_PLANNER_TIMEOUT
                ),
            )
        return _graph_cache[key]


def reset_planner_cache() -> None:
    """重置规划图缓存（供测试与配置热更新使用）。"""
    with _graph_lock:
        _graph_cache.clear()


# ── 结果提取 ──────────────────────────────────────────────────────────────


def _extract_rag_from_results(
    results: List[Dict[str, Any]], top_k: int
) -> Tuple[str, List[Dict[str, Any]]]:
    """从执行结果中提取最后一次 search_knowledge 的检索结果。

    Returns:
        (rag_context, rag_sources)：与快速路径 chat() 的契约一致
        （build_rag_context 格式 + source/score/namespace 列表）
    """
    for r in reversed(results):
        if r.get("tool") != _KNOWLEDGE_TOOL or not r.get("ok"):
            continue
        try:
            data = json.loads(r.get("output") or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, list):
            continue
        top = data[:top_k]
        rag_context = build_rag_context(top)
        rag_sources = [
            {
                "source": x.get("source", ""),
                "score": x.get("score", 0.0),
                "namespace": x.get("namespace", ""),
            }
            for x in top
        ]
        return rag_context, rag_sources
    return "", []


# ── 公开接口 ──────────────────────────────────────────────────────────────


@dataclass
class PlanResult:
    """规划路径结果（字段与快速路径 ChatAgentResult 对齐）。"""

    answer: str
    plan: List[Dict[str, Any]] = field(default_factory=list)
    rag_context: str = ""
    rag_sources: List[Dict[str, Any]] = field(default_factory=list)


def _initial_state(messages: List[BaseMessage]) -> Dict[str, Any]:
    return {
        "messages": list(messages),
        "plan": [],
        "step_index": 0,
        "results": [],
        "answer": "",
    }


async def chat_plan(
    messages: List[BaseMessage],
    *,
    rag_top_k: int = 3,
    tools: Optional[List[Any]] = None,
    llm: Optional[Any] = None,
) -> PlanResult:
    """规划路径（非流式）：规划 → 多步执行 → 总结。

    Args:
        messages: 完整对话上下文（历史 + 记忆注入 SystemMessage + 当前问题）
        rag_top_k: RAG 结果数（用于 rag_sources 透出）
        tools: 可用工具集（默认由调用方传入 chat_agent._TOOLS 及 use_rag 过滤）
        llm: 测试注入的 LLM（默认走 llm.py 工厂）

    Returns:
        PlanResult(answer, plan, rag_context, rag_sources)

    Raises:
        PlannerError / LLM 异常：由上层降级快速路径
    """
    if tools is None:
        raise PlannerError("规划路径需要显式传入工具集（由 chat_agent 提供）")
    graph = _get_graph(tools, llm)
    final = await graph.ainvoke(
        _initial_state(messages),
        config={"recursion_limit": _PLANNER_RECURSION_LIMIT},
    )
    answer = final.get("answer", "")
    if not answer.strip():
        raise PlannerError("规划路径未产出回答")
    rag_context, rag_sources = _extract_rag_from_results(
        final.get("results", []), rag_top_k
    )
    return PlanResult(
        answer=answer,
        plan=final.get("plan", []),
        rag_context=rag_context,
        rag_sources=rag_sources,
    )


async def chat_plan_stream(
    messages: List[BaseMessage],
    *,
    conversation_id: str = "",
    rag_top_k: int = 3,
    tools: Optional[List[Any]] = None,
    labels: Optional[Dict[str, str]] = None,
    llm: Optional[Any] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """规划路径（流式，SSE 事件源，与快速路径契约一致）。

    Yields:
        {"type": "step", "tool", "label"}  规划中/执行中（每个工具一步）/总结中
        {"type": "token", "text"}          总结回答增量（逐字输出）
        {"type": "done", "conversation_id",
         "rag_context", "rag_sources"}     流式完成

    Raises:
        PlannerError / LLM 异常：由上层降级快速路径（生成器内任何异常
        经 async for 透传给调用方）
    """
    if tools is None:
        raise PlannerError("规划路径需要显式传入工具集（由 chat_agent 提供）")
    graph = _get_graph(tools, llm)
    final_results: List[Dict[str, Any]] = []
    answer_len = 0
    summarize_step_emitted = False
    async for mode, data in graph.astream(
        _initial_state(messages),
        stream_mode=["updates", "messages"],
        config={"recursion_limit": _PLANNER_RECURSION_LIMIT},
    ):
        if mode == "updates":
            for node_name, update in data.items():
                if node_name == "plan":
                    yield {
                        "type": "step",
                        "tool": "plan",
                        "label": "正在为您规划行程…",
                    }
                elif node_name == "execute":
                    results = update.get("results") or []
                    if results:
                        tool_name = results[-1].get("tool", "")
                        yield {
                            "type": "step",
                            "tool": tool_name,
                            "label": (labels or {}).get(
                                tool_name, f"正在调用工具 {tool_name}…"
                            ),
                        }
                    else:
                        yield {
                            "type": "step",
                            "tool": "execute",
                            "label": "正在执行行程计划…",
                        }
                elif node_name == "summarize":
                    # 节点完成：捕获最终结果（step 事件已在首个 token 时输出，
                    # 保证「总结中 → token」的顺序）；answer 不在此透出
                    final_results = update.get("results") or final_results
        elif mode == "messages":
            chunk, meta = data
            # 仅透出总结节点的 token（规划节点的结构化输出为一次性调用，无流；
            # 首个 token 前先输出「正在生成行程安排…」步骤事件）
            if meta.get("langgraph_node") == "summarize":
                if not summarize_step_emitted:
                    summarize_step_emitted = True
                    yield {
                        "type": "step",
                        "tool": "summarize",
                        "label": "正在生成行程安排…",
                    }
                content = getattr(chunk, "content", "")
                if isinstance(content, str) and content:
                    answer_len += len(content)
                    yield {"type": "token", "text": content}

    rag_context, rag_sources = _extract_rag_from_results(final_results, rag_top_k)
    logger.info(
        "规划路径(流式): steps=%d tools=%s answer_len=%d",
        len(final_results),
        [r.get("tool") for r in final_results],
        answer_len,
    )
    yield {
        "type": "done",
        "conversation_id": conversation_id,
        "rag_context": rag_context,
        "rag_sources": rag_sources,
    }
