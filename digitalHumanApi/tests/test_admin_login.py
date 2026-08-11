"""管理员账号密码登录测试（批次9：替代 X-Admin-Token 输入形式）。

覆盖（对应任务验收标准）:
- 正确凭据签发管理员 JWT（typ=admin，载荷可区分普通用户/游客 token）
- 错误凭据 401（账号不存在与密码错误一致，不泄露账号是否存在）
- 管理员 JWT 访问管理接口放行（查询类 + 删除类 admin 级接口）
- X-Admin-Token 旧路径完全保留（向后兼容）
- require_role("admin") 对管理员 JWT 生效（operator 无法经 JWT 冒充）
- 普通用户 token（typ=user）不能访问管理接口（JWT 载荷类型区分）
"""

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123456"


@pytest.fixture(autouse=True)
def _admin_credentials(monkeypatch):
    """固定管理员账号密码（config 默认演示值，与 README 一致）。"""
    monkeypatch.setattr(settings, "ADMIN_USERNAME", ADMIN_USERNAME)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", ADMIN_PASSWORD)


@pytest.fixture(autouse=True)
def _reset_admin_login_limiter():
    """每个测试重置管理员登录限流器，避免跨测试污染（同 conftest 对 auth 的处理）。"""
    from app.routers import admin as admin_router

    admin_router._admin_login_limiter.reset()
    yield
    admin_router._admin_login_limiter.reset()


async def _login(client: AsyncClient) -> str:
    """管理员登录并返回 JWT。"""
    resp = await client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


# ── 登录 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_login_success_issues_admin_jwt(client: AsyncClient):
    """正确凭据：200 + 管理员 JWT（typ=admin，载荷区分普通用户）。"""
    resp = await client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["username"] == ADMIN_USERNAME
    assert data["access_token"]
    assert data["expires_in"] == settings.JWT_EXPIRES_MINUTES * 60
    payload = decode_access_token(data["access_token"])
    assert payload is not None
    assert payload["typ"] == "admin"  # 与普通用户（user）/游客（guest）区分
    assert payload["sub"] == ADMIN_USERNAME


@pytest.mark.asyncio
async def test_admin_login_wrong_password_401(client: AsyncClient):
    """错误密码：401。"""
    resp = await client.post(
        "/api/v1/admin/login",
        json={"username": ADMIN_USERNAME, "password": "wrong-password"},
    )
    assert resp.status_code == 401
    assert "密码" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_admin_login_wrong_username_401(client: AsyncClient):
    """账号不存在：401（与密码错误一致，不泄露账号是否存在）。"""
    resp = await client.post(
        "/api/v1/admin/login",
        json={"username": "nobody", "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_login_empty_credentials_401(client: AsyncClient):
    """空账号/空密码：不匹配任何凭据 → 401（与用户登录 LoginRequest 行为一致）。"""
    resp = await client.post(
        "/api/v1/admin/login", json={"username": "", "password": ""}
    )
    assert resp.status_code == 401


# ── 管理员 JWT 访问管理接口 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_jwt_allows_query_api(client: AsyncClient):
    """管理员 JWT 经 Authorization Bearer 访问查询类管理接口放行。"""
    token = await _login(client)
    resp = await client.get(
        "/api/admin/knowledge", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_jwt_allows_admin_level_delete(client: AsyncClient):
    """管理员 JWT 满足 require_role(\"admin\")：删除类接口过 RBAC（404=目标不存在）。"""
    token = await _login(client)
    resp = await client.delete(
        "/api/admin/knowledge/9999", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404
    resp = await client.delete(
        "/api/admin/memories/legacy:no-such",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_token_cannot_access_admin_api(client: AsyncClient):
    """普通用户 token（typ=user）访问管理接口 401——JWT 载荷类型区分管理员。"""
    user_token = create_access_token(subject="1", token_type="user")[0]
    resp = await client.get(
        "/api/admin/knowledge", headers={"Authorization": f"Bearer {user_token}"}
    )
    assert resp.status_code == 401
    # 游客 token 同理
    guest_token = create_access_token(
        subject="guest-abc", token_type="guest", expires_minutes=30
    )[0]
    resp = await client.get(
        "/api/admin/knowledge", headers={"Authorization": f"Bearer {guest_token}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_no_credentials_401(client: AsyncClient):
    """无任何凭据访问管理接口 401。"""
    resp = await client.get("/api/admin/knowledge")
    assert resp.status_code == 401


# ── X-Admin-Token 向后兼容 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_x_admin_token_still_works(client: AsyncClient, monkeypatch):
    """X-Admin-Token 旧路径完全保留：ADMIN_API_TOKEN 配置下行为不变。"""
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "legacy-admin-token")
    monkeypatch.setattr(settings, "ADMIN_TOKENS", {})
    resp = await client.get(
        "/api/admin/knowledge", headers={"X-Admin-Token": "legacy-admin-token"}
    )
    assert resp.status_code == 200
    resp = await client.delete(
        "/api/admin/knowledge/9999", headers={"X-Admin-Token": "legacy-admin-token"}
    )
    assert resp.status_code == 404  # 过了 RBAC，目标不存在
    # 错误 token 仍 401
    resp = await client.get(
        "/api/admin/knowledge", headers={"X-Admin-Token": "wrong-token"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_x_admin_token_operator_role_unchanged(client: AsyncClient, monkeypatch):
    """X-Admin-Token 的 operator 角色分级不受 JWT 路径影响（删除类仍 403）。"""
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "admin-token")
    monkeypatch.setattr(
        settings, "ADMIN_TOKENS", {"op-token": "operator"}
    )
    resp = await client.get(
        "/api/admin/knowledge", headers={"X-Admin-Token": "op-token"}
    )
    assert resp.status_code == 200
    resp = await client.delete(
        "/api/admin/knowledge/1", headers={"X-Admin-Token": "op-token"}
    )
    assert resp.status_code == 403
