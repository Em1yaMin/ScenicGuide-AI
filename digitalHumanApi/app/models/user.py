from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """注册用户（登录用户拥有长期记忆，游客不落库）。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    # 软禁用（账号管理，2026-08-13）：禁用后登录/对话/记忆恢复均被拒绝。
    # server_default 与迁移对齐（遗留库加列时历史行自动置为启用）。
    is_disabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    disabled_at: Mapped[Optional[datetime]] = mapped_column(default=None)
