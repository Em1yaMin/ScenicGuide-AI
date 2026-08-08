"""记忆管理路由（批次5 B5：管理端长期记忆管理，X-Admin-Token 鉴权）。

**管理员例外**（船长确认）：管理员可查看/新增/修改/删除**任意注册用户**的
长期记忆——用户侧隔离由 deepagents 记忆四工具保证（recall/save/list/delete
全部 user_id 隔离、游客跳过，见 doc/long-term-memory.md），管理端不受此限；
游客/匿名用户（visitor / guest-*）无长期记忆：查询返回空、新增返回 400。

**审计**：删除/修改/新增经 logger 记录（operator=admin + 目标 user/
memory_id/内容摘要），日志先行（DB 审计表待需要时再引入，权衡见
doc/memory-admin-api.md）。

复用 memory_service（list_user_memories/delete_user_memory/save_user_memory/
find_memory_by_id/count_memories_per_user/memory_id_of），不重复实现存储逻辑。
"""
import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.routers.deps import require_role, verify_admin_token
from app.schemas.memory_admin import (
    AdminMemoryCreateRequest,
    AdminMemoryDeleteResponse,
    AdminMemoryItem,
    AdminMemoryListResponse,
    AdminMemoryUpdateRequest,
    AdminUserItem,
    AdminUserListResponse,
    MemoryUser,
)
from app.services import memory_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/memories", tags=["记忆管理"])


def _to_item(entry: memory_service.MemoryEntry) -> AdminMemoryItem:
    """MemoryEntry → 响应模型（memory_id 统一走 memory_id_of，legacy 派生兼容）。"""
    return AdminMemoryItem(
        memory_id=memory_service.memory_id_of(entry),
        user=entry.user,
        fact_type=entry.fact_type,
        content=entry.content,
        source_conversation_id=entry.source_conversation_id,
        timestamp=entry.timestamp,
    )


async def _ensure_registered_user(db: AsyncSession, user: str) -> None:
    """校验目标用户：游客/匿名 400；未注册 404。

    记忆仅归属注册用户（游客不落库），管理端新增记忆必须先确认目标用户存在，
    避免为不存在的用户写入垃圾记忆。
    """
    if memory_service.is_guest_user(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="游客/匿名用户不支持记忆管理",
        )
    try:
        user_id = int(user)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    if await db.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )


