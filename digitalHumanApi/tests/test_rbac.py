"""管理端 RBAC 角色分级测试（批次6 C3 安全加固）。

覆盖（对应任务验收标准）:
- 配置解析：ADMIN_TOKENS 映射（token=role）、裸 token 视为 admin、非法角色忽略
- 角色解析：ADMIN_API_TOKEN 恒为 admin（向后兼容）；ADMIN_TOKENS 优先
- 鉴权行为：无 token 401 / 错 token 401 / admin 全通 / operator 查询放行 /
  operator 删除敏感接口（知识库删除/记忆删除）403 / 403 与 401 区分
- 角色层级：require_role("admin") 拒绝 operator，放行 admin
"""
from typing import AsyncGenerator

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.routers.deps import ROLE_ADMIN, ROLE_OPERATOR, _role_for_token

ADMIN_TOKEN = "admin-token-1"
OPERATOR_TOKEN = "operator-token-2"


@pytest.fixture(autouse=True)
def _setup_tokens(monkeypatch):
    """配置双角色：ADMIN_API_TOKEN（兼容）+ ADMIN_TOKENS 映射 operator。"""
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(
        settings,
        "ADMIN_TOKENS",
        {ADMIN_TOKEN: ROLE_ADMIN, OPERATOR_TOKEN: ROLE_OPERATOR},
    )


# ── 配置解析 ────────────────────────────────────────────────────────────


def test_parse_admin_tokens_mapping():
    """ADMIN_TOKENS 解析：token=角色 映射 + 裸 token 视为 admin + 非法角色忽略。"""
    from app.core.config import _parse_admin_tokens

    tokens = _parse_admin_tokens("tok1=admin, tok2=operator, bare")
    assert tokens == {"tok1": "admin", "tok2": "operator", "bare": "admin"}
    # 非法角色（非 admin/operator）被忽略
    tokens = _parse_admin_tokens("tok1=superuser,tok2=admin")
    assert tokens == {"tok2": "admin"}
    assert _parse_admin_tokens("") == {}
    assert _parse_admin_tokens(None) == {}


def test_role_for_token_compat_and_priority():
    """角色解析：ADMIN_TOKENS 优先，ADMIN_API_TOKEN 兜底 admin，未知 token 空。"""
    assert _role_for_token(ADMIN_TOKEN) == ROLE_ADMIN
    assert _role_for_token(OPERATOR_TOKEN) == ROLE_OPERATOR
    assert _role_for_token("some-other-token") == ""
    assert _role_for_token("") == ""


# ── HTTP 鉴权行为 ───────────────────────────────────────────────────────


@pytest.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    from app.core.database import get_db
    from app.main import app

    async def override_get_db():
        yield db_session
        await db_session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = __import__("httpx").ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


def _auth(token: str) -> dict:
    return {"X-Admin-Token": token}


@pytest.mark.asyncio
async def test_no_token_401(client: AsyncClient):
    """无 token 访问管理端点 401。"""
    resp = await client.get("/api/admin/knowledge")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_401(client: AsyncClient):
    """错误 token 401。"""
    resp = await client.get("/api/admin/knowledge", headers=_auth("wrong-token"))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_legacy_admin_token_fully_works(client: AsyncClient):
    """既有 ADMIN_API_TOKEN（无 ADMIN_TOKENS 场景）行为不变：全部操作放行。"""
    from app.core.config import settings as _s

    # 摘除 ADMIN_TOKENS，仅留 ADMIN_API_TOKEN（升级前部署形态）
    _s.ADMIN_TOKENS = {}
    resp = await client.get("/api/admin/knowledge", headers=_auth(ADMIN_TOKEN))
    assert resp.status_code == 200
    # 删除接口（admin 级）也放行
    resp = await client.delete("/api/admin/knowledge/9999", headers=_auth(ADMIN_TOKEN))
    assert resp.status_code == 404  # 过了 RBAC，目标不存在


@pytest.mark.asyncio
async def test_operator_can_query(client: AsyncClient):
    """operator 可访问查询类接口（知识库列表/记忆列表/公告列表）。"""
    resp = await client.get("/api/admin/knowledge", headers=_auth(OPERATOR_TOKEN))
    assert resp.status_code == 200
    resp = await client.get(
        "/api/admin/memories/users", headers=_auth(OPERATOR_TOKEN)
    )
    assert resp.status_code == 200
    resp = await client.get("/api/admin/announcements", headers=_auth(OPERATOR_TOKEN))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_operator_can_publish_announcement(client: AsyncClient):
    """operator 可发布公告（常规写操作，需求明确 operator 可做）。"""
    resp = await client.post(
        "/api/admin/announcements",
        json={"title": "临时公告", "content": "内容"},
        headers=_auth(OPERATOR_TOKEN),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_operator_forbidden_to_delete_knowledge(client: AsyncClient):
    """operator 删除知识库条目 403（删除类敏感操作需 admin）。"""
    resp = await client.delete(
        "/api/admin/knowledge/1", headers=_auth(OPERATOR_TOKEN)
    )
    assert resp.status_code == 403
    data = resp.json()
    assert "admin" in data["detail"]  # 明确提示所需角色（前端可展示）


@pytest.mark.asyncio
async def test_operator_forbidden_to_delete_memory(client: AsyncClient):
    """operator 删除记忆 403（删除类敏感操作需 admin）。"""
    resp = await client.delete(
        "/api/admin/memories/abc123", headers=_auth(OPERATOR_TOKEN)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_delete(client: AsyncClient, db_session):
    """admin 删除类接口放行（目标不存在时返回 404 而非 403，证明过了 RBAC）。"""
    resp = await client.delete("/api/admin/knowledge/9999", headers=_auth(ADMIN_TOKEN))
    assert resp.status_code == 404
    resp = await client.delete(
        "/api/admin/memories/legacy:no-such", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_operator_create_knowledge_allowed(client: AsyncClient, db_session):
    """operator 新增知识库条目放行（维持现状：仅删除类敏感操作需 admin）。"""
    resp = await client.post(
        "/api/admin/knowledge",
        json={"title": "新条目", "content": "内容", "category": "测试"},
        headers=_auth(OPERATOR_TOKEN),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_require_role_dependency_direct():
    """require_role 依赖本身：admin 放行、operator 403、无 token 401（HTTP 层已覆盖，此处验证工厂返回依赖可调用）。"""
    from app.routers.deps import require_role, verify_admin_token

    dep = require_role(ROLE_ADMIN)
    # 依赖是闭包函数，可被 FastAPI 依赖注入；验证其组合 verify_admin_token
    assert callable(dep)
    assert callable(verify_admin_token)
