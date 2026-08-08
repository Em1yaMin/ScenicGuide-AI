"""deepagents 装配层核心（A2 试点 → **A3 对话主链路**，批次3）。

A3 起为对话主链路（路由 `/api/v1/dialogue/chat` 默认引擎 langchain 走本
模块；异常由路由层回落 Dify，演示不翻车）；chat_agent / planner_agent
自研双模式保留为弃用代码（A4 收官再评估删除）。本模块提供：

- `get_agent()` — 构造/缓存 deepagents agent（HarnessProfile 排除内置工具
  + LLM 工厂 + 场景工具 + 记忆四工具；构造耗时 ~24s，必须缓存）
- `chat()` / `chat_stream()` — 对话入口（**A3 接入会话管理**：短期记忆
  持久化 + conversation_id 生成/透出 + 多轮上下文组装；意图分类双模式
  挂载 + 轻量用户摘要注入 + 流式 SSE 契约映射，A1 报告 §5）

**意图分类双模式挂载**（船长决策：planning=prompt 指令，保留意图分类
双模式）：复用 `intent_classifier` 规则分类——简单问答不注入规划指令
（快速直接作答）；复杂任务注入 `_PLAN_INSTRUCTION` 规划指令（prompt 级
规划，A1 实测有效：回答结构化分节、工具链更完整）。

**会话管理复用主链路存储层**（A3）：`chat()`/`chat_stream()` 复用
chat_agent 的会话存储（`_get_or_create_conversation` / `_append_history`，
进程内 TTL 缓存 + SQLite 双写，重启可恢复）与记忆钩子
（`_schedule_memory_extraction`）——单一数据源保证：① 重启后同
conversation_id 续聊可恢复多轮上下文；② 路由层回落 Dify 时
`chat_agent.has_conversation` 归属判定无需改动（生成的 id 登记在同一集合）。

**A4 成本/速度调优**（批次3 收官，实测定参见 doc/deepagents-tuning.md）：
ToolCallLimitMiddleware 单轮工具调用上限（默认 15，continue 优雅降级）
+ 高频只读工具归一化缓存（tool_cache.py）+ 回答后触发上下文压缩
（compaction.py，SummarizationMiddleware 引擎）——见下方对应函数注释。

**只读复用主链路**：场景工具选择（`chat_agent._select_tools`）、系统提示词
（`chat_agent.SYSTEM_PROMPT`）、RAG 来源提取（`chat_agent._extract_rag_info`）
均从 chat_agent 只读引用（chat_agent 保留期间维持单一来源，A4 删除时再
迁移为独立实现）。

**user 状态传递**：自定义 `ScenicDeepAgentState`（DeepAgentState 子类）追加
`user` 字段，对话入口经 `{"messages": ..., "user": user}` 传入，记忆工具经
`InjectedState("user")` 从图状态注入（见 memory_tools.py）。
"""
import logging
import threading
from typing import Any, AsyncIterator, Dict, List, NotRequired, Optional

from deepagents import create_deep_agent
from deepagents.graph import DeepAgentState
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services import chat_agent as main_chain  # 只读复用，不改主链路
from app.services import memory_service
from app.services.deepagents import compaction, tool_cache
from app.services.deepagents.harness import (
    _AGENT_MAX_TOKENS,
    _AGENT_TIMEOUT,
    _MAX_AGENT_STEPS,
    ensure_harness_profile,
)
from app.services.deepagents.memory_tools import (
    MEMORY_STEP_LABELS,
    delete_user_memory,
    list_user_memories,
    recall_user_memory,
    save_user_memory,
)
from app.services.intent_classifier import classify_intent

logger = logging.getLogger(__name__)


# ── 自定义图状态（user 工具注入） ─────────────────────────────────────────


class ScenicDeepAgentState(DeepAgentState):
    """装配层自定义状态：追加 user 字段（记忆工具经 InjectedState 注入）。

    `user` 为当前对话用户标识（登录用户 id / 游客 id / visitor），由对话
    入口从上下文解析后经 ainvoke/astream 输入传入，deepagents 图原样透传，
    记忆四工具经 `InjectedState("user")` 从图状态注入。
    """

    user: NotRequired[str]


