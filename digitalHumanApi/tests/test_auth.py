"""用户体系测试：注册/登录/JWT/游客 token/对话接口用户标识解析。

覆盖（对应任务验收标准）:
- 注册（成功、重名 409、弱密码 422）
- 登录（成功签发 JWT、错误密码 401）
- /auth/me（有效 token 200、无效/缺失 token 401）
- 游客 token 签发与使用
- 对话接口从 token 解析用户（纯函数层）+ 无 token 向后兼容（HTTP 层）
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, resolve_dialogue_user


# --- 注册 ---

@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "secret123", "nickname": "小爱"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "alice"
    assert data["nickname"] == "小爱"
    assert data["id"] > 0
    # 绝不返回密码相关字段
    assert "password_hash" not in data
    assert "password" not in data


@pytest.mark.asyncio
async def test_register_duplicate_username_rejected(client: AsyncClient):
    payload = {"username": "bob", "password": "secret123"}
    r1 = await client.post("/api/v1/auth/register", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/api/v1/auth/register", json=payload)
    assert r2.status_code == 409
    assert "已存在" in r2.json()["detail"]


@pytest.mark.asyncio
async def test_register_weak_password_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "carol", "password": "12345"},
    )
    assert resp.status_code == 422  # schema 层 min_length=6


@pytest.mark.asyncio
async def test_register_invalid_username_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"username": "bad name!", "password": "secret123"},
    )
    assert resp.status_code == 422  # 仅允许字母/数字/下划线


# --- 登录 ---

@pytest.mark.asyncio
async def test_login_success_returns_jwt(client: AsyncClient):
    await client.post(
        "/api/v1/auth/register",
        json={"username": "dave", "password": "secret123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "dave", "password": "secret123"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["expires_in"] == 1440 * 60
    assert data["user"]["username"] == "dave"
    assert data["user"]["last_login_at"] is not None  # 登录时间已更新


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client: AsyncClient):
    await client.post(
        "/api/v1/auth/register",
        json={"username": "eve", "password": "secret123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "eve", "password": "wrong-password"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_user_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "ghost", "password": "whatever"},
    )
    assert resp.status_code == 401


# --- /auth/me ---

@pytest.mark.asyncio
async def test_me_with_valid_token(client: AsyncClient):
    await client.post(
        "/api/v1/auth/register",
        json={"username": "frank", "password": "secret123"},
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "frank", "password": "secret123"},
    )
    token = login.json()["access_token"]
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200
    assert me.json()["username"] == "frank"


@pytest.mark.asyncio
async def test_me_with_invalid_token_401(client: AsyncClient):
    resp = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not.a.valid.token"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_without_token_401(client: AsyncClient):
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


# --- 游客 token ---

@pytest.mark.asyncio
async def test_guest_token_issue_and_use(client: AsyncClient):
    resp = await client.post("/api/v1/auth/guest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["guest_id"].startswith("guest-")
    assert data["expires_in"] == 30 * 60  # 短期

    # 游客 token 可用于 /auth/me（返回游客身份而非用户信息）
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {data['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == data["guest_id"]
    assert me.json()["is_guest"] is True


# --- 对话接口用户标识解析（纯函数层）---

def test_resolve_no_token_returns_none():
    assert resolve_dialogue_user(None) is None
    assert resolve_dialogue_user("") is None


def test_resolve_user_token_returns_user_id():
    token, _ = create_access_token(subject="42", token_type="user")
    assert resolve_dialogue_user(f"Bearer {token}") == "42"


def test_resolve_guest_token_returns_guest_id():
    token, _ = create_access_token(subject="guest-abc123", token_type="guest")
    assert resolve_dialogue_user(f"Bearer {token}") == "guest-abc123"


def test_resolve_invalid_token_degrades_to_visitor():
    # 决策：对话接口无效 token 降级为游客并告警（不阻断对话），认证接口才严格 401
    assert resolve_dialogue_user("Bearer not.a.valid.token") == "visitor"


# --- 对话接口向后兼容（HTTP 层）---

@pytest.mark.asyncio
async def test_chat_without_token_still_works(client: AsyncClient):
    """无 token 请求仍正常：不 401，Dify 未配置时返回兜底文案。"""
    resp = await client.post("/api/v1/dialogue/chat", json={"query": "你好"})
    assert resp.status_code == 200
    assert resp.json()["answer"]


@pytest.mark.asyncio
async def test_chat_with_valid_token_works(client: AsyncClient, db_session: AsyncSession):
    """携带有效 token 的对话请求正常走通（token 解析不阻断主链路）。"""
    # 先落库用户 id=7（对话门禁：注册用户 id 须在 users 表中存在，否则 403）
    from sqlalchemy import select

    from app.models.user import User
    from app.services import auth_service

    user = User(
        id=7,
        username="tok_user",
        password_hash=auth_service.hash_password("secret123"),
    )
    db_session.add(user)
    await db_session.commit()
    assert (await db_session.execute(select(User).where(User.id == 7))).scalar_one()

    token, _ = create_access_token(subject="7", token_type="user")
    resp = await client.post(
        "/api/v1/dialogue/chat",
        json={"query": "你好"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["answer"]


@pytest.mark.asyncio
async def test_chat_with_invalid_token_degrades(client: AsyncClient):
    """无效 token 对话请求降级为游客，仍返回 200。"""
    resp = await client.post(
        "/api/v1/dialogue/chat",
        json={"query": "你好"},
        headers={"Authorization": "Bearer bad.token.value"},
    )
    assert resp.status_code == 200


# --- 密码存储安全性（服务层）---

@pytest.mark.asyncio
async def test_password_stored_hashed_not_plaintext(db_session: AsyncSession):
    from sqlalchemy import select

    from app.models.user import User
    from app.schemas.auth import RegisterRequest
    from app.services.auth_service import register_user

    user = await register_user(
        db_session, RegisterRequest(username="secure_user", password="hunter2secret")
    )
    await db_session.flush()

    stored = await db_session.execute(
        select(User).where(User.username == "secure_user")
    )
    row = stored.scalar_one()
    assert row.password_hash != "hunter2secret"
    assert row.password_hash.startswith("$2")  # bcrypt 哈希前缀
