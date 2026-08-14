"""账号管理业务逻辑（2026-08-13 船长需求，管理端 X-Admin-Token 鉴权）。

能力：
- 用户列表（分页/搜索，复用 memory_admin.list_users 模式）+ 详情（含
  对话数/记忆条数概览）
- 创建用户（复用 auth_service.register_user：用户名唯一性 + 密码强度 +
  bcrypt 72 字节校验与注册接口一致）
- 禁用/启用（软禁用：登录/对话受控，见 routers/auth.py / routers/dialogue.py
  的禁用校验；禁用时间记录 disabled_at）
- 重置密码（管理员指定新密码或生成临时密码；临时密码仅响应返回一次）
- 删除用户（**单目标**，严禁批量；级联清理关联数据：dialogues +
  evaluations + visitors 记录 + user_memory 长期记忆，影响数如实汇报）

**保护性约束**：
- 系统管理员账号（settings.ADMIN_USERNAME，超管）不可禁用/删除
  （管理员登录走 ADMIN_USERNAME/ADMIN_PASSWORD 环境变量，与 users 表
  无关；同名 users 行视为超管保护对象）
- 全部敏感操作（创建/禁用启用/重置密码/删除）强制写落库审计日志
  （admin_audit_logs 表，见 admin_audit_service.record_audit），删除类
  操作在二次确认执行后才写审计
"""
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation
from app.models.user import User
from app.models.visitor import Visitor
from app.schemas.auth import RegisterRequest
from app.schemas.user_admin import (
    AdminUserCreateRequest,
    AdminUserItem,
    AdminUserListResponse,
)
from app.services import auth_service, memory_service

logger = logging.getLogger(__name__)