# ── 系统提示词与规划指令 ──────────────────────────────────────────────────


def _build_system_prompt() -> str:
    """装配层系统提示词：复用主链路角色/工具原则 + 记忆工具使用原则。"""
    return main_chain.SYSTEM_PROMPT + """

记忆工具使用原则：
1. 长期记忆工具（recall_user_memory / save_user_memory / list_user_memories /
   delete_user_memory）只读写当前用户的记忆，系统已自动隔离用户身份，
   无需向用户询问或让用户指定身份。
2. 用户提及既往表达过的偏好/经历时，优先用 recall_user_memory 检索确认，
   引用真实记忆作答，不要凭空假设。
3. 用户明确表达新的可复用偏好/事实时，可用 save_user_memory 记下；
   不要保存景点百科等一次性的资料性内容。
4. 工具效率：一次检索到足够信息后直接作答，不要对同一主题反复重搜。"""


# 复用主链路系统提示词作为基础（chat_agent 保留期间维持单一来源，
# A4 删除时再迁移为独立实现）
SYSTEM_PROMPT = _build_system_prompt()


# prompt 级规划指令（船长决策：planning=prompt 指令；A1 实测有效——
# 复杂任务注入后回答结构化分节、工具链更完整；代价是 token 与耗时略增，
# 仅复杂意图注入，简单问答零开销）
_PLAN_INSTRUCTION = """本次请求是需要多步骤完成的复杂任务（如行程规划）。请按以下方式处理：
1. 先规划：梳理完成回答所需的信息（景点/路线/知识库/用户记忆）与工具调用顺序，形成明确步骤
2. 再执行：按规划依次调用工具获取真实数据；数据一律以工具返回为准；一次检索到足够信息后直接作答，不要反复重搜
3. 最后总结：用分节的结构化回答（如「行程安排」「适合人群」「注意事项」等小节）组织输出，引用真实名称与数据，不要编造"""

# 步骤展示文案（场景工具与 chat_agent._STEP_LABELS 同源；记忆工具见
# memory_tools.MEMORY_STEP_LABELS）——装配层独立维护，chat_agent 保留期间
# 维持同源同步（A4 删除时再评估合并）
_STEP_LABELS: Dict[str, str] = {
    "search_knowledge": "正在检索知识库…",
    "search_spots": "正在查询景点…",
    "search_routes": "正在查询路线…",
    "get_spot_detail": "正在查询景点详情…",
    "get_route_detail": "正在查询路线详情…",
    "filter_by_tags": "正在筛选路线…",
    "recommend_routes": "正在为您推荐路线…",
}
_STEP_LABELS.update(MEMORY_STEP_LABELS)

# 知识检索工具的调用名（与 chat_agent 一致，RAG 来源提取用）
_KNOWLEDGE_TOOL_NAME = "search_knowledge"


# ── 工具装配 ──────────────────────────────────────────────────────────────

# A4 工具结果归一化缓存（同 query 不重查，见 tool_cache.py）：
# - 静态数据工具（景点/路线/推荐）：长 TTL（默认 1h，数据源为 app/data/ 静态 JSON）
# - 知识检索（search_knowledge）：短 TTL（默认 5min）+ 索引重建版本失效
# 包装仅替换执行函数（同名/同描述/同 args_schema），SSE 契约与 RAG 来源提取不变
_CACHEABLE_TOOLS = frozenset(
    {
        "search_spots",
        "search_routes",
        "get_spot_detail",
        "get_route_detail",
        "filter_by_tags",
        "recommend_routes",
    }
)
_KNOWLEDGE_CACHE_TTL = settings.AGENT_TOOL_CACHE_TTL_KNOWLEDGE
_STATIC_CACHE_TTL = settings.AGENT_TOOL_CACHE_TTL_STATIC


