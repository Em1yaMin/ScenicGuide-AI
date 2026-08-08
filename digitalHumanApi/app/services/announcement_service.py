"""公告业务逻辑：管理端 CRUD / 状态切换 / 访客端公开列表。"""
import logging
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.announcement import STATUS_PUBLISHED, Announcement
from app.schemas.announcement import (
    AnnouncementCreateRequest,
    AnnouncementItem,
    AnnouncementListResponse,
    AnnouncementStatusUpdateRequest,
    AnnouncementUpdateRequest,
    PublicAnnouncementItem,
    PublicAnnouncementListResponse,
)

logger = logging.getLogger(__name__)


async def list_announcements(
    db: AsyncSession,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> AnnouncementListResponse:
    """管理端分页查询公告，支持状态筛选与标题/内容关键词搜索。"""
    stmt = select(Announcement)
    count_stmt = select(func.count(Announcement.id))

    if status:
        stmt = stmt.where(Announcement.status == status)
        count_stmt = count_stmt.where(Announcement.status == status)
    if keyword:
        like = f"%{keyword}%"
        keyword_filter = or_(
            Announcement.title.ilike(like),
            Announcement.content.ilike(like),
        )
        stmt = stmt.where(keyword_filter)
        count_stmt = count_stmt.where(keyword_filter)

    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = (
        stmt.order_by(
            Announcement.pinned.desc(),
            Announcement.updated_at.desc(),
            Announcement.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    items = [AnnouncementItem.model_validate(a) for a in result.scalars().all()]

    return AnnouncementListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


async def list_published_announcements(
    db: AsyncSession,
    limit: int = 20,
) -> PublicAnnouncementListResponse:
    """访客端公开列表：仅已发布公告，置顶优先、发布时间倒序。

    Args:
        db: 数据库会话
        limit: 返回条数上限（默认 20，最大 100）
    """
    stmt = (
        select(Announcement)
        .where(Announcement.status == STATUS_PUBLISHED)
        .order_by(
            Announcement.pinned.desc(),
            Announcement.created_at.desc(),
            Announcement.id.desc(),
        )
        .limit(limit)
    )
    result = await db.execute(stmt)
    items = [
        PublicAnnouncementItem.model_validate(a) for a in result.scalars().all()
    ]
    return PublicAnnouncementListResponse(items=items, total=len(items))


async def get_announcement(
    db: AsyncSession, announcement_id: int
) -> Optional[Announcement]:
    """按 ID 查询公告；不存在返回 None。"""
    result = await db.execute(
        select(Announcement).where(Announcement.id == announcement_id)
    )
    return result.scalar_one_or_none()


async def create_announcement(
    db: AsyncSession, request: AnnouncementCreateRequest
) -> AnnouncementItem:
    """创建公告（默认草稿，创建即返回条目）。"""
    announcement = Announcement(
        title=request.title,
        content=request.content,
        status=request.status,
        pinned=request.pinned,
    )
    db.add(announcement)
    await db.flush()
    await db.refresh(announcement)
    logger.info("公告已创建: id=%s status=%s", announcement.id, announcement.status)
    return AnnouncementItem.model_validate(announcement)


async def update_announcement(
    db: AsyncSession, announcement_id: int, request: AnnouncementUpdateRequest
) -> Optional[Announcement]:
    """部分更新公告（仅更新显式传入字段）；不存在返回 None。"""
    announcement = await get_announcement(db, announcement_id)
    if announcement is None:
        return None

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(announcement, field, value)

    await db.flush()
    await db.refresh(announcement)
    return announcement


async def set_announcement_status(
    db: AsyncSession, announcement_id: int, request: AnnouncementStatusUpdateRequest
) -> Optional[Announcement]:
    """发布状态切换（发布/草稿）；不存在返回 None。"""
    announcement = await get_announcement(db, announcement_id)
    if announcement is None:
        return None
    announcement.status = request.status
    await db.flush()
    await db.refresh(announcement)
    return announcement


async def delete_announcement(db: AsyncSession, announcement_id: int) -> bool:
    """删除公告；成功返回 True，不存在返回 False。"""
    announcement = await get_announcement(db, announcement_id)
    if announcement is None:
        return False
    await db.delete(announcement)
    await db.flush()
    logger.info("公告已删除: id=%s", announcement_id)
    return True
