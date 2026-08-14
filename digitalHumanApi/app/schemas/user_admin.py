"""账号管理模块（2026-08-13 船长需求）的 Pydantic 请求/响应模型。

管理端 X-Admin-Token 鉴权（前缀 /api/admin/users，见 routers/user_admin.py）。
契约与前端 digitalHumanVue/src/types/userAdmin.ts 对齐；变更 schema 必须同步。
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.auth import USERNAME_PATTERN


class AdminUserItem(BaseModel):
    """管理端账号列表条目 / 详情（含对话/记忆条数概览）。"""

    id: int
    username: str
    nickname: Optional[str] = None
    is_disabled: bool  # 软禁用标记（禁用后登录/对话受控）
    disabled_at: Optional[datetime] = None
    dialogue_count: int  # 关联对话条数（dialogues.visitor_id == str(id)）
    memory_count: int  # 长期记忆条数（user_memory 向量库）
    created_at: datetime
    last_login_at: Optional[datetime] = None


class AdminUserListResponse(BaseModel):
    """管理端账号分页列表。"""

    items: list[AdminUserItem]
    total: int
    page: int
    page_size: int


class AdminUserCreateRequest(BaseModel):
    """创建账号请求（用户名唯一性 + 密码强度校验与注册接口一致）。"""

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
        description="初始密码（至少 6 位；bcrypt 上限 72 字节由 service 层校验）",
    )
    nickname: Optional[str] = Field(
        None, max_length=32, description="昵称（可选）"
    )


class AdminUserSetDisabledRequest(BaseModel):
    """禁用/启用账号请求（软禁用：登录/对话/记忆恢复受控）。"""

    disabled: bool = Field(..., description="true=禁用 / false=启用")


class AdminUserResetPasswordRequest(BaseModel):
    """重置密码请求：new_password 为空（缺省或空串）时由服务端生成临时密码。"""

    new_password: Optional[str] = Field(
        None,
        min_length=6,
        max_length=64,
        description="新密码（至少 6 位；留空则由系统生成临时密码）",
    )

    @field_validator("new_password", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Optional[str]) -> Optional[str]:
        return None if value == "" else value


class AdminUserResetPasswordResponse(BaseModel):
    """重置密码响应（临时密码仅本次返回，绝不落库/落日志）。"""

    user_id: int
    username: str
    generated: bool  # true=系统生成临时密码 / false=管理员指定
    new_password: str


class AdminUserDeleteResponse(BaseModel):
    """删除账号响应（级联清理影响如实汇报）。"""

    success: bool
    user_id: int
    username: str
    deleted_dialogues: int
    deleted_evaluations: int
    deleted_memories: int


class AdminAuditLogItem(BaseModel):
    """账号管理操作审计条目（落库 admin_audit_logs 表）。"""

    id: int
    operator: str  # 操作人标识（管理员账号 / admin-agent）
    action: str  # create/disable/enable/reset_password/delete
    target_user_id: Optional[int] = None
    target_username: str  # 目标用户名快照（用户删除后仍可查）
    detail: Optional[str] = None  # 结果摘要
    created_at: datetime


class AdminAuditLogListResponse(BaseModel):
    """账号管理操作审计分页列表。"""

    items: list[AdminAuditLogItem]
    total: int
    page: int
    page_size: int
