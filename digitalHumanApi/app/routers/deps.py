"""路由公共依赖。

目前仅含管理员 Token 校验（X-Admin-Token），
knowledge / announcement 等管理端路由复用，避免重复实现。
"""
from fastapi import Header, HTTPException, status

from app.core.config import settings


async def verify_admin_token(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> str:
    """简易管理员 Token 校验（X-Admin-Token 请求头）。"""
    expected = settings.ADMIN_API_TOKEN
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员 Token 未配置，请设置 ADMIN_API_TOKEN 环境变量",
        )
    if x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权访问",
        )
    return x_admin_token