# ── 用户列表（供管理页面选择用户查看其记忆） ──────────────────────────────


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    keyword: Optional[str] = Query(None, description="用户名/昵称关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminUserListResponse:
    """已注册用户列表（id/username/nickname/记忆条数/最后登录时间）。"""
    conditions = []
    if keyword:
        like = f"%{keyword}%"
        conditions.append(or_(User.username.like(like), User.nickname.like(like)))
    total = (
        await db.execute(select(func.count()).select_from(User).where(*conditions))
    ).scalar_one()
    result = await db.execute(
        select(User)
        .where(*conditions)
        .order_by(User.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = result.scalars().all()
    # 单次持锁批量统计各用户记忆条数（user_memory 命名空间，按 str(id) 归属）
    counts = await memory_service.count_memories_per_user()
    items = [
        AdminUserItem(
            id=u.id,
            username=u.username,
            nickname=u.nickname,
            memory_count=counts.get(str(u.id), 0),
            created_at=u.created_at,
            last_login_at=u.last_login_at,
        )
        for u in users
    ]
    return AdminUserListResponse(items=items, total=total, page=page, page_size=page_size)


# ── 记忆查看/新增/修改/删除（管理员例外：跨用户可用） ─────────────────────


@router.get("", response_model=AdminMemoryListResponse)
async def list_user_memories(
    user: Annotated[
        MemoryUser, Query(..., description="目标用户标识（注册用户 id / 游客标识）")
    ],
    _: str = Depends(verify_admin_token),
) -> AdminMemoryListResponse:
    """按用户查看记忆列表（含 memory_id/fact_type/content/timestamp）。

    legacy 条目（升级前写入、无 memory_id）的 memory_id 为派生 id
    `legacy:{归一化 content}`，可直接用于删除/修改。游客/匿名用户返回空列表。
    """
    entries = await memory_service.list_user_memories(user)
    items = [_to_item(e) for e in entries]
    return AdminMemoryListResponse(user=user, total=len(items), items=items)


@router.post("", response_model=AdminMemoryItem, status_code=status.HTTP_201_CREATED)
async def create_memory(
    request: AdminMemoryCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminMemoryItem:
    """管理端新增记忆（管理员例外：可为任意注册用户新增）。

    游客/匿名用户 400；目标用户未注册 404；内容上限 200 字符（422 由 schema
    校验）。同内容按既有去重语义覆盖更新（不膨胀）。审计日志记录操作者 admin。
    """
    await _ensure_registered_user(db, request.user)
    # 预生成 memory_id：save_user_memory 透传（见 memory_service 写入注释），
    # 响应可直接返回稳定 id 供前端后续按 id 修改/删除
    entry = memory_service.MemoryEntry(
        user=request.user,
        fact_type=request.fact_type,
        content=request.content,
        memory_id=uuid.uuid4().hex,
    )
    result = await memory_service.save_user_memory(request.user, [entry])
    if result.get("skipped"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="游客/匿名用户不支持记忆管理",
        )
    logger.info(
        "管理员新增记忆: operator=admin user=%s memory_id=%s content=%r",
        request.user,
        entry.memory_id,
        entry.content[:50],
    )
    return _to_item(entry)


@router.put("/{memory_id}", response_model=AdminMemoryItem)
async def update_memory(
    memory_id: str,
    request: AdminMemoryUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminMemoryItem:
    """管理端修改记忆（管理员例外：跨用户可用；修改 = 删除后重建）。

    按 memory_id 定位（含 legacy 派生 id），删除原条目后以新内容重建：保留
    user 与 source_conversation_id，fact_type/content 按请求更新（部分更新），
    timestamp 刷新为当前时间、memory_id 更新为新建 uuid（响应返回新 id）。
    目标不存在 404。注意：新内容与同用户其他条目同内容时按既有去重语义
    覆盖更新（该其他条目被替换）。审计日志记录操作者 admin。
    """
    entry = await memory_service.find_memory_by_id(memory_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="记忆不存在",
        )
    new_entry = memory_service.MemoryEntry(
        user=entry.user,
        fact_type=request.fact_type if request.fact_type is not None else entry.fact_type,
        content=request.content if request.content is not None else entry.content,
        source_conversation_id=entry.source_conversation_id,
        memory_id=uuid.uuid4().hex,
    )
    deleted = await memory_service.delete_user_memory(entry.user, memory_id)
    if not deleted.get("deleted"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="记忆不存在",
        )
    result = await memory_service.save_user_memory(entry.user, [new_entry])
    if result.get("skipped"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="目标用户不支持记忆管理",
        )
    logger.info(
        "管理员修改记忆: operator=admin user=%s memory_id=%s content=%r -> %r",
        entry.user,
        memory_id,
        entry.content[:50],
        new_entry.content[:50],
    )
    return _to_item(new_entry)


@router.delete("/{memory_id}", response_model=AdminMemoryDeleteResponse)
async def delete_memory(
    memory_id: str,
    _: str = Depends(require_role("admin")),
) -> AdminMemoryDeleteResponse:
    """按 memory_id 删除单条记忆（管理员例外：跨用户可用；legacy 派生 id 兼容）。

    审计日志记录操作者（admin）/目标 user/memory_id/内容摘要。目标不存在 404。
    """
    entry = await memory_service.find_memory_by_id(memory_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="记忆不存在",
        )
    deleted = await memory_service.delete_user_memory(entry.user, memory_id)
    if not deleted.get("deleted"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="记忆不存在",
        )
    logger.info(
        "管理员删除记忆: operator=admin user=%s memory_id=%s content=%r",
        entry.user,
        memory_id,
        entry.content[:50],
    )
    return AdminMemoryDeleteResponse(
        success=True,
        user=entry.user,
        memory_id=memory_id,
        deleted=1,
    )