class UserAdminError(Exception):
    """账号管理业务错误；status_code 直接映射为 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def is_protected_username(username: str) -> bool:
    """超管保护：系统管理员账号（ADMIN_USERNAME）不可禁用/删除。"""
    return bool(settings.ADMIN_USERNAME) and username == settings.ADMIN_USERNAME


async def _dialogue_count(db: AsyncSession, user_id: int) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Dialogue)
            .where(Dialogue.visitor_id == str(user_id))
        )
    ).scalar_one()


def _to_item(user: User, dialogue_count: int, memory_count: int) -> AdminUserItem:
    return AdminUserItem(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        is_disabled=user.is_disabled,
        disabled_at=user.disabled_at,
        dialogue_count=dialogue_count,
        memory_count=memory_count,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


# ── 列表 / 详情 ───────────────────────────────────────────────────────────


async def list_users(
    db: AsyncSession,
    *,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> AdminUserListResponse:
    """已注册用户分页列表（keyword 模糊匹配用户名/昵称；含对话/记忆条数）。"""
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
    memory_counts = await memory_service.count_memories_per_user()
    items = []
    for u in users:
        dlg_count = await _dialogue_count(db, u.id)
        items.append(
            _to_item(u, dlg_count, memory_counts.get(str(u.id), 0))
        )
    return AdminUserListResponse(
        items=items, total=total, page=page, page_size=page_size
    )


async def get_user_item(db: AsyncSession, user_id: int) -> Optional[AdminUserItem]:
    """用户详情（含对话/记忆条数概览）；不存在返回 None。"""
    user = await db.get(User, user_id)
    if user is None:
        return None
    memory_count = await memory_service.count_user_memory(str(user_id))
    return _to_item(user, await _dialogue_count(db, user_id), memory_count)


# ── 创建 ─────────────────────────────────────────────────────────────────


async def create_user(
    db: AsyncSession, request: AdminUserCreateRequest
) -> User:
    """创建账号（唯一性/密码强度校验与注册接口一致，复用 register_user）。"""
    # RegisterRequest 字段与 AdminUserCreateRequest 对齐；AuthError(409/400)
    # 直接上抛（router 转 HTTPException）
    return await auth_service.register_user(
        db,
        RegisterRequest(
            username=request.username,
            password=request.password,
            nickname=request.nickname,
        ),
    )


# ── 禁用 / 启用（软禁用） ────────────────────────────────────────────────


async def set_user_disabled(
    db: AsyncSession, user_id: int, disabled: bool
) -> tuple[User, bool]:
    """禁用/启用账号（软禁用：登录/对话受控）。超管账号拒绝。

    Returns:
        (user, changed)：changed=False 表示状态本就一致（幂等 no-op，
        调用方不应写审计/重复记录）。
    """
    user = await db.get(User, user_id)
    if user is None:
        raise UserAdminError("用户不存在", 404)
    if disabled and is_protected_username(user.username):
        raise UserAdminError(
            f"系统管理员账号「{user.username}」受保护，不可禁用", 400
        )
    if user.is_disabled == disabled:
        # 幂等：状态已一致视为成功（无副作用，不写审计）
        return user, False
    user.is_disabled = disabled
    user.disabled_at = datetime.now(timezone.utc) if disabled else None
    await db.flush()
    return user, True


# ── 重置密码 ─────────────────────────────────────────────────────────────


def generate_temp_password() -> str:
    """生成 12 位临时密码（urlsafe 字符集，无易混淆字符问题）。"""
    return secrets.token_urlsafe(9)


async def reset_user_password(
    db: AsyncSession, user_id: int, new_password: Optional[str] = None
) -> dict:
    """重置密码：指定新密码或生成临时密码（临时密码仅响应返回一次）。

    Returns:
        {"user": User, "generated": bool, "new_password": str}
    """
    user = await db.get(User, user_id)
    if user is None:
        raise UserAdminError("用户不存在", 404)
    if new_password:
        if len(new_password.encode("utf-8")) > auth_service.BCRYPT_MAX_BYTES:
            raise UserAdminError(
                f"密码过长（bcrypt 上限 {auth_service.BCRYPT_MAX_BYTES} 字节，请缩短密码）",
                400,
            )
        plain = new_password
        generated = False
    else:
        plain = generate_temp_password()
        generated = True
    user.password_hash = auth_service.hash_password(plain)
    await db.flush()
    return {"user": user, "generated": generated, "new_password": plain}


# ── 删除（单目标 + 级联清理，严禁批量） ──────────────────────────────────


async def delete_user(db: AsyncSession, user_id: int) -> Optional[dict]:
    """删除账号并级联清理关联数据（单目标；删除前须展示影响概览）。

    级联范围（删除或保留需明确——本实现为级联清理，影响数如实汇报）：
    - dialogues 表中 visitor_id == str(id) 的对话（含其 evaluations）
    - visitors 表中对应 id 记录
    - user_memory 向量库中长期记忆

    Returns:
        None（用户不存在）或
        {"username", "deleted_dialogues", "deleted_evaluations", "deleted_memories"}
    """
    user = await db.get(User, user_id)
    if user is None:
        return None
    if is_protected_username(user.username):
        raise UserAdminError(
            f"系统管理员账号「{user.username}」受保护，不可删除", 400
        )
    vid = str(user_id)

    # 1. 关联对话的量化评估（evaluations.dialogue_id 外键 → 先删评估）
    dialogue_ids = select(Dialogue.id).where(Dialogue.visitor_id == vid)
    deleted_evaluations = (
        await db.execute(
            delete(Evaluation).where(Evaluation.dialogue_id.in_(dialogue_ids))
        )
    ).rowcount
    # 2. 对话
    deleted_dialogues = (
        await db.execute(delete(Dialogue).where(Dialogue.visitor_id == vid))
    ).rowcount
    # 3. visitors 记录（对话外键已清理）
    await db.execute(delete(Visitor).where(Visitor.id == vid))
    # 4. 长期记忆（user_memory 向量库）
    deleted_memories = await memory_service.delete_all_user_memories(vid)
    # 5. 用户行
    await db.delete(user)
    await db.flush()
    logger.info(
        "账号删除（级联清理）: user_id=%s username=%s dialogues=%d evaluations=%d memories=%d",
        user_id,
        user.username,
        deleted_dialogues,
        deleted_evaluations,
        deleted_memories,
    )
    return {
        "username": user.username,
        "deleted_dialogues": deleted_dialogues,
        "deleted_evaluations": deleted_evaluations,
        "deleted_memories": deleted_memories,
    }