def _select_tools(use_rag: bool) -> List[Any]:
    """装配工具集 = 场景工具（复用主链路选择逻辑）+ 记忆四工具。

    场景工具经 `chat_agent._select_tools` 只读复用（use_rag 决定是否含
    search_knowledge），记忆四工具始终装配（A2 试点目标能力）。

    A4 调优：高频只读场景工具包装为归一化缓存版本（同 query 不重查，
    降工具调用开销与 token 消耗）；记忆工具不缓存（有副作用）。
    """
    tools: List[Any] = main_chain._select_tools(use_rag)
    cached: List[Any] = []
    for t in tools:
        name = getattr(t, "name", "")
        if name == "search_knowledge":
            cached.append(
                tool_cache.wrap_tool(t, ttl_seconds=_KNOWLEDGE_CACHE_TTL)
            )
        elif name in _CACHEABLE_TOOLS:
            cached.append(tool_cache.wrap_tool(t, ttl_seconds=_STATIC_CACHE_TTL))
        else:
            cached.append(t)
    cached.extend(
        [recall_user_memory, save_user_memory, list_user_memories, delete_user_memory]
    )
    return cached


# ── Agent 构建（缓存：构造耗时 ~24s，必须缓存） ───────────────────────────


def _build_agent(*, use_rag: bool, llm: Optional[Any] = None) -> Any:
    """构建 deepagents agent（create_deep_agent，A1 报告 §3 装配方法）。

    - HarnessProfile 已注册（排除内置 9 工具 + 禁用通用子代理）
    - 模型复用 llm.py 工厂（max_tokens 经 extra_body 注入、timeout ≥120s）
    - 显式 state_schema（user 字段供记忆工具注入）
    - recursion_limit 由调用方在 ainvoke/astream 的 config 显式收紧
      （create_deep_agent 硬编码 9999，见 harness.py 常量注释）
    """
    ensure_harness_profile()
    if llm is None:
        llm = get_chat_llm(max_tokens=_AGENT_MAX_TOKENS, timeout=_AGENT_TIMEOUT)
    # A4 调优：单轮工具调用上限（ToolCallLimitMiddleware，langchain 自带）。
    # run_limit=AGENT_MAX_TOOL_CALLS（默认 15，依据 A1 实测：复杂任务平均 9 步、
    # 峰值 13 步）；exit_behavior="continue" 优雅降级——超限工具调用被拦截并
    # 以错误 ToolMessage 回给模型，模型基于已有信息直接作答（不报错、不回落）；
    # 中间件 state schema（工具计数）由 create_deep_agent 自动与自定义
    # ScenicDeepAgentState 合并。上限值与实测依据见 doc/deepagents-tuning.md
    return create_deep_agent(
        model=llm,
        tools=_select_tools(use_rag),
        system_prompt=SYSTEM_PROMPT,
        state_schema=ScenicDeepAgentState,
        middleware=[
            ToolCallLimitMiddleware(
                run_limit=settings.AGENT_MAX_TOOL_CALLS,
                exit_behavior="continue",
            )
        ],
        name="scenic_deep_agent",
    )


_agent_with_rag: Optional[Any] = None
_agent_without_rag: Optional[Any] = None
_build_lock = threading.Lock()


def get_agent(*, use_rag: bool = True, llm: Optional[Any] = None) -> Any:
    """获取 deepagents agent 实例：默认 LLM 走模块级缓存；测试注入 llm 时
    独立构建（与 chat_agent._get_agent 同模式）。"""
    global _agent_with_rag, _agent_without_rag
    if llm is None:
        with _build_lock:
            if use_rag:
                if _agent_with_rag is None:
                    _agent_with_rag = _build_agent(use_rag=True)
                return _agent_with_rag
            if _agent_without_rag is None:
                _agent_without_rag = _build_agent(use_rag=False)
            return _agent_without_rag
    return _build_agent(use_rag=use_rag, llm=llm)


def reset_agent_cache() -> None:
    """重置 agent 缓存（供测试与配置热更新使用）。"""
    global _agent_with_rag, _agent_without_rag
    with _build_lock:
        _agent_with_rag = None
        _agent_without_rag = None


# ── 意图分类双模式挂载 ────────────────────────────────────────────────────


