"""管理员 agent 装配层（批次6 B4，deepagents 管理端变体）。

复用 A2/A3 装配模式（app/services/deepagents/）：

- **HarnessProfile**：直接复用 `deepagents.harness.ensure_harness_profile`
  （同一 openai provider 注册、排除内置 9 工具、禁用通用子代理——管理
  agent 同样不需要文件系统/shell/子代理能力，零系统能力面）
- **装配常量**：复用 `_MAX_AGENT_STEPS`（recursion_limit 显式收紧，防
  病态循环）/ `_AGENT_TIMEOUT` / `_AGENT_MAX_TOKENS`（llm.py 工厂
  extra_body 注入原生 max_tokens，DeepSeek 系端点长度限制生效）
- **agent 缓存**：构造耗时 ~24s，必须缓存（`get_agent` / `reset_agent_cache`，
  llm 注入时独立构建不缓存，与 deepagents 同模式）
- **成本调优**：ToolCallLimitMiddleware（单轮工具调用上限，continue 优雅
  降级）；管理工具不缓存（全部有副作用或需实时数据）
- **SSE 契约**：step/token/done 与用户侧一致（done 携带 conversation_id）
- **is_admin 状态传递**：自定义 `AdminAgentState(DeepAgentState)` 追加
  `is_admin` 字段；入口经 `{"messages": ..., "is_admin": True}` 传入
  （仅路由层 X-Admin-Token 鉴权后可达），管理工具经
  `InjectedState("is_admin")` 注入并在调用前校验（见 tools.py）

**system prompt 管理员角色**：只操作项目数据、遵守两阶段二次确认契约、
不执行系统命令/不读写服务器文件（工具面本身已排除系统能力，prompt 为
行为纪律约束）。

**无会话持久化**：管理端 agent 为「后台页打开才活跃」的一次性对话
（每请求独立，不落库短期记忆/长期记忆，避免污染用户侧数据）；
conversation_id 仅透传回显（空串生成新 uuid）。
"""
import asyncio
import json
import logging
import threading
import uuid
from typing import Any, AsyncIterator, Dict, List, NotRequired, Optional

