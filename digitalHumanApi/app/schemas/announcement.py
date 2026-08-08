"""公告模块的 Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.announcement import STATUS_DRAFT, STATUS_PUBLISHED

# 公告状态取值（发布 / 草稿），管理端创建/更新/切换状态共用
AnnouncementStatus = Literal[STATUS_PUBLISHED, STATUS_DRAFT]


class AnnouncementItem(BaseModel):
    """公告条目（管理端与访客端共用字段结构）。"""

    id: int
    title: str
    content: str
    status: str
    pinned: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AnnouncementCreateRequest(BaseModel):
    """创建公告请求：新建默认草稿、不置顶。"""

    title: str = Field(..., min_length=1, max_length=255, description="公告标题")
    content: str = Field(..., description="公告正文")
    status: AnnouncementStatus = STATUS_DRAFT
    pinned: bool = False


class AnnouncementUpdateRequest(BaseModel):
    """更新公告请求：仅更新传入字段（部分更新）。"""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    content: Optional[str] = None
    status: Optional[AnnouncementStatus] = None
    pinned: Optional[bool] = None


class AnnouncementStatusUpdateRequest(BaseModel):
    """发布状态切换请求（发布/草稿）。"""

    status: AnnouncementStatus


class AnnouncementListResponse(BaseModel):
    """管理端公告分页列表。"""

    items: list[AnnouncementItem]
    total: int
    page: int
    page_size: int

    model_config = {"from_attributes": True}


class PublicAnnouncementItem(BaseModel):
    """访客端公告条目：仅已发布公告（无 status 字段）。"""

    id: int
    title: str
    content: str
    pinned: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PublicAnnouncementListResponse(BaseModel):
    """访客端公告列表：置顶优先、时间倒序。"""

    items: list[PublicAnnouncementItem]
    total: int

    model_config = {"from_attributes": True}
