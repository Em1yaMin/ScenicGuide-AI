"""路由公共依赖。

包含管理端接口鉴权（双路径，批次9 起）与 RBAC 角色分级（批次6 C3 安全加固），
knowledge / announcement / memory_admin / admin_agent 等管理端路由复用。

鉴权双路径（任一命中即放行）：
- X-Admin-Token 请求头（向后兼容：内部调用、管理员 agent 工具、脚本不受影响）
- Authorization Bearer 管理员 JWT（管理员账号密码登录签发，typ=admin，
  见 app/routers/admin.py POST /api/v1/admin/login）

角色模型（最小改动升级，向后兼容）：
- ADMIN_API_TOKEN（既有配置）→ 恒为 admin 角色（向后兼容，现有 token 行为不变）
- ADMIN_TOKENS（新增映射，如 "token1=admin,token2=operator"）→ 按映射解析角色
- 管理员 JWT → 恒为 admin 角色（账号登录即最高权限）
- 角色分级：admin 可执行全部操作；operator 可执行查询/公告发布等常规操作，
  删除类敏感操作（知识库删除/记忆删除）要求 admin（403）
"""
from typing import Callable

from fastapi import Header, HTTPException, status

from app.core.config import settings
from app.core.security import decode_bearer

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
    authorization: str = Header(default="", description="Authorization: Bearer 管理员 JWT"),
) -> str:
    """管理端接口鉴权（双路径，批次9 起）：

    ① X-Admin-Token 请求头（向后兼容：内部调用、管理员 agent 工具、脚本、
       既有部署行为完全不变）；
    ② Authorization Bearer 管理员 JWT（管理员账号密码登录签发，typ=admin，
       见 app/routers/admin.py POST /api/v1/admin/login）。

    两者命中其一即放行，返回角色（admin/operator）。管理员 JWT 恒为 admin
    角色（账号登录即最高权限）。
    """
    # 路径一：X-Admin-Token（既有 ADMIN_TOKENS 映射 / ADMIN_API_TOKEN 兜底）
    role = _role_for_token(x_admin_token)
    if role:
        return role
    # 路径二：管理员 JWT（Authorization Bearer，typ=admin 区分普通用户/游客 token）
    payload = decode_bearer(authorization)
    if payload is not None and payload.get("typ") == "admin":
        return ROLE_ADMIN
    if not settings.ADMIN_API_TOKEN and not settings.ADMIN_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "管理员认证未配置，请设置 ADMIN_API_TOKEN / ADMIN_TOKENS "
                "环境变量，或使用管理员账号密码登录"
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未授权访问",
    )


def require_role(min_role: str) -> Callable:
    """角色分级依赖工厂：require_role(\"admin\") 生成要求 admin 角色的依赖。

    用法与 verify_admin_token 一致（`_: str = Depends(require_role(\"admin\"))`），
    内部先做 token 校验再比较角色层级；权限不足返回 403（与 401 未授权区分，
    便于前端识别「已登录但权限不够」）。
    """

    async def _dependency(
        x_admin_token: str = Header(default="", alias="X-Admin-Token"),
        authorization: str = Header(default="", description="Authorization: Bearer 管理员 JWT"),
    ) -> str:
        role = await verify_admin_token(x_admin_token, authorization)
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(min_role, 0):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：该操作需要 {min_role} 角色",
            )
        return role

    return _dependency