def _is_complex_intent(query: str) -> bool:
    """意图分类（复用 intent_classifier 规则分类）：复杂 → True。

    与 chat_agent 同策略：分类器内部失败默认简单；此处再包一层 try/except
    双保险——任何异常按简单处理（安全降级快速直接作答，对话不中断）。
    """
    try:
        return classify_intent(query) == "complex"
    except Exception:
        logger.warning("意图分类异常（按简单处理）: query=%r", query)
        return False


def _build_messages(
    query: str,
    *,
    history: Optional[List[BaseMessage]] = None,
    complex_intent: bool = False,
    light_summary: str = "",
) -> List[BaseMessage]:
    """组装 agent 输入消息（意图分流 + 轻量摘要注入）。

    - **意图分类双模式**：复杂任务在消息头部注入 `_PLAN_INSTRUCTION`
      （prompt 级规划，A1 实测有效）；简单问答不注入（零开销直接作答）
    - **轻量用户摘要**（昵称/记忆概览，防"忘记调用"记忆工具）：作为首条
      SystemMessage 注入（与摘要本体隔离，见 `_build_light_summary`）

    顺序：摘要 → 规划指令 → 历史 → 当前问题（DeepSeek 兼容端点支持多条
    system 消息，与 chat_agent 记忆注入同机制）。
    """
    messages: List[BaseMessage] = list(history or []) + [HumanMessage(content=query)]
    if complex_intent:
        messages.insert(0, SystemMessage(content=_PLAN_INSTRUCTION))
    if light_summary:
        messages.insert(0, SystemMessage(content=light_summary))
    return messages


# ── 轻量用户摘要（昵称/记忆概览级） ───────────────────────────────────────


async def _fetch_nickname(user: str) -> str:
    """查用户昵称（users 表）；任何失败返回空串（尽力而为，不阻断对话）。

    独立成函数便于测试 monkeypatch（测试环境不连主库）。
    """
    try:
        from sqlalchemy import select

        from app.core.database import async_session_factory
        from app.models.user import User

        uid = int(user)
        async with async_session_factory() as session:
            nickname = (
                await session.execute(
                    select(User.nickname).where(User.id == uid)
                )
            ).scalar_one_or_none()
            return nickname or ""
    except Exception:
        return ""


async def _build_light_summary(user: str) -> str:
    """轻量用户摘要（昵称/记忆概览级，防"忘记调用"记忆工具）。

    与阶段2 的「检索注入」（按当前问题语义检索具体记忆）不同：本摘要只含
    用户标识级概览（昵称 + 记忆条数），每轮对话注入（成本极低），提示模型
    存在记忆并引导其调用记忆工具获取详情。游客/无数据返回空串不注入。
    """
    if memory_service.is_guest_user(user) or not user:
        return ""
    try:
        nickname = await _fetch_nickname(user)
    except Exception as e:
        # 昵称查询异常（含测试注入）：静默降级，不阻断对话
        logger.warning("用户昵称查询失败（摘要降级）: %s", e)
        nickname = ""
    try:
        count = await memory_service.count_user_memory(user)
    except Exception as e:
        logger.warning("用户记忆计数失败（摘要降级）: %s", e)
        count = 0
    if not nickname and not count:
        return ""
    parts: List[str] = []
    if nickname:
        parts.append(f"昵称：{nickname}")
    if count:
        parts.append(f"长期记忆：{count} 条")
    return (
        "当前对话用户画像摘要（系统自动生成，仅作参考）：" + "；".join(parts)
        + "。如需记忆的具体内容，请调用 recall_user_memory / list_user_memories "
        "查询，不要凭空假设用户偏好。"
    )


# ── 对话入口 ──────────────────────────────────────────────────────────────


def _serialize_history_for_store(
    messages: List[BaseMessage],
) -> List[Any]:
    """压缩后历史序列化为 (role, content) 行（SQLite 落盘用）。

    支持 system（摘要 SystemMessage）/ human / ai 三种角色；非字符串
    content 转字符串兜底（本层历史均为纯文本，防御性）。
    """
    rows: List[Any] = []
    for m in messages:
        role = getattr(m, "type", "")
        if role not in ("system", "human", "ai"):
            role = "human"
        content = m.content
        rows.append((role, content if isinstance(content, str) else str(content or "")))
    return rows


