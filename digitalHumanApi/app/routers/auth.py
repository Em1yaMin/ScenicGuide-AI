from datetime import datetime, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_bearer
from app.schemas.auth import (
    AuthResponse,
    GuestIdentityResponse,
    GuestResponse,
    LoginRequest,
    RegisterRequest,
    UserResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/api/v1/auth", tags=["用户认证"])

# 认证接口对无效 token / 错误凭据一律严格 401（与对话接口的「降级游客」策略相反，
# 见 app/core/security.py resolve_dialogue_user 的决策注释）。


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    request: RegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """注册用户。用户名重复返回 409，密码强度不足由 schema 层拦截（422）。"""
    try:
        user = await auth_service.register_user(db, request)
    except auth_service.AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """登录：校验凭据，返回 JWT + 用户信息。凭据错误一律 401。"""
    user = await auth_service.authenticate_user(
        db, request.username, request.password
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()

    token, expires_in = auth_service.issue_user_token(user)
    return AuthResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=Union[UserResponse, GuestIdentityResponse])
async def me(
    authorization: str = Header(default="", description="Bearer token"),
    db: AsyncSession = Depends(get_db),
) -> Union[UserResponse, GuestIdentityResponse]:
    """解析当前登录用户（user token → 用户信息；guest token → 游客身份）。

    无效/过期 token 严格返回 401。
    """
    payload = decode_bearer(authorization)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 token",
        )
    if payload.get("typ") == "guest":
        return GuestIdentityResponse(id=payload.get("sub", ""))
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 载荷非法",
        )
    user = await auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已注销"
        )
    return UserResponse.model_validate(user)


@router.post("/guest", response_model=GuestResponse)
async def guest() -> GuestResponse:
    """签发短期游客 token（匿名，无持久化用户记录）。"""
    token, guest_id, expires_in = auth_service.create_guest_token()
    return GuestResponse(
        access_token=token, guest_id=guest_id, expires_in=expires_in
    )
