"""路由公共依赖。

包含管理员 Token 校验（X-Admin-Token）与 RBAC 角色分级（批次6 C3 安全加固），
knowledge / announcement / memory_admin / admin_agent 等管理端路由复用。

角色模型（最小改动升级，前端暂不改、仍传同一 token）：
- ADMIN_API_TOKEN（既有配置）→ 恒为 admin 角色（向后兼容，现有 token 行为不变）
- ADMIN_TOKENS（新增映射，如 "token1=admin,token2=operator"）→ 按映射解析角色
- 角色分级：admin 可执行全部操作；operator 可执行查询/公告发布等常规操作，
  删除类敏感操作（知识库删除/记忆删除）要求 admin（403）
"""
from typing import Callable

from fastapi import Header, HTTPException, status

from app.core.config import settings

# ── 角色常量（管理端 RBAC） ─────────────────────────────────────────────
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"

# 角色层级：admin > operator（低权限请求高权限接口时 403）
_ROLE_RANK = {ROLE_OPERATOR: 1, ROLE_ADMIN: 2}


def _role_for_token(token: str) -> str:
    """解析 token 角色：ADMIN_TOKENS 映射优先，其次 ADMIN_API_TOKEN 兜底 admin。"""
    if token and settings.ADMIN_TOKENS and token in settings.ADMIN_TOKENS:
        return settings.ADMIN_TOKENS[token]
    if token and settings.ADMIN_API_TOKEN and token == settings.ADMIN_API_TOKEN:
        return ROLE_ADMIN
    return ""


async def verify_admin_token(
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> str:
    """管理员 Token 校验（X-Admin-Token 请求头），返回角色（admin/operator）。

    兼容既有行为：ADMIN_API_TOKEN 未配置时报 401（提示配置）；token 不匹配
    ADMIN_TOKENS 映射且不等于 ADMIN_API_TOKEN 时 401。返回值从「原样回显
    token」改为「角色字符串」——既有调用方均以 `_: str = Depends(...)` 忽略
    返回值，无行为影响。
    """
    if not settings.ADMIN_API_TOKEN and not settings.ADMIN_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员 Token 未配置，请设置 ADMIN_API_TOKEN 环境变量",
        )
    role = _role_for_token(x_admin_token)
    if not role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未授权访问",
        )
    return role


def require_role(min_role: str) -> Callable:
    """角色分级依赖工厂：require_role(\"admin\") 生成要求 admin 角色的依赖。

    用法与 verify_admin_token 一致（`_: str = Depends(require_role(\"admin\"))`），
    内部先做 token 校验再比较角色层级；权限不足返回 403（与 401 未授权区分，
    便于前端识别「已登录但权限不够」）。
    """

    async def _dependency(
        x_admin_token: str = Header(default="", alias="X-Admin-Token"),
    ) -> str:
        role = await verify_admin_token(x_admin_token)
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(min_role, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：该操作需要 {min_role} 角色",
            )
        return role

    return _dependency
