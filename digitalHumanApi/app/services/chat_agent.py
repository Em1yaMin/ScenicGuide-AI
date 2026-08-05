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
  阶段1 不持久化，阶段2 再接入用户长期记忆）
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
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool

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
# 知识检索工具的调用名（用于从 agent 返回中提取 RAG 来源）
_KNOWLEDGE_TOOL_NAME = "search_knowledge"


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


# ── 进程内会话存储（阶段1：不持久化） ─────────────────────────────────────

_conversations: "OrderedDict[str, List[BaseMessage]]" = OrderedDict()
_conversations_lock = threading.Lock()
# 本模块生成的会话 ID 集合（仅 uuid.uuid4().hex 生成时登记）：
# 判定「该 id 对 Dify 未知」用，不随 LRU 淘汰，也不登记客户端传入/复用的 id
_generated_conversation_ids: "set[str]" = set()


def _evict_locked() -> None:
    """LRU 淘汰最久未活跃会话（须在持有 _conversations_lock 时调用）。"""
    while len(_conversations) > _MAX_CONVERSATIONS:
        oldest_id, _ = _conversations.popitem(last=False)
        logger.info("会话数超限，淘汰最久未活跃会话: conversation_id=%s", oldest_id)


def _get_or_create_conversation(conversation_id: str) -> str:
    """按 conversation_id 取会话；空 id 或未知 id 时新建并返回有效 id。"""
    with _conversations_lock:
        if not conversation_id or conversation_id not in _conversations:
            if not conversation_id:
                conversation_id = uuid.uuid4().hex
                _generated_conversation_ids.add(conversation_id)
            _conversations[conversation_id] = []
            _evict_locked()
            logger.info("新建对话会话: conversation_id=%s", conversation_id)
        else:
            _conversations.move_to_end(conversation_id)
        return conversation_id


def _append_history(conversation_id: str, query: str, answer: str) -> None:
    """追加一轮问答到会话历史，超限丢弃最早消息。"""
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


def clear_conversation(conversation_id: str) -> None:
    """清除指定会话历史（供测试与未来管理接口使用）。"""
    with _conversations_lock:
        _conversations.pop(conversation_id, None)


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
        user: 用户标识（登录用户 id / 游客 id / visitor；阶段1 仅用于日志，
            阶段2 接入用户长期记忆时使用）
        conversation_id: 会话 ID；空串则新建会话（生成新 id 并返回）
        use_rag: 是否启用知识检索工具 search_knowledge
        rag_top_k: RAG 检索结果数（同时用于响应中 rag_sources 透出）

    Returns:
        ChatAgentResult(answer, conversation_id, rag_context, rag_sources)

    Raises:
        LLMConfigError / 网络 / API 异常：由路由层捕获并自动回落 Dify
    """
    # 1. 会话管理（进程内）
    conversation_id = _get_or_create_conversation(conversation_id)
    with _conversations_lock:
        history = list(_conversations.get(conversation_id, []))

    # 2. 调用 agent（历史 + 当前消息；create_agent 内部循环执行工具）
    agent = _get_agent(use_rag=use_rag)
    result = await agent.ainvoke({"messages": history + [HumanMessage(content=query)]})
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

    # 5. 追加历史（进程内，阶段2 再做持久化记忆）
    _append_history(conversation_id, query, answer)

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
