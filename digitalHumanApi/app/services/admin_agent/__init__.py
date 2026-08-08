"""管理员 agent（批次6 B4，deepagents 装配模式管理端变体）。

X-Admin-Token 鉴权（路由层），可对项目数据库直接调整（知识库/用户记忆/
公告/统计/RAG 索引/首页数据，全部复用既有 service）；删除类操作两阶段
二次确认（confirm.py）+ 写操作审计日志（tools.py `_audit_log`）。

模块：

- `confirm`：两阶段二次确认的待确认项存储（operation_id + 短 TTL 过期）
- `tools`：管理工具集（每工具调用前校验 admin 上下文；删除类工具先登记
  待确认，confirm_operation 才真正执行）
- `agent`：装配（复用 deepagents HarnessProfile/装配常量/缓存模式）+
  chat / chat_stream（SSE 契约与用户侧一致）

入口：`from app.services.admin_agent import chat, chat_stream, get_agent`
（路由见 app/routers/admin_agent.py；测试见 tests/test_admin_agent*.py）
"""
from app.services.admin_agent.agent import (
    SYSTEM_PROMPT,
    AdminAgentState,
    chat,
    chat_stream,
    get_agent,
    reset_agent_cache,
)
from app.services.admin_agent.tools import ADMIN_TOOLS, confirm_operation

__all__ = [
    "SYSTEM_PROMPT",
    "AdminAgentState",
    "ADMIN_TOOLS",
    "chat",
    "chat_stream",
    "confirm_operation",
    "get_agent",
    "reset_agent_cache",
]