from deepagents import create_deep_agent
from deepagents.graph import DeepAgentState
from langchain.agents.middleware.tool_call_limit import ToolCallLimitMiddleware
from langchain_core.messages import (
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services.admin_agent.tools import ADMIN_TOOLS
from app.services.deepagents.harness import (
    _AGENT_MAX_TOKENS,
    _AGENT_TIMEOUT,
    _MAX_AGENT_STEPS,
    ensure_harness_profile,
)
from app.services.usage_tracker import CHAIN_ADMIN_AGENT, usage_context

logger = logging.getLogger(__name__)


# ── 自定义图状态（is_admin 工具注入） ─────────────────────────────────────


class AdminAgentState(DeepAgentState):
    """管理 agent 自定义状态：追加 is_admin 字段（工具 admin 上下文校验）。

    `is_admin` 由入口（chat/chat_stream）固定置 True 传入（仅路由层
    X-Admin-Token 鉴权后可达）；管理工具经 `InjectedState("is_admin")`
    注入并在调用前校验（非管理员上下文直接抛错拒绝，见 tools.py）。
    """

    is_admin: NotRequired[bool]


# ── 系统提示词（管理员角色） ─────────────────────────────────────────────


SYSTEM_PROMPT = """你是景区导览系统的管理后台智能助手（admin-agent），协助管理员维护系统项目数据。

行为准则：
1. 只能操作项目自身数据（知识库条目、用户长期记忆、公告、运营统计、RAG 索引、首页数据、
   注册账号），严禁执行系统命令、读写服务器文件、访问外部系统——你没有这些能力，不要尝试。
2. **删除/禁用/重置密码类操作必须二次确认**：delete_knowledge / delete_user_memory /
   delete_announcement / disable_user / reset_user_password / delete_user 首次调用只返回
   "待确认"结果（含 operation_id 与目标摘要），此时**并没有执行任何变更**；必须先向管理员
   展示待确认信息，再调用 confirm_operation(operation_id) 确认后才会真正执行。
   未确认/过期（约 120 秒）的 operation_id 无法执行，需要重新发起。
3. **账号管理纪律**：删除/禁用/重置密码均为**单目标**操作（参数为单个 user_id），
   严禁批量删除、严禁无目标模糊操作（如按关键词删除账号）；删除账号会**级联清理**其
   对话/量化评估/长期记忆（不可恢复），发起删除前必须向管理员展示影响概览（对话数/记忆数）；
   系统管理员账号（ADMIN_USERNAME，通常为 admin）受保护，不可禁用/删除；
   create_user 的密码至少 6 位；reset_user_password 不传 new_password 时系统生成临时密码
   （确认执行后仅返回一次，必须立即转达管理员）。
4. **公告新建默认为草稿**：create_announcement 默认创建草稿（draft），只有发布
   （published）后才在首页/访客端可见。管理员要求"发一个首页公告"时，应在创建时直接
   指定 status=published（一步完成）；若已建成草稿，则再调用 set_announcement_status
   发布。发布与否均须如实告知管理员。
5. 写操作（新增/修改/发布/下架/删除/重建索引/账号操作）完成后如实汇报结果（目标与状态）；
   系统会自动记录审计日志（operator=admin-agent）并落库 admin_audit_logs 表，无需额外声明。
6. 数据一律以工具返回为准：查询类工具是只读的，不要编造统计数字、条目内容或 id。
7. 工具效率：一次查询到足够信息后直接作答，不要对同一主题反复重查。
8. 用户记忆与账号信息为敏感数据：仅在管理员要求时查询/操作，展示时注意内容摘要。"""

# 工具调用步骤展示文案（SSE step 事件透出）
_STEP_LABELS: Dict[str, str] = {
    "query_knowledge": "正在查询知识库…",
    "create_knowledge": "正在新增知识条目…",
    "update_knowledge": "正在更新知识条目…",
    "delete_knowledge": "正在删除知识条目…",
    "query_user_memories": "正在查询用户记忆…",
    "delete_user_memory": "正在删除用户记忆…",
    "list_announcements": "正在查询公告…",
    "create_announcement": "正在新建公告…",
    "update_announcement": "正在更新公告…",
    "delete_announcement": "正在删除公告…",
    "set_announcement_status": "正在更新公告状态…",
    "query_statistics": "正在查询运营数据…",
    "rebuild_rag_index": "正在重建知识库索引…",
    "view_home_highlights": "正在查询首页数据…",
    "list_users": "正在查询账号列表…",
    "get_user_detail": "正在查询账号详情…",
    "create_user": "正在创建账号…",
    "disable_user": "正在禁用账号…",
    "enable_user": "正在启用账号…",
    "reset_user_password": "正在重置账号密码…",
    "delete_user": "正在删除账号…",
    "confirm_operation": "正在确认操作…",
}


# ── Agent 构建（缓存：构造耗时 ~24s，必须缓存） ───────────────────────────


def _build_agent(llm: Optional[Any] = None) -> Any:
    """构建管理 agent（create_deep_agent，复用 A2/A3 装配模式）。

    - HarnessProfile 复用 deepagents 注册（排除内置 9 工具 + 禁用通用子代理）
    - 模型复用 llm.py 工厂（max_tokens 经 extra_body 注入、timeout ≥120s，
      与用户侧同配置 opencode-go）
    - 显式 state_schema（is_admin 字段供工具 admin 上下文校验注入）
    - recursion_limit 由调用方在 ainvoke/astream 的 config 显式收紧
    - ToolCallLimitMiddleware 单轮工具调用上限（continue 优雅降级，
      与管理工具数匹配，防病态循环）
    """
    ensure_harness_profile()
    if llm is None:
        llm = get_chat_llm(max_tokens=_AGENT_MAX_TOKENS, timeout=_AGENT_TIMEOUT)
    return create_deep_agent(
        model=llm,
        tools=ADMIN_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        state_schema=AdminAgentState,
        middleware=[
            ToolCallLimitMiddleware(
                run_limit=settings.AGENT_MAX_TOOL_CALLS,
                exit_behavior="continue",
            )
        ],
        name="scenic_admin_agent",
    )


_agent: Optional[Any] = None
_build_lock = threading.Lock()


def get_agent(llm: Optional[Any] = None) -> Any:
    """获取管理 agent 实例：默认 LLM 走模块级缓存；测试注入 llm 时独立构建。"""
    global _agent
    if llm is None:
        with _build_lock:
            if _agent is None:
                _agent = _build_agent()
            return _agent
    return _build_agent(llm=llm)


def reset_agent_cache() -> None:
    """重置 agent 缓存（供测试与配置热更新使用）。"""
    global _agent
    with _build_lock:
        _agent = None


# ── 对话入口 ──────────────────────────────────────────────────────────────


def _extract_answer(all_messages: List[BaseMessage]) -> str:
    """提取最终回答（最后一条非工具 AI 消息，与 deepagents 同逻辑）。"""
    for msg in reversed(all_messages):
        if getattr(msg, "type", "") == "ai" and not getattr(msg, "tool_calls", None):
            content = msg.content
            return content if isinstance(content, str) else str(content or "")
    return ""


async def chat(
    query: str,
    *,
    conversation_id: str = "",
    agent: Optional[Any] = None,
    llm: Optional[Any] = None,
) -> Dict[str, Any]:
    """管理 agent 非流式对话（X-Admin-Token 鉴权由路由层保证）。

    - 每请求独立（无会话持久化，见模块 docstring）；conversation_id 仅透传
      回显（空串生成新 uuid，管理端多轮对话由前端自行携带）
    - is_admin 固定置 True 注入图状态（路由层鉴权后可达；工具侧仍有
      独立校验，双保险）

    Args:
        query: 管理员消息
        conversation_id: 会话 ID（透传回显；空串生成新 uuid）
        agent: 注入的 agent 实例（测试用）
        llm: 注入的 LLM（测试用，独立构建不缓存）

    Returns:
        {"answer", "conversation_id"}
    """
    cid = conversation_id or uuid.uuid4().hex
    agent = agent or get_agent(llm=llm)
    # 批次7 C5 token 记账：标注链路/用户（管理员 agent 调用记账 user=admin）
    with usage_context(CHAIN_ADMIN_AGENT, "admin"):
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=query)], "is_admin": True},
            config={"recursion_limit": _MAX_AGENT_STEPS},
        )
    all_messages: List[BaseMessage] = result.get("messages", [])
    answer = _extract_answer(all_messages)
    logger.info(
        "管理员Agent对话完成: conversation=%s tools=%d answer_len=%d",
        cid,
        len([m for m in all_messages if getattr(m, "type", "") == "tool"]),
        len(answer),
    )
    return {"answer": answer, "conversation_id": cid}


