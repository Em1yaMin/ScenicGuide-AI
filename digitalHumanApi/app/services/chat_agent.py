"""LangChain 对话 Agent（阶段1 T2：对话主链路 Dify → LangChain agent）。

双轨开关见 `app/routers/dialogue.py`：`DIALOGUE_ENGINE=langchain`（默认）走本
模块，异常自动回落 Dify；`DIALOGUE_ENGINE=dify` 强制走原 Dify 链路。

设计要点（船长决策 2026-08-05）：

- **结构化数据零幻觉**：景点/路线信息由 `search_spots` / `search_routes` /
  `get_spot_detail` / `get_route_detail` / `filter_by_tags` 等结构化工具直查
  本地 JSON（复用 recommend_agent 的工具，不走向量）
- **知识检索统一入口**：`search_knowledge` 复用 recommend_agent 的同步包装
  （rag_service._search_documents_sync，默认 knowledge+documents 命名空间）
- **推荐能力**：`recommend_routes` 包装 recommend_agent.recommend（规则引擎
  评分 + 结构化直查），供 agent 在识别到游览偏好时调用
- **多轮上下文**：进程内会话（conversation_id → 消息历史 dict，线程锁保护；
  阶段2 T1 起短期记忆持久化：SQLite 落盘 + 进程内 TTL 缓存，重启可恢复，
  详见下方「进程内会话存储」节；长期记忆为后续阶段）
- **安全边界**：预订/账号/支付等操作类需求不提供工具、永不自动办理，
  system prompt 要求 agent 引导游客联系人工客服

模型接入走 `app/core/llm.py` 工厂（LLM_API_* 主端点 / LLM_FALLBACK_* 备用，
主端点缺 key 自动降级；max_tokens 经 extra_body 注入保证 DeepSeek 系端点
长度限制生效；reasoning_content 由 langchain-openai 自动剥离）。
"""
import asyncio
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from langchain_core.tools import tool

from app.core import memory_store
from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services import recommend_agent
from app.services.rag_service import build_rag_context

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────────

# 系统提示词：角色 + 工具使用原则 + 安全边界
SYSTEM_PROMPT = """你是「景区导览」的 AI 数字人助手，运行在景区导览系统后端。请始终用中文、亲切友好地回答问题。

工具使用原则：
1. 景点信息（名称、描述、标签、时长、难度、亮点等）用 search_spots / get_spot_detail 直接查询；路线信息（名称、标签、适合人群、难度、时长等）用 search_routes / get_route_detail / filter_by_tags 直接查询。结构化数据一律以工具返回为准，禁止凭空编造景点或路线。
2. 人文典故、讲解词、FAQ、开放时间等背景知识用 search_knowledge 检索本地知识库。
3. 游客表达游览偏好（如「带老人小孩」「喜欢拍照」「半日游」）时，用 recommend_routes 生成个性化路线推荐。
4. 工具查不到的信息，诚实告知「暂时没有相关资料」，不要编造。
5. 安全边界：预订、购票、账号、支付等操作类需求本助手不直接办理，需人工确认——礼貌说明并引导游客联系人工客服。"""

# 对话输出上限（token）：经 llm.py 工厂 extra_body 注入，DeepSeek 系端点生效
_CHAT_MAX_TOKENS = 2048
# LLM 调用超时（秒），与 Dify 兜底路径的 60s 超时一致，挂起时尽快触发回落
_CHAT_TIMEOUT = 60
# 进程内会话保留的最大消息数（超限丢弃最早的，按 20 条 ≈ 10 轮）
_MAX_HISTORY_MESSAGES = 20
# 进程内会话总数上限（超限 LRU 淘汰最久未活跃会话，防长跑进程内存无限增长）
_MAX_CONVERSATIONS = 1000
# agent 图执行步数上限：langchain 1.3 的 create_agent 把 recursion_limit
# 硬编码为 9999，异常 LLM 反复请求工具时会循环执行近万次才报错——对话场景
# 必须显式收紧，超限抛 GraphRecursionError 由路由层快速回落 Dify（不挂起）
_MAX_AGENT_STEPS = 25
# 知识检索工具的调用名（用于从 agent 返回中提取 RAG 来源）
_KNOWLEDGE_TOOL_NAME = "search_knowledge"

