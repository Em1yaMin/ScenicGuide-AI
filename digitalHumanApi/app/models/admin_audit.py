from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdminAuditLog(Base):
    """管理员账号管理操作审计日志（2026-08-13 船长需求，落库强制）。

    记录管理员对账号的敏感操作（创建/禁用启用/重置密码/删除），含操作人、
    时间、目标账号、操作结果。删除类操作仍走两阶段二次确认，确认执行后
    才写审计；执行失败也记录（结果如实）。
    """

    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    operator: Mapped[str] = mapped_column(String(64), index=True)  # 操作人标识（管理员账号 / admin-agent）
    action: Mapped[str] = mapped_column(String(32), index=True)  # create/disable/enable/reset_password/delete
    target_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, index=True, default=None
    )  # 目标用户 id（创建操作落库后确定）
    target_username: Mapped[str] = mapped_column(String(64))  # 目标用户名（快照，用户删除后仍可查）
    detail: Mapped[Optional[str]] = mapped_column(String(255), default=None)  # 结果摘要（如 "ok" / "user_not_found"）
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
