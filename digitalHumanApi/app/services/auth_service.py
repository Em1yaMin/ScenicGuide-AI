"""用户认证业务逻辑：注册、登录校验、JWT 签发、游客 token。

密码绝不落明文/日志；日志中不得出现 token。
"""

import logging
from typing import Optional, Tuple
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import RegisterRequest

logger = logging.getLogger(__name__)

# bcrypt 输入上限（72 字节）；pwdlib[bcrypt] 超长会截断，注册时显式拒绝
BCRYPT_MAX_BYTES = 72


class AuthError(Exception):
    """认证业务错误；status_code 直接映射为 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def register_user(db: AsyncSession, request: RegisterRequest) -> User:
    """创建用户。用户名已存在（预检 + 唯一约束兜底）→ AuthError(409)。

    仅 flush()，由 get_db() 依赖统一 commit。
    """
    # bcrypt 72 字节上限：按 UTF-8 字节数校验，拒绝超长密码（不截断，避免歧义）
    if len(request.password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise AuthError(
            f"密码过长（bcrypt 上限 {BCRYPT_MAX_BYTES} 字节，请缩短密码）", 400
        )

    result = await db.execute(select(User).where(User.username == request.username))
    if result.scalar_one_or_none() is not None:
        raise AuthError("用户名已存在", 409)

    user = User(
        username=request.username,
        password_hash=hash_password(request.password),
        nickname=request.nickname,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # 并发注册竞态兜底：唯一约束冲突
        logger.warning("用户名注册竞态冲突（唯一约束）: %s", request.username)
        raise AuthError("用户名已存在", 409)
    logger.info("新用户注册成功: %s (id=%s)", request.username, user.id)
    return user


async def authenticate_user(
    db: AsyncSession, username: str, password: str
) -> Optional[User]:
    """校验用户名 + 密码，成功返回用户，失败返回 None。"""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user


async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


def issue_user_token(user: User) -> Tuple[str, int]:
    """为登录用户签发 JWT（sub=用户 id），返回 (token, 有效期秒)。"""
    token, expires_minutes = create_access_token(
        subject=str(user.id), token_type="user"
    )
    return token, expires_minutes * 60


def create_guest_token() -> Tuple[str, str, int]:
    """签发短期游客 token（匿名，无持久化用户记录）。

    返回 (token, guest_id, 有效期秒)。guest_id 形如 guest-<uuid>，
    直接作为对话接口的 Dify user 标识，同一游客在一次会话内可关联。
    """
    guest_id = f"guest-{uuid4().hex}"
    token, expires_minutes = create_access_token(
        subject=guest_id,
        token_type="guest",
        expires_minutes=settings.GUEST_TOKEN_EXPIRES_MINUTES,
    )
    logger.debug("签发游客 token: %s（有效期 %d 分钟）", guest_id, expires_minutes)
    return token, guest_id, expires_minutes * 60