async def _maybe_compact_history(
    conversation_id: str,
    llm: Optional[Any] = None,
) -> None:
    """回答后触发：长会话历史超过阈值时压缩早期轮次为摘要（A4 调优）。

    在 `_append_history` 之后调用（回答已完成，压缩不占用用户侧延迟）：
    读取会话历史 → `compaction.maybe_compact`（内置 SummarizationMiddleware
    引擎，阈值/保留窗口见 AGENT_COMPACT_* 配置）→ 压缩成功则写回
    （进程内缓存 + SQLite replace_messages，重启不丢、不重复压缩）。

    降级：任何异常/未达阈值静默跳过（历史保持原样，由消息上限截断兜底），
    绝不阻断对话、不影响 SSE 契约。并发安全：写回前校验快照末条消息未被
    新轮次追加（并发对话时放弃本次压缩，下次触发再压）。
    """
    try:
        with main_chain._conversations_lock:
            history = list(main_chain._conversations.get(conversation_id, []))
        compacted = await compaction.maybe_compact(history, llm)
        if compacted is None:
            return
        # 写回校验：快照后若已有新轮次追加（并发对话），放弃本次压缩
        # （下次超过阈值时再压，避免覆盖新消息）
        with main_chain._conversations_lock:
            current = main_chain._conversations.get(conversation_id)
            if current is None or not current:
                return
            if (
                len(current) != len(history)
                or getattr(current[-1], "content", "") != getattr(history[-1], "content", "")
            ):
                logger.info(
                    "会话压缩写回跳过（并发有新轮次追加）: conversation_id=%s",
                    conversation_id,
                )
                return
            main_chain._conversations[conversation_id] = compacted
            main_chain._conversations.move_to_end(conversation_id)
        # SQLite 落盘（失败仅告警：进程内已生效，库中保持未压缩历史，
        # 语义等价于跳过本次压缩，可接受）
        main_chain._store_call(
            "replace_messages",
            conversation_id,
            _serialize_history_for_store(compacted),
        )
    except Exception as e:
        logger.warning("会话历史压缩失败（跳过，保持原历史）: %s", e)


def _extract_answer(all_messages: List[BaseMessage]) -> str:
    """提取最终回答（最后一条非工具 AI 消息，与 chat_agent 同逻辑）。"""
    for msg in reversed(all_messages):
        if getattr(msg, "type", "") == "ai" and not getattr(msg, "tool_calls", None):
            content = msg.content
            return content if isinstance(content, str) else str(content or "")
    return ""


