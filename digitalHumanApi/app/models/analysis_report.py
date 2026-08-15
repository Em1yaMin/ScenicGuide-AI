"""AI 运营分析概览报告持久化模型（2026-08-15 船长需求）。

管理后台「AI 运营分析概览」（GET /api/v1/analysis/overview）的分析结果
落库持久化：服务重启后先秒回库中上次结果、后台静默刷新，避免每次打开都
重新抽样本调 LLM（实测 60~140s）。每 days 参数一行（唯一约束），upsert
语义：重新分析完成后覆盖更新。

字段语义：
- days: 统计天数范围（1-365），每 days 一行
- response_json: AIOverviewResponse 响应快照 JSON（含 analysis_timestamp）
- analysis_timestamp: 响应中的实际分析时间（冗余列，便于查询/调试）
- created_at / updated_at: 落库时间；过期判定以 updated_at 为基准
  （沿用进程内缓存 TTL 语义 OVERVIEW_CACHE_TTL_SECONDS=600s）
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnalysisReport(Base):
    """AI 运营分析概览报告（analysis_reports 表）。"""

    __tablename__ = "analysis_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    days: Mapped[int] = mapped_column(Integer, unique=True)
    response_json: Mapped[str] = mapped_column(Text)
    analysis_timestamp: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
