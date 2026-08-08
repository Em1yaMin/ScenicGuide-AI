"""长会话上下文压缩（A4 成本/速度调优，批次3 收官）。

背景：多轮长会话下历史消息不断膨胀，每轮模型输入 token 随轮次线性增长
（A1 实测复杂任务单轮 15-28k input tokens 的主要成分之一）。A4 组合拳之
三：**回答后触发**（船长决策）压缩早期轮次为摘要、保留最近 N 轮完整消息，
长会话的 token 消耗从线性增长变为有界。

实现（全用 langchain 自带中间件，零新依赖）：

- **压缩引擎 = 内置 `SummarizationMiddleware`**（langchain.agents.middleware）：
  复用其阈值判定（trigger）、安全截断（AI/Tool 消息对不拆散，本层历史仅存
  Q&A 对无 Tool 消息）、摘要生成（LLM + 可定制 prompt）与保留窗口（keep）。
  本模块在会话存储层**回答后**调用中间件的 `abefore_model`（纯状态函数，
  不依赖图运行时），把压缩结果写回短期记忆存储（进程内 + SQLite），
  下一次对话直接使用压缩后的历史——摘要只生成一次、持久化、重启不丢；
  与「把中间件挂进图里」的方案相比：① 不增加用户侧首字延迟（压缩在回答
  完成后进行，不占用下一轮模型调用前的时间）；② 不会每轮重复触发压缩
  （图内方案在无 checkpointer 的 stateless 调用下，每轮都会对全量历史
  重新压缩，反而增加成本）
- **阈值/窗口**：`AGENT_COMPACT_TRIGGER_MESSAGES`（默认 24 条 ≈ 12 轮）
  触发、`AGENT_COMPACT_KEEP_MESSAGES`（默认 10 条 ≈ 5 轮）保留——会话
  历史按 2 条/轮（Q&A 对）折算，与船长建议的「阈值 10-15 轮、窗口 3-5 轮」
  一致；实测依据见 doc/deepagents-tuning.md
- **压缩质量**：自定义中文摘要 prompt，显式要求保留游客关键约束（人群/
  时间/预算/兴趣）与已确认事实（景点/路线名称等），防「带老人小孩」类
  关键信息在压缩中丢失（测试覆盖）
- **降级**：LLM 未配置/调用失败/解析异常一律跳过压缩（返回 None，历史
  保持原样，由既有消息上限截断兜底），绝不阻断对话、不影响 SSE 契约

本层历史只含 Q&A 对（HumanMessage/AIMessage，工具消息不落库），压缩后
首条为摘要 SystemMessage（中文包装），其余为保留的最近轮次。
"""
import logging
from typing import Any, List, Optional

from langchain.agents.middleware.summarization import (
    SummarizationMiddleware,
)
from langchain_core.messages import BaseMessage, SystemMessage

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.services.usage_tracker import CHAIN_COMPACTION, usage_context

logger = logging.getLogger(__name__)

# 摘要生成 LLM 输出上限（token）：摘要需精炼（关键约束 + 已确认事实），
# 1024 足够；经 llm.py 工厂 extra_body 注入（DeepSeek 系端点长度限制生效）
_SUMMARY_MAX_TOKENS = 1024
# 摘要生成 LLM 超时（秒）：压缩是回答后触发，可容忍较长等待；失败静默跳过
_SUMMARY_TIMEOUT = 60

# 摘要 SystemMessage 中文包装前缀（压缩结果写入会话历史时的标识）
_SUMMARY_PREFIX = "（以下为更早轮次的会话摘要，由系统自动压缩生成，仅作参考）："

# 摘要生成 prompt（替换中间件默认英文 prompt；结构化要求保留关键约束）
_SUMMARY_PROMPT = """你是景区导览数字人系统的会话压缩助手。下面 <messages> 内是游客与 AI 助手的一段多轮对话历史，请用中文提炼一份精炼摘要，用于替换这些早期轮次。必须遵守：

1. 保留游客的关键约束与偏好：人群（带老人/带小孩/情侣等）、时间与时长（半日游/一日游/具体时间）、预算、兴趣（拍照/自然风光/历史人文等）、出发地等——这些直接影响后续推荐，一字不漏
2. 保留已确认事实：已推荐或已讨论的景点/路线名称、已给出的关键信息（开放时间、票价、注意事项等）
3. 保留游客尚未得到答复的诉求（如待确认的问题、待推荐的路线）
4. 其余寒暄与过程可高度概括；只输出摘要正文，不要任何前后缀、标题或解释

<messages>
会话历史：
{messages}
</messages>"""