async def chat(
    query: str,
    *,
    user: str = "visitor",
    conversation_id: str = "",
    use_rag: bool = True,
    rag_top_k: int = 3,
    history: Optional[List[BaseMessage]] = None,
    agent: Optional[Any] = None,
    llm: Optional[Any] = None,
) -> Dict[str, Any]:
    """deepagents 装配层非流式对话（A3 主链路）。

    **A3 会话管理**：conversation_id 空串则新建（生成 uuid 并 SQLite 落盘）；
    非空则从进程内缓存 / SQLite 懒恢复历史（重启可恢复），多轮上下文
    组装进 messages（历史 + 当前问题）；对话完成后追加历史（双写）并
    调度长期记忆提取钩子（与 chat_agent 主链路行为一致）。

    返回 {"answer", "conversation_id", "rag_context", "rag_sources",
    "intent"}；intent 为意图分流结果（complex/simple，观测用）。

    Args:
        query: 用户消息
        user: 用户标识（登录用户 id / 游客 id / visitor）
        conversation_id: 会话 ID；空串则新建会话（生成新 id 并返回）
        use_rag: 是否启用知识检索工具 search_knowledge
        rag_top_k: RAG 检索结果数（同时用于响应中 rag_sources 透出）
        history: 显式传入的多轮上下文（测试/调用方注入用；不传则从
            会话存储读取，二者不可混用——传了 history 时仍会按
            conversation_id 做会话持久化）
        agent: 注入的 agent 实例（测试用）
        llm: 注入的 LLM（测试用，独立构建不缓存）

    Raises:
        LLMConfigError / 网络 / API 异常：由路由层捕获并自动回落 Dify
    """
    # 1. 会话管理（A3：进程内缓存 → SQLite 懒恢复；新建生成 id 并落盘）
    conversation_id = main_chain._get_or_create_conversation(conversation_id, user)
    if history is None:
        with main_chain._conversations_lock:
            history = list(main_chain._conversations.get(conversation_id, []))

    # 2. 意图分流 + 轻量摘要注入 + 消息组装（多轮上下文 + 当前问题）
    complex_intent = _is_complex_intent(query)
    light_summary = await _build_light_summary(user)
    messages = _build_messages(
        query,
        history=history,
        complex_intent=complex_intent,
        light_summary=light_summary,
    )
    agent = agent or get_agent(use_rag=use_rag, llm=llm)
    result = await agent.ainvoke(
        {"messages": messages, "user": user},
        config={"recursion_limit": _MAX_AGENT_STEPS},
    )
    all_messages: List[BaseMessage] = result.get("messages", [])
    answer = _extract_answer(all_messages)
    rag_context, rag_sources = main_chain._extract_rag_info(all_messages, rag_top_k)

    # 3. 会话持久化 + 长期记忆钩子（与 chat_agent 主链路一致：先落盘后返回）
    main_chain._append_history(conversation_id, query, answer, user)
    # A4 调优：回答后触发上下文压缩（长会话历史超过阈值时压缩早期轮次
    # 为摘要；未达阈值/失败静默跳过，见 _maybe_compact_history）
    await _maybe_compact_history(conversation_id, llm)
    main_chain._schedule_memory_extraction(user, conversation_id, query, answer)
    logger.info(
        "deepagents 对话完成: intent=%s user=%s conversation=%s tools=%d answer_len=%d",
        "complex" if complex_intent else "simple",
        user,
        conversation_id,
        len([m for m in all_messages if getattr(m, "type", "") == "tool"]),
        len(answer),
    )
    return {
        "answer": answer,
        "conversation_id": conversation_id,
        "rag_context": rag_context,
        "rag_sources": rag_sources,
        "intent": "complex" if complex_intent else "simple",
    }


