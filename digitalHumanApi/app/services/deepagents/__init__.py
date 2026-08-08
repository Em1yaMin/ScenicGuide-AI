"""deepagents 装配层（A2 试点，批次3）。

在现有主链路（chat_agent）之外独立提供 deepagents 0.7.5 装配能力，
**不替换主链路**（A3 才切换），与现有链路共存：

- `harness`：HarnessProfile 注册（排除 deepagents 内置工具/禁用通用子代理）
  + 装配常量（recursion_limit / 超时 / 输出上限，A1 报告 §8 清单）
- `memory_tools`：长期记忆工具化四工具（user_id 隔离，船长决策）
- `agent`：装配入口（工具集、agent 实例缓存、意图分类双模式挂载、
  轻量用户摘要注入、流式 SSE 契约）

入口：`from app.services.deepagents import get_agent, chat, chat_stream`
（详见各子模块 docstring；试点测试见 tests/test_deepagents_*.py）
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