def _build_compact_middleware(llm: Optional[Any] = None) -> SummarizationMiddleware:
    """构造压缩引擎（内置 SummarizationMiddleware 实例）。

    触发/保留阈值取自配置（消息条数）；摘要模型：调用方注入的 llm 优先
    （测试注入 mock），否则 llm.py 工厂构造（与对话 LLM 同配置，输出上限
    收紧为 _SUMMARY_MAX_TOKENS）。
    """
    if llm is None:
        llm = get_chat_llm(max_tokens=_SUMMARY_MAX_TOKENS, timeout=_SUMMARY_TIMEOUT)
    return SummarizationMiddleware(
        model=llm,
        trigger=("messages", settings.AGENT_COMPACT_TRIGGER_MESSAGES),
        keep=("messages", settings.AGENT_COMPACT_KEEP_MESSAGES),
        summary_prompt=_SUMMARY_PROMPT,
    )


async def maybe_compact(
    history: List[BaseMessage],
    llm: Optional[Any] = None,
) -> Optional[List[BaseMessage]]:
    """回答后压缩：历史超过阈值时压缩早期轮次为摘要，保留最近 N 轮。

    Args:
        history: 会话历史（Q&A 对列表；压缩时其中可能已含历史摘要消息）
        llm: 注入的 LLM（测试用；不传则 llm.py 工厂构造）

    Returns:
        压缩后的历史列表（首条为摘要 SystemMessage + 保留的最近消息）；
        未达阈值 / 压缩失败 / 开关关闭返回 None（历史原样保留，调用方
        不做任何改动）。绝不抛异常。

    实现：调用内置 SummarizationMiddleware.abefore_model（纯状态函数），
    其返回 `{"messages": [RemoveMessage, 摘要消息, *保留消息]}`；过滤掉
    langgraph 的 RemoveMessage 指令并把摘要消息重包装为中文 SystemMessage
    （默认英文包装不适合景区导览会话，且 SystemMessage 更符合「系统生成的
    上下文」语义；会话存储/序列化已支持 system 角色）。
    """
    if not settings.AGENT_COMPACT_ENABLED:
        return None
    if not history:
        return None
    try:
        middleware = _build_compact_middleware(llm)
        # 批次7 C5 token 记账：标注链路（压缩摘要为后台任务，无用户）
        with usage_context(CHAIN_COMPACTION):
            result = await middleware.abefore_model({"messages": list(history)}, None)
    except Exception as e:
        # LLM 未配置/网络/解析异常：跳过压缩（历史原样，截断兜底），
        # 绝不阻断对话（与记忆提取钩子同策略）
        logger.warning("会话历史压缩失败（跳过，保持原历史）: %s", e)
        return None
    if result is None:
        return None  # 未达阈值：不压缩

    messages = result.get("messages") or []
    compacted: List[BaseMessage] = []
    summary_text: Optional[str] = None
    for msg in messages:
        # 过滤 langgraph 的 RemoveMessage 还原指令（会话层不是图状态，
        # 直接替换整列表即可）
        if getattr(msg, "type", "") == "remove":
            continue
        if summary_text is None:
            # 首条为摘要消息（中间件默认英文包装），剥离包装取摘要正文
            content = msg.content if isinstance(msg.content, str) else str(msg.content or "")
            marker = "Here is a summary of the conversation to date:\n\n"
            if content.startswith(marker):
                content = content[len(marker):]
            summary_text = content.strip()
            compacted.append(
                SystemMessage(content=f"{_SUMMARY_PREFIX}\n{summary_text}")
            )
        else:
            compacted.append(msg)
    if summary_text is None or len(compacted) < 2:
        # 摘要缺失（异常形态）：不压缩，保持原历史
        logger.warning("会话历史压缩结果异常（摘要缺失），跳过压缩")
        return None
    if not summary_text or summary_text.startswith("Error generating summary"):
        # 摘要为空 / 中间件把 LLM 异常吞为错误文本（其内部 except 捕获）：
        # 视为压缩失败，保持原历史（避免把错误文本当作摘要写回）
        logger.warning("会话历史压缩失败（摘要为空或错误文本），跳过压缩")
        return None
    logger.info(
        "会话历史已压缩: before=%d after=%d keep=%d",
        len(history),
        len(compacted),
        len(compacted) - 1,
    )
    return compacted
