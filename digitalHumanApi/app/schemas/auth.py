from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# 用户名：3-32 位字母/数字/下划线
USERNAME_PATTERN = r"^[a-zA-Z0-9_]{3,32}$"


class RegisterRequest(BaseModel):
    """用户注册请求。"""

    username: str = Field(
        ...,
        min_length=3,
        max_length=32,
        pattern=USERNAME_PATTERN,
        description="用户名（3-32 位字母/数字/下划线）",
    )
    password: str = Field(
        ...,
        min_length=6,
        max_length=64,
        description="密码（至少 6 位；bcrypt 上限 72 字节，超长由 service 层校验）",
    )
    nickname: Optional[str] = Field(
        None, max_length=32, description="昵称（可选）"
    )


class LoginRequest(BaseModel):
    """登录请求。"""

    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserResponse(BaseModel):
    """用户信息（绝不包含 password_hash 等敏感字段）。"""

    id: int
    username: str
    nickname: Optional[str] = None
    created_at: datetime
    last_login_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    """登录成功响应：JWT + 用户信息。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="token 有效期（秒）")
    user: UserResponse


class GuestResponse(BaseModel):
    """游客 token 签发响应（匿名，无持久化用户记录）。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="token 有效期（秒）")
    guest_id: str


class GuestIdentityResponse(BaseModel):
    """游客身份信息（/auth/me 对游客 token 的返回）。"""

    id: str
    is_guest: bool = True