# 工具名 → 步骤展示文案（SSE step 事件透出到前端，思考过程可视化）
_STEP_LABELS: Dict[str, str] = {
    "search_knowledge": "正在检索知识库…",
    "search_spots": "正在查询景点…",
    "search_routes": "正在查询路线…",
    "get_spot_detail": "正在查询景点详情…",
    "get_route_detail": "正在查询路线详情…",
    "filter_by_tags": "正在筛选路线…",
    "recommend_routes": "正在为您推荐路线…",
}


# ── 推荐工具（包装 recommend_agent.recommend 供 agent 调用） ─────────────


@tool
def recommend_routes(preference: str) -> str:
    """根据游客偏好描述推荐游览路线。

    preference 是游客的偏好描述原文（如「喜欢自然风光，带老人小孩，想拍照打卡」）。
    返回推荐路线 JSON（含路线 id/名称/描述/景点列表/标签/匹配分/推荐理由）与
    综合推荐说明。"""
    try:
        # agent 工具运行在线程池（langgraph 经 executor 执行同步工具），
        # 线程内无事件循环，asyncio.run 安全
        response = asyncio.run(recommend_agent.recommend(preference))
        return response.model_dump_json(ensure_ascii=False)
    except Exception as e:
        logger.warning("recommend_routes 工具调用失败: %s", e)
        return json.dumps(
            {"message": "路线推荐服务暂时不可用，请稍后再试"}, ensure_ascii=False
        )


# ── 工具集（复用 recommend_agent 的结构化直查与知识检索） ────────────────

# 全部工具：结构化直查（零幻觉）+ 知识检索（统一入口）+ 规则推荐
_TOOLS: List[Any] = [
    recommend_agent.search_knowledge,   # 统一检索入口（knowledge+documents）
    recommend_agent.search_spots,       # 景点结构化直查
    recommend_agent.search_routes,      # 路线结构化直查
    recommend_agent.get_spot_detail,    # 景点详情
    recommend_agent.get_route_detail,   # 路线详情（含景点列表）
    recommend_agent.filter_by_tags,     # 标签筛选路线
    recommend_routes,                   # 规则引擎推荐（可复用 recommend 逻辑）
]


# ── 进程内会话存储（阶段2 T1：进程内 TTL 缓存 + SQLite 双写持久化） ────
#
# 短期记忆持久化方案（与 app/core/memory_store.py 配套）：
# - 读走缓存、写双写：`_conversations` 为热缓存（LRU 上限 _MAX_CONVERSATIONS），
#   每次追加同步落盘 SQLite（独立文件 memory_store.db），崩溃无丢失窗口；
#   SQLite 写失败仅告警降级为进程内记忆，不阻断对话
# - 懒恢复：缓存未命中时从 SQLite 读回该会话历史（重启后同 conversation_id
#   续聊可恢复多轮上下文），避免启动全量加载；LRU 淘汰只出缓存、不出库
#   （_MAX_CONVERSATIONS 内存上限语义不变，SQLite 数据保留至 TTL 过期）
# - TTL 滑动窗口：`_conversation_meta` 记录每会话过期时间（每次追加刷新），
#   过期会话访问时惰性清理；后台任务（app/tasks/memory_cleanup.py）定期
#   清理 SQLite 过期行 + 驱逐进程内过期缓存
# - `_generated_conversation_ids` 归属判定随 source 持久化：重启恢复时
#   仅对本模块生成的 id（source=langchain）重新登记，双轨回落行为不变

_conversations: "OrderedDict[str, List[BaseMessage]]" = OrderedDict()
_conversations_lock = threading.Lock()


@dataclass
class _SessionMeta:
    """会话元数据（进程内，与 SQLite 行对应）。"""
    expires_at: float  # 过期时间（unix 秒），滑动窗口 TTL
    user: str          # 最近活跃用户标识（登录用户 id / 游客 id / visitor）
    source: str        # memory_store.SOURCE_LANGCHAIN（本模块生成）| SOURCE_CLIENT


