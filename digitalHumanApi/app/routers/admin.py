from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limiter import AuthRateLimiter, client_ip
from app.core.security import create_access_token
from app.schemas.admin import (
    AdminLoginRequest,
    AdminLoginResponse,
    DashboardResponse,
    ReportResponse,
)
from app.services.admin_service import get_dashboard_stats, get_report_data

router = APIRouter(prefix="/api/v1/admin", tags=["管理后台API"])

# 管理员登录端点进程内限流器（批次9，与 /api/v1/auth 同模式，防爆破）：
# 按 IP + 账号维度，失败达阈值进冷却（429）；登录成功清零防误伤。
_admin_login_limiter = AuthRateLimiter(
    max_failures=settings.AUTH_RATE_LIMIT_MAX_FAILURES,
    window_seconds=settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
    cooldown_seconds=settings.AUTH_RATE_LIMIT_COOLDOWN_SECONDS,
    guest_max_per_window=settings.AUTH_GUEST_MAX_PER_MINUTE,
    guest_window_seconds=60,
)


class _RateLimited(HTTPException):
    """429 限流拒绝（与 auth.py 同型：detail 明确提示 + Retry-After 头）。"""

    def __init__(self, remaining: int) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"尝试过于频繁，请 {remaining} 秒后重试",
            headers={"Retry-After": str(remaining)},
        )


@router.post("/login", response_model=AdminLoginResponse)
async def admin_login(
    request: AdminLoginRequest, req: Request
) -> AdminLoginResponse:
    """管理员账号密码登录（批次9，2026-08-10 船长指示：替代 X-Admin-Token
    输入形式，改为主页账号密码登录后跳转管理后台）。

    凭据来自环境变量 ADMIN_USERNAME / ADMIN_PASSWORD（config.py，默认演示
    值见 README）；校验通过签发管理员 JWT（typ=admin，sub=账号），管理端
    接口经 Authorization Bearer 透传（deps.verify_admin_token 双路径，旧
    X-Admin-Token 完全保留向后兼容）。凭据错误一律 401（不泄露账号是否存在）。
    """
    ip = client_ip(req)
    if (
        settings.AUTH_RATE_LIMIT_ENABLED
        and _admin_login_limiter.is_blocked(ip, request.username)
    ):
        raise _RateLimited(
            _admin_login_limiter.cooldown_remaining(ip, request.username)
        )
    ok = (
        request.username == settings.ADMIN_USERNAME
        and request.password == settings.ADMIN_PASSWORD
    )
    if not ok:
        # 凭据错误（含账号不存在）计入失败次数，达阈值进入冷却
        if settings.AUTH_RATE_LIMIT_ENABLED:
            _admin_login_limiter.record_failure(ip, request.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误"
        )
    if settings.AUTH_RATE_LIMIT_ENABLED:
        # 登录成功清零失败计数，防误伤正常使用
        _admin_login_limiter.record_success(ip, request.username)
    token, expires_minutes = create_access_token(
        subject=settings.ADMIN_USERNAME, token_type="admin"
    )
    return AdminLoginResponse(
        access_token=token,
        expires_in=expires_minutes * 60,
        username=settings.ADMIN_USERNAME,
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    return await get_dashboard_stats(db)


@router.get("/report", response_model=ReportResponse)
async def report(
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    return await get_report_data(db)
