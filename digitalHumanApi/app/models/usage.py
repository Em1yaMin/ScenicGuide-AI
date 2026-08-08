"""LLM token 用量记账模型（批次7 C5）。

记录每次 LLM 调用的 token 用量/耗时/链路来源/用户，供管理端报表
（趋势图 + 明细表，参考 GitHub 用量报表风格）。写入经进程内队列 +
后台批量 flush（app/services/usage_tracker.py），不阻塞 LLM 调用。

字段语义：
- model: 实际调用的模型名（如 deepseek-v4-flash / deepseek-chat）
- input_tokens / output_tokens / total_tokens: 响应 usage 字段
  （流式调用经 stream_options.include_usage 获取，见 usage_tracker）
- duration_ms: 单次模型调用耗时（毫秒）
- chain: 链路来源（对话/推荐/评估/压缩/管理员agent/记忆提取/规划/其他）
- user: 用户标识（登录用户 id / 游客 id / visitor；后台/定时任务为 NULL）
- cost: 估算费用（元，按 MODEL_PRICES 单价表换算；订阅制可留空不记钱）
- created_at: 记录时间（UTC）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class LLMUsage(Base):
    """LLM 调用用量记录（llm_usage 表）。"""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    chain: Mapped[str] = mapped_column(String(32), index=True)
    user: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    cost: Mapped[Optional[float]] = mapped_column(Float, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
