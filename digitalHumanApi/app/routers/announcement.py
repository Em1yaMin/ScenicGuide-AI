"""公告路由。

- 管理端 `/api/admin/announcements`：CRUD + 发布状态切换（X-Admin-Token 鉴权）
- 访客端 `/api/v1/announcements`：公开列表（仅已发布，置顶优先 + 时间倒序）
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.deps import verify_admin_token
from app.schemas.announcement import (
    AnnouncementCreateRequest,
    AnnouncementItem,
    AnnouncementListResponse,
    AnnouncementStatusUpdateRequest,
    AnnouncementUpdateRequest,
    PublicAnnouncementListResponse,
)
from app.services import announcement_service

admin_router = APIRouter(prefix="/api/admin/announcements", tags=["公告管理"])
public_router = APIRouter(prefix="/api/v1/announcements", tags=["访客公告"])


# ── 管理端（X-Admin-Token 鉴权） ─────────────────────────────────────────


@admin_router.get("", response_model=AnnouncementListResponse)
async def list_announcements(
    status_filter: Optional[str] = Query(None, alias="status", description="状态筛选（published/draft）"),
    keyword: Optional[str] = Query(None, description="标题/内容关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AnnouncementListResponse:
    """管理端公告分页列表（含草稿，置顶优先 + 更新时间倒序）。"""
    return await announcement_service.list_announcements(
        db,
        status=status_filter,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@admin_router.post("", response_model=AnnouncementItem, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    request: AnnouncementCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AnnouncementItem:
    """创建公告（默认草稿；可指定发布/置顶）。"""
    return await announcement_service.create_announcement(db, request)


@admin_router.put("/{announcement_id}", response_model=AnnouncementItem)
async def update_announcement(
    announcement_id: int,
    request: AnnouncementUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AnnouncementItem:
    """部分更新公告（仅更新传入字段）。"""
    announcement = await announcement_service.update_announcement(
        db, announcement_id, request
    )
    if announcement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="公告不存在",
        )
    return AnnouncementItem.model_validate(announcement)


@admin_router.patch("/{announcement_id}/status", response_model=AnnouncementItem)
async def set_announcement_status(
    announcement_id: int,
    request: AnnouncementStatusUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AnnouncementItem:
    """发布状态切换（published/draft），发布即对访客端可见。"""
    announcement = await announcement_service.set_announcement_status(
        db, announcement_id, request
    )
    if announcement is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="公告不存在",
        )
    return AnnouncementItem.model_validate(announcement)


@admin_router.delete("/{announcement_id}")
async def delete_announcement(
    announcement_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> dict[str, bool]:
    """删除公告。"""
    deleted = await announcement_service.delete_announcement(db, announcement_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="公告不存在",
        )
    return {"success": True}


# ── 访客端（公开，无需鉴权） ─────────────────────────────────────────────


@public_router.get("", response_model=PublicAnnouncementListResponse)
async def list_public_announcements(
    limit: int = Query(20, ge=1, le=100, description="返回条数上限"),
    db: AsyncSession = Depends(get_db),
) -> PublicAnnouncementListResponse:
    """公开公告列表：仅已发布，置顶优先、发布时间倒序。"""
    return await announcement_service.list_published_announcements(db, limit=limit)
