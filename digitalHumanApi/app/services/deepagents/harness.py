"""deepagents 装配环境：HarnessProfile 注册与装配常量（A2 试点 → A3 主链路，批次3）。

A1 实测报告（data/sg-deepagents-probe/report.md）结论：

- deepagents 0.7.5 与项目 langchain 1.3.x / langgraph 1.2.x 栈零冲突
- **内置工具只能经 HarnessProfile.excluded_tools 排除**：create_deep_agent
  默认装配文件系统/execute/task 内置工具，且 FilesystemMiddleware /
  SubAgentMiddleware 是受保护脚手架（excluded_middleware 不允许排除），
  只能走 excluded_tools
- profile 按键为模型 provider（ChatOpenAI → `"openai"`，经
  `_harness_profile_for_model` 的 provider-only fallback 匹配）；未注册
  profile 时使用空默认 profile，**不会自动排除内置工具**（A1 报告 §3）
- 默认通用 subagent：无 profile 或 profile 未禁用时自动装配
  `general-purpose` subagent（提供 `task` 工具），经
  `GeneralPurposeSubagentProfile(enabled=False)` 禁用

本模块提供 `ensure_harness_profile()`（幂等注册）与装配常量，
供 `agent.py` 构造 deepagents agent 前调用。
"""
import logging

from deepagents import HarnessProfile, register_harness_profile
from deepagents.profiles import GeneralPurposeSubagentProfile

logger = logging.getLogger(__name__)

# ── 装配常量（A1 报告 §8 装配清单） ───────────────────────────────────────

# agent 图执行步数上限：create_deep_agent 与 create_agent 同款坑——硬编码
# recursion_limit=9999（deepagents/graph.py 末尾 with_config），异常 LLM 反复
# 请求工具时会循环执行近万次才报错。对话场景必须显式收紧：语义同
# chat_agent._MAX_AGENT_STEPS=25（防死循环）；A4 与工具上限联动——每轮
# 工具调用约消耗 2 个 superstep（模型+工具节点），恰好 15 次调用 + 最终
# 作答 ≈ 31 步，34 留余量保证 ToolCallLimitMiddleware continue 优雅降级
# 在上限边界不触发 GraphRecursionError（见 doc/deepagents-tuning.md §1）
_MAX_AGENT_STEPS = 34
# LLM 调用超时（秒）：A1 建议 ≥120s——opencode-go 延迟波动大，现有快速路径
# 的 60s 在规划类长链场景偏紧（实测单轮最长 37s，留足重试/波动余量）
_AGENT_TIMEOUT = 120
# 对话输出上限（token）：经 llm.py 工厂 extra_body 注入 max_tokens，
# DeepSeek 系端点长度限制生效（与 chat_agent._CHAT_MAX_TOKENS 一致）
_AGENT_MAX_TOKENS = 2048

# ── 内置工具排除集 ────────────────────────────────────────────────────────

# deepagents 内置工具（A1 报告 §3 装配方法列 8 个 + 实测补 1 个）：
# - 文件系统：ls / read_file / write_file / edit_file / glob / grep / delete
# - shell 执行：execute（SandboxBackend 下可执行任意命令，安全风险）
# - 子代理：task（调用通用子代理）
# 景区导览对话场景不需要任何系统能力，全部排除（A1 实测排除后模型侧仅剩
# 项目自定义工具；delete 为实测发现同属 FilesystemMiddleware 装配的第 9 个
# 内置工具，一并排除）
_EXCLUDED_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "delete",
        "execute",
        "task",
    }
)

_HARNESS_REGISTERED = False


def ensure_harness_profile() -> None:
    """注册 openai provider 的 HarnessProfile（幂等，进程内仅一次）。

    - `excluded_tools`：排除 deepagents 内置 9 工具（文件系统/execute/task，
      见 `_EXCLUDED_TOOLS`），模型只见项目自定义工具（零系统能力面）
    - `general_purpose_subagent` 禁用：不装配默认通用子代理（无子代理场景，
      避免模型经 task 工具把任务外包给不受控的通用子代理）

    必须在构造 agent 前调用（未注册 profile 时 create_deep_agent 使用空
    默认 profile，内置工具不会被排除）；`agent.py` 的 `_build_agent` 已
    自动调用，测试与外部调用方无需手动注册。
    """
    global _HARNESS_REGISTERED
    if _HARNESS_REGISTERED:
        return
    register_harness_profile(
        "openai",
        HarnessProfile(
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
            excluded_tools=_EXCLUDED_TOOLS,
        ),
    )
    _HARNESS_REGISTERED = True
    logger.info(
        "deepagents HarnessProfile 已注册: provider=openai excluded_tools=%d",
        len(_EXCLUDED_TOOLS),
    )