# conversation_id → 会话元数据（TTL 惰性过期检查用；与 SQLite ttl_expires_at 一致）
_conversation_meta: "Dict[str, _SessionMeta]" = {}
# 本模块生成的会话 ID 集合（仅 uuid.uuid4().hex 生成时登记）：
# 判定「该 id 对 Dify 未知」用，不随 LRU 淘汰，也不登记客户端传入/复用的 id
_generated_conversation_ids: "set[str]" = set()


def _now_ts() -> float:
    return time.time()


def _expires_at() -> float:
    """当前时间的会话过期时间（TTL 取存储层配置，存储不可用时降级默认值）。"""
    ttl = None
    try:
        ttl = memory_store.get_store().ttl_seconds()
    except Exception:
        logger.warning("短期记忆存储不可用，按配置默认 TTL 计算过期时间")
    if ttl is None:
        ttl = int(settings.MEMORY_TTL_DAYS * 86400)
    return _now_ts() + ttl


def _store_call(name: str, *args: Any, **kwargs: Any) -> Any:
    """SQLite 持久化调用包装：失败仅告警，记忆降级为进程内，不阻断对话。"""
    try:
        return getattr(memory_store.get_store(), name)(*args, **kwargs)
    except Exception as e:
        logger.warning(
            "短期记忆持久化失败（对话继续，降级为进程内记忆）: op=%s err=%s",
            name,
            e,
        )
        return None


def _deserialize_history(seq: List[Dict[str, Any]]) -> List[BaseMessage]:
    """SQLite 消息序列 → BaseMessage 历史列表（忽略未知 role，防御性）。"""
    history: List[BaseMessage] = []
    for item in seq:
        role = item.get("role")
        content = item.get("content", "")
        if role == "human":
            history.append(HumanMessage(content=content))
        elif role == "ai":
            history.append(AIMessage(content=content))
    return history


def _evict_locked() -> None:
    """LRU 淘汰最久未活跃会话（须在持有 _conversations_lock 时调用）。

    LRU 淘汰仅移出进程内缓存，SQLite 落盘数据保留（短期记忆持久化），
    下次访问该 id 时从库中懒恢复；_MAX_CONVERSATIONS 内存上限语义不变。
    """
    while len(_conversations) > _MAX_CONVERSATIONS:
        oldest_id, _ = _conversations.popitem(last=False)
        _conversation_meta.pop(oldest_id, None)
        logger.info("会话数超限，淘汰最久未活跃会话: conversation_id=%s", oldest_id)


def _get_or_create_conversation(
    conversation_id: str, user: str = "visitor"
) -> str:
    """按 conversation_id 取会话（进程内缓存 → SQLite 懒恢复）；空 id /
    过期 / 未知 id 时新建并返回有效 id（新建会话同步落盘）。

    阶段2 T1 起接入短期记忆持久化：重启后同 conversation_id 续聊可从
    SQLite 恢复历史消息；新建会话写双写（缓存 + 落盘），TTL 滑动窗口。
    """
    with _conversations_lock:
        now = _now_ts()
        # 1. 缓存命中且未过期 → 直接复用
        if conversation_id and conversation_id in _conversations:
            meta = _conversation_meta.get(conversation_id)
            if meta is not None and meta.expires_at > now:
                _conversations.move_to_end(conversation_id)
                return conversation_id
            # 过期：从缓存驱逐（惰性清理），走下方恢复/新建
            logger.info(
                "会话已过期（惰性清理）: conversation_id=%s", conversation_id
            )
            _conversations.pop(conversation_id, None)
            _conversation_meta.pop(conversation_id, None)

        # 2. SQLite 懒恢复（重启后续聊不丢上下文）
        if conversation_id:
            session = _store_call("load_session", conversation_id, now)
            if session:
                history = _deserialize_history(session["messages"])
                _conversations[conversation_id] = history
                _conversation_meta[conversation_id] = _SessionMeta(
                    expires_at=session["ttl_expires_at"],
                    user=session["user"],
                    source=session["source"],
                )
                if session["source"] == memory_store.SOURCE_LANGCHAIN:
                    # 归属判定随 source 恢复：重启后该 id 对 Dify 依旧未知
                    _generated_conversation_ids.add(conversation_id)
                _conversations.move_to_end(conversation_id)
                logger.info(
                    "从 SQLite 恢复会话: conversation_id=%s user=%s msgs=%d",
                    conversation_id,
                    session["user"],
                    len(history),
                )
                return conversation_id

        # 3. 新建（空 id 生成 uuid；同步落盘，source 区分归属）
        generated = False
        if not conversation_id:
            conversation_id = uuid.uuid4().hex
            generated = True
            _generated_conversation_ids.add(conversation_id)
        source = (
            memory_store.SOURCE_LANGCHAIN if generated else memory_store.SOURCE_CLIENT
        )
        _conversations[conversation_id] = []
        _conversation_meta[conversation_id] = _SessionMeta(
            expires_at=_expires_at(), user=user, source=source
        )
        _evict_locked()
        _store_call("create_session", conversation_id, user=user, source=source)
        logger.info(
            "新建对话会话: conversation_id=%s user=%s", conversation_id, user
        )
        return conversation_id


