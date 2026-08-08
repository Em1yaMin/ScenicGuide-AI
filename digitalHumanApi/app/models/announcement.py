"""公告模型。

公告用于景区运营通知（开园时间调整、活动预告、政策变更等），
管理端可发布/下架（status），访客端仅可见已发布公告（置顶优先 + 时间倒序）。
"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 公告状态常量：发布 / 草稿
STATUS_PUBLISHED = "published"
STATUS_DRAFT = "draft"


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_DRAFT, index=True
    )
    # 置顶标记：访客端列表置顶优先展示
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
