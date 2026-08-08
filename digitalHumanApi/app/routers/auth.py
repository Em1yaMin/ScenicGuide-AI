from datetime import datetime, timezone
from typing import Optional, Union

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limiter import AuthRateLimiter, client_ip
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

# 认证端点进程内限流器（批次6 C3 安全加固，见 app/core/rate_limiter.py）：
# 按 IP + 账号维度，失败达阈值进冷却（429）；登录成功清零防误伤；
# guest 端点纯 IP 频率限制。AUTH_RATE_LIMIT_ENABLED=false 整体关闭。
# 测试通过 monkeypatch 替换本模块 _limiter 为小参数实例控制阈值。
_limiter = AuthRateLimiter(
    max_failures=settings.AUTH_RATE_LIMIT_MAX_FAILURES,
    window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
    cooldown_seconds=settings.AUTH_RATE_LIMIT_COOLDOWN_SECONDS,
    guest_max_per_window=settings.AUTH_GUEST_MAX_PER_MINUTE,
    guest_window_seconds=60,
)


class _RateLimited(HTTPException):
    """429 限流拒绝：detail 明确提示 + Retry-After 头（前端可展示倒计时）。"""

    def __init__(self, remaining: int) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"尝试过于频繁，请 {remaining} 秒后重试",
            headers={"Retry-After": str(remaining)},
        )


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    request: RegisterRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """注册用户。用户名重复返回 409，密码强度不足由 schema 层拦截（422）。"""
    ip = client_ip(req)
    if settings.AUTH_RATE_LIMIT_ENABLED and _limiter.is_blocked(ip, request.username):
        raise _RateLimited(_limiter.cooldown_remaining(ip, request.username))
    try:
        user = await auth_service.register_user(db, request)
    except auth_service.AuthError as e:
        # 注册失败（用户名重复等）计入失败次数，防批量注册探测
        if settings.AUTH_RATE_LIMIT_ENABLED:
            _limiter.record_failure(ip, request.username)
        raise HTTPException(status_code=e.status_code, detail=e.message)
    if settings.AUTH_RATE_LIMIT_ENABLED:
        _limiter.record_success(ip, request.username)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=AuthResponse)
async def login(
    request: LoginRequest,
    req: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    """登录：校验凭据，返回 JWT + 用户信息。凭据错误一律 401。"""
    ip = client_ip(req)
    if settings.AUTH_RATE_LIMIT_ENABLED and _limiter.is_blocked(ip, request.username):
        raise _RateLimited(_limiter.cooldown_remaining(ip, request.username))
    user = await auth_service.authenticate_user(
        db, request.username, request.password
    )
    if user is None:
        # 凭据错误（含用户名不存在）计入失败次数，达阈值进入冷却
        if settings.AUTH_RATE_LIMIT_ENABLED:
            _limiter.record_failure(ip, request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    if settings.AUTH_RATE_LIMIT_ENABLED:
        # 登录成功清零失败计数，防误伤正常用户
        _limiter.record_success(ip, request.username)
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
async def guest(req: Request) -> GuestResponse:
    """签发短期游客 token（匿名，无持久化用户记录）。"""
    ip = client_ip(req)
    # guest 无失败场景：纯 IP 维度频率限制（固定窗口，防 token 刷取）
    if settings.AUTH_RATE_LIMIT_ENABLED and _limiter.is_guest_blocked(ip):
        # 剩余秒数随固定窗口位置真实变化（非固定阈值），前端可准确倒计时
        raise _RateLimited(_limiter.guest_retry_after(ip))
    token, guest_id, expires_in = auth_service.create_guest_token()
    if settings.AUTH_RATE_LIMIT_ENABLED:
        _limiter.record_guest_request(ip)
    return GuestResponse(
        access_token=token, guest_id=guest_id, expires_in=expires_in
    )