def _append_history(
    conversation_id: str, query: str, answer: str, user: str = "visitor"
) -> None:
    """追加一轮问答到会话历史（进程内 + SQLite 双写），超限丢弃最早消息。"""
    source = memory_store.SOURCE_CLIENT
    with _conversations_lock:
        history = _conversations.get(conversation_id)
        if history is None:
            history = []
            _conversations[conversation_id] = history
            _evict_locked()
        else:
            _conversations.move_to_end(conversation_id)
        history.append(HumanMessage(content=query))
        history.append(AIMessage(content=answer))
        if len(history) > _MAX_HISTORY_MESSAGES:
            del history[: len(history) - _MAX_HISTORY_MESSAGES]
        meta = _conversation_meta.get(conversation_id)
        if meta is None:
            # 防御：缓存有历史但元数据缺失（理论上不发生），按新建补齐
            meta = _SessionMeta(expires_at=_expires_at(), user=user, source=source)
            _conversation_meta[conversation_id] = meta
        else:
            meta.expires_at = _expires_at()  # TTL 滑动窗口（每次对话刷新）
            meta.user = user
        source = meta.source

    # 写双写：SQLite 落盘（失败仅告警，不阻断对话）
    ok = _store_call(
        "append_messages",
        conversation_id,
        [("human", query), ("ai", answer)],
        _MAX_HISTORY_MESSAGES,
        user=user,
    )
    if ok is False:
        # 会话在库中缺失/已过期（正常路径不会发生，防御性重建后重试）
        _store_call("create_session", conversation_id, user=user, source=source)
        _store_call(
            "append_messages",
            conversation_id,
            [("human", query), ("ai", answer)],
            _MAX_HISTORY_MESSAGES,
            user=user,
        )


def clear_conversation(conversation_id: str) -> None:
    """清除指定会话历史（进程内缓存 + SQLite，供测试与未来管理接口使用）。"""
    with _conversations_lock:
        _conversations.pop(conversation_id, None)
        _conversation_meta.pop(conversation_id, None)
    _store_call("delete_session", conversation_id)


def evict_expired_conversations(now: Optional[float] = None) -> int:
    """驱逐进程内已过期的会话缓存（供后台清理任务调用）。返回驱逐数。

    与 SQLite 侧 cleanup_expired 配套：库删过期行、缓存驱过期会话，
    双保险保证 TTL 过期后同 id 续聊按新会话处理。
    """
    if now is None:
        now = _now_ts()
    evicted = 0
    with _conversations_lock:
        for cid in list(_conversation_meta.keys()):
            if _conversation_meta[cid].expires_at <= now:
                _conversations.pop(cid, None)
                _conversation_meta.pop(cid, None)
                evicted += 1
    if evicted:
        logger.info("清理过期会话缓存: evicted=%d", evicted)
    return evicted


def has_conversation(conversation_id: str) -> bool:
    """判断会话 ID 是否由本模块（LangChain 链路）生成。

    供路由层双轨回落使用：LangChain 生成的 uuid 会话 ID 对 Dify 未知
    （透传会触发 400 conversation_not_exist），需先经此判断决定是否
    移交空 ID 让 Dify 新建会话；Dify 自己生成的 id 不会登记，回落时
    原样透传以延续 Dify 侧多轮上下文。
    """
    if not conversation_id:
        return False
    with _conversations_lock:
        return conversation_id in _generated_conversation_ids


