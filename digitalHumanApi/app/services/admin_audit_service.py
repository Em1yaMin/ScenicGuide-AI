"""管理员账号管理操作审计（2026-08-13 船长需求，落库强制）。

记录管理员对账号的敏感操作（创建/禁用启用/重置密码/删除），含操作人、
时间、目标账号、操作结果，供管理页查询展示（admin_audit_logs 表）。
删除类操作在二次确认执行后才写审计；执行失败也记录（结果如实）。
"""
import logging
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_audit import AdminAuditLog
from app.schemas.user_admin import AdminAuditLogItem, AdminAuditLogListResponse

logger = logging.getLogger(__name__)

# 审计动作取值（与 tools.py 账号工具 / 前端类型对齐）
ACTION_CREATE = "create"
ACTION_DISABLE = "disable"
ACTION_ENABLE = "enable"
ACTION_RESET_PASSWORD = "reset_password"
ACTION_DELETE = "delete"


def _to_item(row: AdminAuditLog) -> AdminAuditLogItem:
    return AdminAuditLogItem(
        id=row.id,
        operator=row.operator,
        action=row.action,
        target_user_id=row.target_user_id,
        target_username=row.target_username,
        detail=row.detail,
        created_at=row.created_at,
    )


async def record_audit(
    db: AsyncSession,
    *,
    operator: str,
    action: str,
    target_user_id: Optional[int],
    target_username: str,
    detail: str = "ok",
) -> AdminAuditLog:
    """写入一条账号管理操作审计（与业务同一事务提交，不单独 commit）。

    Args:
        operator: 操作人标识（管理员账号名 / admin-agent）
        action: create/disable/enable/reset_password/delete
        target_user_id: 目标用户 id（可空）
        target_username: 目标用户名快照（用户删除后仍可查）
        detail: 结果摘要（如 "ok" / "user_not_found" / "ok deleted=3 dialogues"）
    """
    row = AdminAuditLog(
        operator=operator[:64],
        action=action,
        target_user_id=target_user_id,
        target_username=target_username[:64],
        detail=(detail or "")[:255],
    )
    db.add(row)
    await db.flush()
    logger.info(
        "账号管理审计落库: operator=%s action=%s target=%s(%s) detail=%s",
        row.operator,
        row.action,
        row.target_username,
        row.target_user_id,
        row.detail,
    )
    return row


async def list_audit_logs(
    db: AsyncSession,
    *,
    keyword: Optional[str] = None,
    action: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminAuditLogListResponse:
    """审计日志分页列表（keyword 模糊匹配操作人/目标用户名/结果）。"""
    conditions = []
    if keyword:
        like = f"%{keyword}%"
        conditions.append(
            or_(
                AdminAuditLog.operator.like(like),
                AdminAuditLog.target_username.like(like),
                AdminAuditLog.detail.like(like),
            )
        )
    if action:
        conditions.append(AdminAuditLog.action == action)
    total = (
        await db.execute(
            select(func.count()).select_from(AdminAuditLog).where(*conditions)
        )
    ).scalar_one()
    result = await db.execute(
        select(AdminAuditLog)
        .where(*conditions)
        .order_by(AdminAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_to_item(r) for r in result.scalars().all()]
    return AdminAuditLogListResponse(
        items=items, total=total, page=page, page_size=page_size
    )
