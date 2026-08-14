"""账号管理路由（2026-08-13 船长需求，管理端 X-Admin-Token 鉴权）。

端点（前缀 /api/admin/users）：
- GET    /users                    — 账号分页列表（keyword 搜索；含对话/记忆条数）
- GET    /users/{user_id}          — 账号详情（含对话/记忆条数概览）
- POST   /users                    — 创建账号（用户名唯一性 + 密码强度）
- PUT    /users/{user_id}/disabled — 禁用/启用（软禁用：登录/对话受控）
- POST   /users/{user_id}/reset-password — 重置密码（指定新密码或生成临时密码）
- DELETE /users/{user_id}          — 删除账号（**单目标**，级联清理关联数据）
- GET    /users/audit-logs         — 账号管理操作审计日志（分页/筛选）

**权限分级（RBAC，批次6 C3）**：列表/详情/审计查询为 operator 可读；
创建为常规写操作（operator）；**禁用/启用/重置密码/删除为敏感操作，
要求 admin 角色**（403）。

**保护性约束**：系统管理员账号（ADMIN_USERNAME，超管）不可禁用/删除；
删除为单目标接口，无批量路径。

**审计（落库强制）**：全部敏感操作（创建/禁用启用/重置密码/删除）经
admin_audit_service.record_audit 写入 admin_audit_logs 表（操作人 = 管理员
账号名；X-Admin-Token 路径记录固定标签 x-admin-token、不落凭证明文——
审计列表 operator 角色可读，防 token 经审计面泄露），与业务同一事务提交；
删除类操作由管理员 agent 两阶段二次确认（confirm.py）执行后同样落审计
（operator=admin-agent）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import decode_bearer
from app.models.user import User
from app.routers.deps import require_role, verify_admin_token
from app.schemas.user_admin import (
    AdminAuditLogListResponse,
    AdminUserCreateRequest,
    AdminUserDeleteResponse,
    AdminUserItem,
    AdminUserListResponse,
    AdminUserResetPasswordRequest,
    AdminUserResetPasswordResponse,
    AdminUserSetDisabledRequest,
)
from app.services import admin_audit_service, auth_service, user_admin_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/users", tags=["账号管理"])


def _resolve_operator(
    x_admin_token: str = "",
    authorization: str = "",
) -> str:
    """解析操作人标识（审计用）：优先管理员 JWT 的账号名；X-Admin-Token 路径
    使用固定标签（不落凭证明文，防 operator 经审计列表读取 admin token 提权）。"""
    payload = decode_bearer(authorization)
    if payload is not None and payload.get("typ") == "admin":
        return str(payload.get("sub") or "admin")
    return "x-admin-token" if x_admin_token else "admin"


def _http_error(e: user_admin_service.UserAdminError) -> HTTPException:
    return HTTPException(status_code=e.status_code, detail=e.message)


# ── 列表 / 详情 / 审计（operator 可读） ──────────────────────────────────


@router.get("", response_model=AdminUserListResponse)
async def list_users(
    keyword: Optional[str] = Query(None, description="用户名/昵称关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminUserListResponse:
    """账号分页列表（含对话数/记忆条数概览；keyword 模糊匹配用户名/昵称）。"""
    return await user_admin_service.list_users(
        db, keyword=keyword, page=page, page_size=page_size
    )


@router.get("/audit-logs", response_model=AdminAuditLogListResponse)
async def list_audit_logs(
    keyword: Optional[str] = Query(None, description="操作人/目标用户名/结果关键词"),
    action: Optional[str] = Query(None, description="动作筛选：create/disable/enable/reset_password/delete"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminAuditLogListResponse:
    """账号管理操作审计日志（分页/筛选；全部敏感操作强制落库）。

    注意：本路由必须声明在 /{user_id} 之前（Starlette 按注册顺序匹配路径，
    {user_id} 的 int 转换失败不会回退到本路由）。
    """
    return await admin_audit_service.list_audit_logs(
        db, keyword=keyword, action=action, page=page, page_size=page_size
    )


@router.get("/{user_id}", response_model=AdminUserItem)
async def get_user_detail(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminUserItem:
    """账号详情（含对话数/记忆条数概览；不存在 404）。"""
    item = await user_admin_service.get_user_item(db, user_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在"
        )
    return item


# ── 创建（operator） ─────────────────────────────────────────────────────


@router.post(
    "", response_model=AdminUserItem, status_code=status.HTTP_201_CREATED
)
async def create_user(
    request: AdminUserCreateRequest,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> AdminUserItem:
    """创建账号（用户名重复 409；密码强度不足 422；敏感操作落审计）。"""
    operator = _resolve_operator(x_admin_token, authorization)
    try:
        user = await user_admin_service.create_user(db, request)
    except auth_service.AuthError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    await admin_audit_service.record_audit(
        db,
        operator=operator,
        action=admin_audit_service.ACTION_CREATE,
        target_user_id=user.id,
        target_username=user.username,
        detail="ok",
    )
    item = await user_admin_service.get_user_item(db, user.id)
    assert item is not None
    return item


# ── 禁用 / 启用（admin 角色，敏感） ──────────────────────────────────────


@router.put("/{user_id}/disabled", response_model=AdminUserItem)
async def set_user_disabled(
    user_id: int,
    request: AdminUserSetDisabledRequest,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role("admin")),
) -> AdminUserItem:
    """禁用/启用账号（软禁用：登录/对话受控；超管账号 400；落审计）。"""
    operator = _resolve_operator(x_admin_token, authorization)
    try:
        user, changed = await user_admin_service.set_user_disabled(
            db, user_id, request.disabled
        )
    except user_admin_service.UserAdminError as e:
        raise _http_error(e)
    if changed:
        await admin_audit_service.record_audit(
            db,
            operator=operator,
            action=(
                admin_audit_service.ACTION_DISABLE
                if request.disabled
                else admin_audit_service.ACTION_ENABLE
            ),
            target_user_id=user.id,
            target_username=user.username,
            detail="ok",
        )
    item = await user_admin_service.get_user_item(db, user.id)
    assert item is not None
    return item


# ── 重置密码（admin 角色，敏感） ─────────────────────────────────────────


@router.post(
    "/{user_id}/reset-password", response_model=AdminUserResetPasswordResponse
)
async def reset_user_password(
    user_id: int,
    request: AdminUserResetPasswordRequest,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role("admin")),
) -> AdminUserResetPasswordResponse:
    """重置密码（指定新密码或生成临时密码——临时密码仅本次响应返回；落审计）。"""
    operator = _resolve_operator(x_admin_token, authorization)
    try:
        result = await user_admin_service.reset_user_password(
            db, user_id, request.new_password
        )
    except user_admin_service.UserAdminError as e:
        raise _http_error(e)
    user = result["user"]
    await admin_audit_service.record_audit(
        db,
        operator=operator,
        action=admin_audit_service.ACTION_RESET_PASSWORD,
        target_user_id=user.id,
        target_username=user.username,
        detail="ok generated" if result["generated"] else "ok",
    )
    return AdminUserResetPasswordResponse(
        user_id=user.id,
        username=user.username,
        generated=result["generated"],
        new_password=result["new_password"],
    )


# ── 删除（admin 角色，单目标 + 级联清理） ────────────────────────────────


@router.delete("/{user_id}", response_model=AdminUserDeleteResponse)
async def delete_user(
    user_id: int,
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
    authorization: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
    _: str = Depends(require_role("admin")),
) -> AdminUserDeleteResponse:
    """删除账号（**单目标**，严禁批量；级联清理对话/评估/记忆，影响如实汇报）。

    删除影响展示：调用方应先经 GET /users/{user_id} 查看对话/记忆条数概览
    （或本接口返回的 deleted_* 计数）。超管账号 400；不存在 404；落审计。
    """
    operator = _resolve_operator(x_admin_token, authorization)
    try:
        result = await user_admin_service.delete_user(db, user_id)
    except user_admin_service.UserAdminError as e:
        raise _http_error(e)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在"
        )
    await admin_audit_service.record_audit(
        db,
        operator=operator,
        action=admin_audit_service.ACTION_DELETE,
        target_user_id=user_id,
        target_username=result["username"],
        detail=(
            f"ok dialogues={result['deleted_dialogues']} "
            f"evaluations={result['deleted_evaluations']} "
            f"memories={result['deleted_memories']}"
        ),
    )
    return AdminUserDeleteResponse(
        success=True,
        user_id=user_id,
        username=result["username"],
        deleted_dialogues=result["deleted_dialogues"],
        deleted_evaluations=result["deleted_evaluations"],
        deleted_memories=result["deleted_memories"],
    )