# ── Agent 构建（缓存） ────────────────────────────────────────────────────


def _build_agent(*, use_rag: bool, llm: Optional[Any] = None) -> Any:
    """构建 LangChain tool-calling agent（create_agent，langchain 1.x 状态图）。"""
    if llm is None:
        llm = get_chat_llm(max_tokens=_CHAT_MAX_TOKENS, timeout=_CHAT_TIMEOUT)
    tools = list(_TOOLS) if use_rag else [
        t for t in _TOOLS if t.name != _KNOWLEDGE_TOOL_NAME
    ]
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        name="scenic_chat_agent",
    )


_agent_with_rag: Optional[Any] = None
_agent_without_rag: Optional[Any] = None
_build_lock = threading.Lock()


def _get_agent(*, use_rag: bool, llm: Optional[Any] = None) -> Any:
    """获取 agent 实例：默认 LLM 走模块级缓存；测试注入 llm 时独立构建。"""
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


# ── 对话入口 ──────────────────────────────────────────────────────────────


@dataclass
class ChatAgentResult:
    """LangChain 对话链路结果（与 ChatResponse 字段兼容）。"""
    answer: str
    conversation_id: str
    rag_context: str = ""
    rag_sources: List[Dict[str, Any]] = field(default_factory=list)


def _extract_rag_info(
    messages: List[BaseMessage], top_k: int
) -> Tuple[str, List[Dict[str, Any]]]:
    """从 agent 返回消息中提取最后一次 search_knowledge 调用的检索结果。

    Returns:
        (rag_context, rag_sources)：rag_context 为拼接的上下文文本
        （build_rag_context 格式，调试用）；rag_sources 为来源列表
        （source/score/namespace，与现有 ChatResponse 契约一致）。
    """
    for msg in reversed(messages):
        if (
            getattr(msg, "type", "") == "tool"
            and getattr(msg, "name", "") == _KNOWLEDGE_TOOL_NAME
        ):
            try:
                data = json.loads(msg.content)
            except (json.JSONDecodeError, TypeError):
                logger.warning("search_knowledge 工具返回非 JSON，跳过来源提取")
                continue
            if not isinstance(data, list):
                continue
            top = data[:top_k]
            rag_context = build_rag_context(top)
            rag_sources = [
                {
                    "source": r.get("source", ""),
                    "score": r.get("score", 0.0),
                    "namespace": r.get("namespace", ""),
                }
                for r in top
            ]
            return rag_context, rag_sources
    return "", []


async def chat(
    query: str,
    *,
    user: str = "visitor",
    conversation_id: str = "",
    use_rag: bool = True,
    rag_top_k: int = 3,
) -> ChatAgentResult:
    """LangChain 对话主链路：工具调用 agent + 进程内多轮上下文。

    Args:
        query: 用户消息
        user: 用户标识（登录用户 id / 游客 id / visitor；写入短期记忆会话的
            user 字段，登录用户与游客均享短期记忆）
        conversation_id: 会话 ID；空串则新建会话（生成新 id 并返回，SQLite 落盘）
        use_rag: 是否启用知识检索工具 search_knowledge
        rag_top_k: RAG 检索结果数（同时用于响应中 rag_sources 透出）

    Returns:
        ChatAgentResult(answer, conversation_id, rag_context, rag_sources)

    Raises:
        LLMConfigError / 网络 / API 异常：由路由层捕获并自动回落 Dify
    """
    # 1. 会话管理（进程内缓存 → SQLite 懒恢复；新建同步落盘）
    conversation_id = _get_or_create_conversation(conversation_id, user)
    with _conversations_lock:
        history = list(_conversations.get(conversation_id, []))

    # 2. 调用 agent（历史 + 当前消息；create_agent 内部循环执行工具）
    agent = _get_agent(use_rag=use_rag)
    result = await agent.ainvoke(
        {"messages": history + [HumanMessage(content=query)]},
        config={"recursion_limit": _MAX_AGENT_STEPS},
    )
    all_messages: List[BaseMessage] = result.get("messages", [])

    # 3. 提取最终回答（最后一条非工具消息）
    answer = ""
    for msg in reversed(all_messages):
        if getattr(msg, "type", "") == "ai" and not getattr(msg, "tool_calls", None):
            content = msg.content
            answer = content if isinstance(content, str) else str(content or "")
            break

    # 4. 提取 RAG 来源（search_knowledge 工具真实检索结果，透出到响应）
    rag_context, rag_sources = _extract_rag_info(all_messages, rag_top_k)

    # 5. 追加历史（进程内 + SQLite 双写，短期记忆持久化）
    _append_history(conversation_id, query, answer, user)

    logger.info(
        "对话完成: engine=langchain user=%s conversation=%s tools=%d answer_len=%d",
        user,
        conversation_id,
        len([m for m in all_messages if getattr(m, "type", "") == "tool"]),
        len(answer),
    )
    return ChatAgentResult(
        answer=answer,
        conversation_id=conversation_id,
        rag_context=rag_context,
        rag_sources=rag_sources,
    )


