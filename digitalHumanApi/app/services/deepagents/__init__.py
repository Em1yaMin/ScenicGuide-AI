"""deepagents 装配层（A2 试点 → **A3 对话主链路**，批次3）。

A3 起为对话主链路（路由 `/api/v1/dialogue/chat` 默认引擎 langchain 走本层，
异常由路由层回落 Dify）；A2 试点时的并存说明已过时，chat_agent /
planner_agent 保留为弃用代码（A4 收官再评估删除）。本包提供：

- `harness`：HarnessProfile 注册（排除 deepagents 内置工具/禁用通用子代理）
  + 装配常量（recursion_limit / 超时 / 输出上限，A1 报告 §8 清单）
- `memory_tools`：长期记忆工具化四工具（user_id 隔离，船长决策）
- `agent`：对话入口（会话管理/意图分类双模式挂载/轻量用户摘要注入/流式
  SSE 契约，A1 报告 §5）

入口：`from app.services.deepagents import get_agent, chat, chat_stream`
（详见各子模块 docstring；测试见 tests/test_deepagents_*.py）
"""
from app.services.deepagents.agent import (
    SYSTEM_PROMPT,
    _PLAN_INSTRUCTION,
    chat,
    chat_stream,
    get_agent,
    reset_agent_cache,
)
from app.services.deepagents.harness import (
    _MAX_AGENT_STEPS,
    ensure_harness_profile,
)
from app.services.deepagents.memory_tools import (
    delete_user_memory,
    list_user_memories,
    recall_user_memory,
    save_user_memory,
)

__all__ = [
    "SYSTEM_PROMPT",
    "_PLAN_INSTRUCTION",
    "_MAX_AGENT_STEPS",
    "chat",
    "chat_stream",
    "delete_user_memory",
    "ensure_harness_profile",
    "get_agent",
    "list_user_memories",
    "recall_user_memory",
    "reset_agent_cache",
    "save_user_memory",
]