async def chat_stream(
    query: str,
    *,
    conversation_id: str = "",
    agent: Optional[Any] = None,
    llm: Optional[Any] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """管理 agent 流式对话（SSE 事件源，事件契约与用户侧一致）。

    事件契约（与 deepagents / 对话路由完全一致，管理端前端零适配复用）：

    - `step`：工具调用步骤（tool + label，按 call id 去重）
    - `token`：回答增量（保留 suppress_tokens 语义——工具调用消息的内联
      content 抑制，防渲染工具调用前的碎文本）
    - `done`：流结束事件（携带 conversation_id）

    Args:
        query: 管理员消息
        conversation_id: 会话 ID（透传回显；空串生成新 uuid）
        agent: 注入的 agent 实例（测试用）
        llm: 注入的 LLM（测试用，独立构建不缓存）
    """
    cid = conversation_id or uuid.uuid4().hex
    agent = agent or get_agent(llm=llm)

    seen_call_ids: "set[str]" = set()
    suppress_tokens = False
    answer_parts: List[str] = []

    # 批次7 C5 token 记账：标注链路/用户（生成器跨 yield 持有上下文，
    # 客户端中断时 contextmanager 仍会复位）
    with usage_context(CHAIN_ADMIN_AGENT, "admin"):
        async for chunk, _meta in agent.astream(
            {"messages": [HumanMessage(content=query)], "is_admin": True},
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
                # 工具执行结果（ToolMessage）：重置抑制标记（工具调用结束，
                # 后续模型正文正常输出）
                suppress_tokens = False

    answer = "".join(answer_parts)
    logger.info(
        "管理员Agent对话完成(流式): conversation=%s steps=%d answer_len=%d",
        cid,
        len(seen_call_ids),
        len(answer),
    )
    yield {"type": "done", "conversation_id": cid}