async def chat_stream(
    query: str,
    *,
    user: str = "visitor",
    conversation_id: str = "",
    use_rag: bool = True,
    rag_top_k: int = 3,
    history: Optional[List[BaseMessage]] = None,
    agent: Optional[Any] = None,
    llm: Optional[Any] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """deepagents 装配层流式对话（SSE 事件源，A3 主链路）。

    **A3 会话管理**：与 chat() 一致——conversation_id 空串新建（生成 uuid
    并落盘），非空则恢复历史；done 事件携带 conversation_id（客户端续聊
    回传）；历史追加与记忆钩子调度均在 done 事件**之前**（客户端收到 done
    即关闭连接也不丢上下文/不丢记忆提取，review F1 2026-08-08 加固）。

    A1 报告 §5 契约映射（与 ADH 前端契约一致，前端零改动）：

    - `step`：messages 模式 AIMessageChunk 的 `tool_call_chunks`（按调用 id
      去重，与 chat_agent 现有实现机制相同）；label 取 _STEP_LABELS
    - `token`：AI chunk 内容增量；**保留 suppress_tokens 语义**——模型在
      调用工具前会先输出正文 token，工具调用消息的内联 content 必须抑制
      （A1 实测必现，否则前端渲染工具调用前的碎文本）
    - `done`：流结束事件（conversation_id 透出；rag_context/rag_sources
      从 search_knowledge 的 ToolMessage 提取，与 chat() 契约一致）

    事件契约与现有 SSE 兼容：{"type": "step"|"token"|"done", ...}，
    done 额外携带 intent 字段（分流结果，观测用）。

    Args:
        query: 用户消息
        user: 用户标识（登录用户 id / 游客 id / visitor）
        conversation_id: 会话 ID；空串则新建会话（生成新 id，done 透出）
        use_rag: 是否启用知识检索工具 search_knowledge
        rag_top_k: RAG 检索结果数（同时用于响应中 rag_sources 透出）
        history: 显式传入的多轮上下文（测试/调用方注入用；不传则从
            会话存储读取）
        agent: 注入的 agent 实例（测试用）
        llm: 注入的 LLM（测试用，独立构建不缓存）

    Raises:
        LLMConfigError / 网络 / API 异常：由调用方捕获并回落（同上）
    """
    # 1. 会话管理（A3：进程内缓存 → SQLite 懒恢复；新建生成 id 并落盘）
    conversation_id = main_chain._get_or_create_conversation(conversation_id, user)
    if history is None:
        with main_chain._conversations_lock:
            history = list(main_chain._conversations.get(conversation_id, []))

    # 2. 意图分流 + 轻量摘要注入 + 消息组装
    complex_intent = _is_complex_intent(query)
    light_summary = await _build_light_summary(user)
    messages = _build_messages(
        query,
        history=history,
        complex_intent=complex_intent,
        light_summary=light_summary,
    )
    agent = agent or get_agent(use_rag=use_rag, llm=llm)

    seen_call_ids: "set[str]" = set()
    # 当前模型消息是否为工具调用消息：是则抑制其内联 content 作为回答输出
    # （工具调用消息拆分为多个 chunk，须用消息级布尔标志而非计数，
    # 与 chat_agent 相同；A1 实测模型在调用工具前会输出正文 token，必须抑制）
    suppress_tokens = False
    rag_tool_messages: List[BaseMessage] = []  # search_knowledge 的 ToolMessage
    answer_parts: List[str] = []

    async for chunk, _meta in agent.astream(
        {"messages": messages, "user": user},
        stream_mode="messages",
        config={"recursion_limit": _MAX_AGENT_STEPS},
    ):
        if isinstance(chunk, AIMessageChunk):
            tool_calls = chunk.tool_call_chunks or []
            if tool_calls:
                suppress_tokens = True
                for call in tool_calls:
                    call_id = call.get("id")
                    name = call.get("name")
                    if name and call_id and call_id not in seen_call_ids:
                        seen_call_ids.add(call_id)
                        yield {
                            "type": "step",
                            "tool": name,
                            "label": _STEP_LABELS.get(
                                name, f"正在调用工具 {name}…"
                            ),
                        }
            text = chunk.content
            if not suppress_tokens and isinstance(text, str) and text:
                answer_parts.append(text)
                yield {"type": "token", "text": text}
        elif getattr(chunk, "type", "") == "tool":
            # 工具执行结果（deepagents messages 流模式亦产出 ToolMessage）
            suppress_tokens = False
            if getattr(chunk, "name", "") == _KNOWLEDGE_TOOL_NAME:
                rag_tool_messages.append(chunk)

    # 3. 会话持久化 + 长期记忆钩子（done 前调度：客户端收到 done 即关闭
    #    连接也不丢上下文/不丢记忆提取；review F1 2026-08-08——yield done
    #    后调度存在 GeneratorExit 跳过窗口，改为 done 前更稳妥）
    answer = "".join(answer_parts)
    main_chain._append_history(conversation_id, query, answer, user)
    # A4 调优：回答后触发上下文压缩（与 chat() 一致；done 前完成，
    # 客户端收到 done 即关闭连接也不丢压缩结果）
    await _maybe_compact_history(conversation_id, llm)
    main_chain._schedule_memory_extraction(user, conversation_id, query, answer)
    rag_context, rag_sources = main_chain._extract_rag_info(
        rag_tool_messages, rag_top_k
    )
    logger.info(
        "deepagents 对话完成(流式): intent=%s user=%s conversation=%s steps=%d answer_len=%d",
        "complex" if complex_intent else "simple",
        user,
        conversation_id,
        len(seen_call_ids),
        len(answer),
    )
    yield {
        "type": "done",
        "conversation_id": conversation_id,
        "rag_context": rag_context,
        "rag_sources": rag_sources,
        "intent": "complex" if complex_intent else "simple",
    }