async def chat_stream(
    query: str,
    *,
    user: str = "visitor",
    conversation_id: str = "",
    use_rag: bool = True,
    rag_top_k: int = 3,
) -> AsyncIterator[Dict[str, Any]]:
    """LangChain 流式对话（SSE 事件源，阶段1 T3）。

    经 `agent.astream(stream_mode="messages")` 逐块产出 LLM 输出：

    - AIMessageChunk：携带 `tool_call_chunks` 的块 → step 事件（工具调用开始）；
      携带内容增量的块 → token 事件（回答逐字输出；DeepSeek 思考块为空串，
      langchain-openai 已剥离 reasoning_content，天然不会输出思考过程）
    - ToolMessage（type="tool"）：工具执行结果 → 收集 search_knowledge 的
      结果，供 done 事件的 rag_context/rag_sources 透出（与 chat() 契约一致）

    Yields（事件 dict，路由层序列化为 SSE data 帧）：

        {"type": "step", "tool": str, "label": str}   工具调用开始（按调用 id 去重）
        {"type": "token", "text": str}                回答增量
        {"type": "done", "conversation_id": str,
         "rag_context": str, "rag_sources": list}     流式完成

    Raises:
        LLMConfigError / 网络 / API 异常：由路由层捕获并自动回落 Dify
    """
    # 1. 会话管理（进程内缓存 → SQLite 懒恢复；新建同步落盘，与 chat() 一致）
    conversation_id = _get_or_create_conversation(conversation_id, user)
    with _conversations_lock:
        history = list(_conversations.get(conversation_id, []))

    # 2. 流式调用 agent（多轮上下文 + 当前消息）
    agent = _get_agent(use_rag=use_rag)
    seen_call_ids: "set[str]" = set()
    # 当前模型消息是否为工具调用消息：是则抑制其内联 content 作为回答输出
    # （工具调用消息拆分为多个 chunk，须用消息级布尔标志而非计数）
    suppress_tokens = False
    rag_tool_messages: List[BaseMessage] = []  # search_knowledge 的 ToolMessage
    answer_parts: List[str] = []

    async for chunk, _meta in agent.astream(
        {"messages": history + [HumanMessage(content=query)]},
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
            # 工具执行结果（langgraph 1.2 的 messages 流模式亦产出 ToolMessage）
            suppress_tokens = False
            if getattr(chunk, "name", "") == _KNOWLEDGE_TOOL_NAME:
                rag_tool_messages.append(chunk)

    # 3. 追加历史（进程内 + SQLite 双写，短期记忆持久化）
    answer = "".join(answer_parts)
    _append_history(conversation_id, query, answer, user)

    # 4. RAG 来源透出（最后一次 search_knowledge 调用，与 chat() 一致）
    rag_context, rag_sources = _extract_rag_info(rag_tool_messages, rag_top_k)
    logger.info(
        "对话完成(流式): engine=langchain user=%s conversation=%s steps=%d answer_len=%d",
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
    }
