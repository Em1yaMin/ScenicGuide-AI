"""账号管理 API（2026-08-13 船长需求）测试。

覆盖：
1. 鉴权：无 token / 错误 token → 401（全部端点）
2. 列表：分页/搜索 + 对话数/记忆条数概览 + is_disabled
3. 详情：含计数；404
4. 创建：成功 201 / 重名 409 / 弱密码 422 / 非法用户名 422
5. 禁用/启用：禁用后登录 403、/auth/me 403、对话 403（登录/对话受控）；
   启用恢复登录；幂等；404；超管账号（ADMIN_USERNAME）保护 400
6. 重置密码：指定新密码 / 生成临时密码（仅响应返回一次，可立即登录）
7. 删除：级联清理（dialogues/evaluations/visitors/长期记忆）、404、
   超管保护 400、**单目标**（无批量端点）
8. 审计日志：敏感操作全部落库（create/disable/enable/reset_password/delete）、
   列表/动作筛选/关键词搜索/分页
9. RBAC：operator 可读列表/详情/审计，禁用/重置/删除要求 admin（403）
"""
import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rag
from app.core.config import settings
from app.core.security import create_access_token
from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.models.user import User
from app.models.visitor import Visitor
from app.services import auth_service, memory_service
from app.services.memory_service import MemoryEntry

ADMIN_TOKEN = "test-admin-token"
OPERATOR_TOKEN = "op-token"
API = "/api/admin/users"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)


@pytest.fixture(autouse=True)
def _isolate_memory_store(tmp_path, monkeypatch):
    """每个测试使用独立的 user_memory 持久化目录（避免污染真实 rag_data 与跨测试串扰）。"""
    monkeypatch.setattr(rag, "RAG_DATA_DIR", tmp_path)
    with rag._vectorstore_lock:
        rag._vectorstores.clear()
    yield
    with rag._vectorstore_lock:
        rag._vectorstores.clear()


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {"X-Admin-Token": token}


async def _seed_user(
    db_session: AsyncSession,
    username: str,
    nickname: str | None = None,
    *,
    disabled: bool = False,
) -> int:
    """直接向测试库插入注册用户，返回 id。"""
    from datetime import datetime, timezone

    user = User(
        username=username,
        password_hash=auth_service.hash_password("secret123"),
        nickname=nickname,
        is_disabled=disabled,
        disabled_at=datetime.now(timezone.utc) if disabled else None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user.id


async def _seed_dialogue(db_session: AsyncSession, user_id: int, content: str = "你好") -> int:
    """写入用户的一轮对话（visitor 记录按需 upsert），返回 dialogue id。"""
    vid = str(user_id)
    visitor = await db_session.get(Visitor, vid)
    if visitor is None:
        db_session.add(Visitor(id=vid))
    db_session.add(
        Dialogue(visitor_id=vid, content=content, role=DialogueRole.user)
    )
    await db_session.commit()
    result = await db_session.execute(
        select(Dialogue).where(Dialogue.visitor_id == vid)
    )
    return result.scalars().all()[-1].id


async def _seed_evaluation(db_session: AsyncSession, dialogue_id: int) -> None:
    db_session.add(Evaluation(dialogue_id=dialogue_id, sentiment_label="positive"))
    await db_session.commit()


async def _seed_memory(user_id: int, content: str = "喜欢拍照") -> None:
    result = await memory_service.save_user_memory(
        str(user_id), [MemoryEntry(fact_type="preference", content=content)]
    )
    assert result.get("written") == 1


async def _audit_rows(db_session: AsyncSession) -> list:
    from app.models.admin_audit import AdminAuditLog

    result = await db_session.execute(
        select(AdminAuditLog).order_by(AdminAuditLog.id)
    )
    return list(result.scalars().all())


# ── 鉴权 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_endpoints_require_admin_token(client: AsyncClient):
    """全部账号管理端点：无 token / 错误 token → 401。"""
    no_token_calls = [
        ("GET", API),
        ("GET", f"{API}/1"),
        ("GET", f"{API}/audit-logs"),
        ("POST", API),
        ("PUT", f"{API}/1/disabled"),
        ("POST", f"{API}/1/reset-password"),
        ("DELETE", f"{API}/1"),
    ]
    for method, url in no_token_calls:
        resp = await client.request(method, url, json={} if method != "GET" else None)
        assert resp.status_code == 401, f"{method} {url} 无 token 应 401"
    for method, url in no_token_calls:
        resp = await client.request(
            method,
            url,
            json={} if method != "GET" else None,
            headers=_auth("wrong-token"),
        )
        assert resp.status_code == 401, f"{method} {url} 错误 token 应 401"


# ── 列表 / 详情 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_users_pagination_and_search(client: AsyncClient, db_session: AsyncSession):
    u1 = await _seed_user(db_session, "alice", "小爱")
    u2 = await _seed_user(db_session, "bob", "小波")
    await _seed_user(db_session, "carol")

    resp = await client.get(API, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert {i["username"] for i in data["items"]} == {"alice", "bob", "carol"}

    # keyword 匹配用户名/昵称
    resp = await client.get(f"{API}?keyword=小爱", headers=_auth())
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["username"] == "alice"

    # 分页
    resp = await client.get(f"{API}?page=2&page_size=2", headers=_auth())
    assert resp.json()["total"] == 3
    assert len(resp.json()["items"]) == 1


@pytest.mark.asyncio
async def test_list_and_detail_include_counts(client: AsyncClient, db_session: AsyncSession):
    uid = await _seed_user(db_session, "counter")
    await _seed_dialogue(db_session, uid, "你好")
    await _seed_dialogue(db_session, uid, "灵山有什么好玩的")
    await _seed_memory(uid, "喜欢拍照")
    await _seed_memory(uid, "带老人小孩")

    resp = await client.get(API, headers=_auth())
    item = next(i for i in resp.json()["items"] if i["id"] == uid)
    assert item["dialogue_count"] == 2
    assert item["memory_count"] == 2
    assert item["is_disabled"] is False

    resp = await client.get(f"{API}/{uid}", headers=_auth())
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["dialogue_count"] == 2
    assert detail["memory_count"] == 2


@pytest.mark.asyncio
async def test_get_user_detail_not_found(client: AsyncClient):
    resp = await client.get(f"{API}/9999", headers=_auth())
    assert resp.status_code == 404


# ── 创建 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_user_success_and_audit(client: AsyncClient, db_session: AsyncSession):
    resp = await client.post(
        API,
        json={"username": "newbie", "password": "secret123", "nickname": "新人"},
        headers=_auth(),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["username"] == "newbie"
    assert data["nickname"] == "新人"
    assert data["is_disabled"] is False
    assert "password" not in data

    # 审计落库（操作人 = X-Admin-Token 固定标签，不落凭证明文）
    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    assert rows[0].operator == "x-admin-token"
    assert rows[0].action == "create"
    assert rows[0].target_username == "newbie"

    # 新账号可登录（登录接口，非对话）
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "newbie", "password": "secret123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_create_user_duplicate_and_weak_password(client: AsyncClient, db_session: AsyncSession):
    await _seed_user(db_session, "dup")
    resp = await client.post(
        API, json={"username": "dup", "password": "secret123"}, headers=_auth()
    )
    assert resp.status_code == 409
    assert "已存在" in resp.json()["detail"]

    resp = await client.post(
        API, json={"username": "weakpw", "password": "12345"}, headers=_auth()
    )
    assert resp.status_code == 422

    resp = await client.post(
        API, json={"username": "bad name!", "password": "secret123"}, headers=_auth()
    )
    assert resp.status_code == 422


# ── 禁用 / 启用（登录/对话受控） ────────────────────────────────────────


@pytest.mark.asyncio
async def test_disable_user_blocks_login_me_and_dialogue(
    client: AsyncClient, db_session: AsyncSession
):
    uid = await _seed_user(db_session, "tobe_disabled", "将被禁用")

    # 禁用前可登录
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "tobe_disabled", "password": "secret123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]

    resp = await client.put(
        f"{API}/{uid}/disabled", json={"disabled": True}, headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["is_disabled"] is True
    assert resp.json()["disabled_at"] is not None

    # 禁用后登录 403
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "tobe_disabled", "password": "secret123"},
    )
    assert login.status_code == 403
    assert "禁用" in login.json()["detail"]

    # 禁用后 /auth/me 403
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 403

    # 禁用后对话 403（软禁用：对话受控；门禁在 LLM 调用前短路）
    chat = await client.post(
        "/api/v1/dialogue/chat",
        json={"query": "你好"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chat.status_code == 403

    # 审计：disable 落库
    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["disable"]
    assert rows[0].target_username == "tobe_disabled"


@pytest.mark.asyncio
async def test_enable_user_restores_login(client: AsyncClient, db_session: AsyncSession):
    uid = await _seed_user(db_session, "restore", disabled=True)
    # 禁用态登录 403
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "restore", "password": "secret123"},
    )
    assert login.status_code == 403

    resp = await client.put(
        f"{API}/{uid}/disabled", json={"disabled": False}, headers=_auth()
    )
    assert resp.status_code == 200
    assert resp.json()["is_disabled"] is False
    assert resp.json()["disabled_at"] is None

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "restore", "password": "secret123"},
    )
    assert login.status_code == 200

    rows = await _audit_rows(db_session)
    assert rows[-1].action == "enable"


@pytest.mark.asyncio
async def test_disable_super_admin_protected(client: AsyncClient, db_session: AsyncSession):
    """超管保护：ADMIN_USERNAME 同名账号不可禁用（400）。"""
    uid = await _seed_user(db_session, settings.ADMIN_USERNAME)
    resp = await client.put(
        f"{API}/{uid}/disabled", json={"disabled": True}, headers=_auth()
    )
    assert resp.status_code == 400
    assert "受保护" in resp.json()["detail"]
    # 未执行禁用、未写审计（保护拒绝不落审计）
    user = await db_session.get(User, uid)
    assert user.is_disabled is False


@pytest.mark.asyncio
async def test_disable_user_not_found_and_idempotent(
    client: AsyncClient, db_session: AsyncSession
):
    resp = await client.put(
        f"{API}/9999/disabled", json={"disabled": True}, headers=_auth()
    )
    assert resp.status_code == 404

    uid = await _seed_user(db_session, "idem")
    resp = await client.put(
        f"{API}/{uid}/disabled", json={"disabled": True}, headers=_auth()
    )
    assert resp.status_code == 200
    # 重复禁用幂等（不报错、不重复写审计）
    resp = await client.put(
        f"{API}/{uid}/disabled", json={"disabled": True}, headers=_auth()
    )
    assert resp.status_code == 200
    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["disable"]


# ── 重置密码 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_password_explicit(client: AsyncClient, db_session: AsyncSession):
    uid = await _seed_user(db_session, "resetme")
    resp = await client.post(
        f"{API}/{uid}/reset-password",
        json={"new_password": "brandnew123"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.json()["generated"] is False
    assert resp.json()["new_password"] == "brandnew123"

    # 旧密码失效、新密码可登录
    old = await client.post(
        "/api/v1/auth/login",
        json={"username": "resetme", "password": "secret123"},
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login",
        json={"username": "resetme", "password": "brandnew123"},
    )
    assert new.status_code == 200

    rows = await _audit_rows(db_session)
    assert rows[-1].action == "reset_password"


@pytest.mark.asyncio
async def test_reset_password_generated_temp(client: AsyncClient, db_session: AsyncSession):
    uid = await _seed_user(db_session, "temp_pw")
    resp = await client.post(
        f"{API}/{uid}/reset-password", json={}, headers=_auth()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated"] is True
    assert len(data["new_password"]) >= 8

    # 临时密码可登录；审计 detail 标记 generated
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "temp_pw", "password": data["new_password"]},
    )
    assert login.status_code == 200
    rows = await _audit_rows(db_session)
    assert rows[-1].action == "reset_password"
    assert "generated" in rows[-1].detail


@pytest.mark.asyncio
async def test_reset_password_empty_string_generates_temp(
    client: AsyncClient, db_session: AsyncSession
):
    """显式传空串 new_password 与缺省等价：生成临时密码而非 422。"""
    uid = await _seed_user(db_session, "empty_pw")
    resp = await client.post(
        f"{API}/{uid}/reset-password",
        json={"new_password": ""},
        headers=_auth(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["generated"] is True
    assert len(data["new_password"]) >= 8


@pytest.mark.asyncio
async def test_reset_password_not_found(client: AsyncClient):
    resp = await client.post(
        f"{API}/9999/reset-password", json={}, headers=_auth()
    )
    assert resp.status_code == 404


# ── 删除（单目标 + 级联清理） ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_user_cascades_all_data(
    client: AsyncClient, db_session: AsyncSession
):
    uid = await _seed_user(db_session, "doomed")
    d1 = await _seed_dialogue(db_session, uid, "你好")
    await _seed_dialogue(db_session, uid, "再见")
    await _seed_evaluation(db_session, d1)
    await _seed_memory(uid, "喜欢拍照")
    await _seed_memory(uid, "带老人")

    resp = await client.delete(f"{API}/{uid}", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["username"] == "doomed"
    assert data["deleted_dialogues"] == 2
    assert data["deleted_evaluations"] == 1
    assert data["deleted_memories"] == 2

    # 用户/对话/评估/visitor/记忆全部清理
    assert await db_session.get(User, uid) is None
    assert (
        await db_session.execute(
            select(func.count()).select_from(Dialogue).where(Dialogue.visitor_id == str(uid))
        )
    ).scalar_one() == 0
    assert (
        await db_session.execute(select(func.count()).select_from(Evaluation))
    ).scalar_one() == 0
    assert await db_session.get(Visitor, str(uid)) is None
    assert await memory_service.count_user_memory(str(uid)) == 0

    # 删除后旧 token 对话被门禁阻断（用户不存在 = 已注销）
    token, _ = create_access_token(subject=str(uid), token_type="user")
    chat = await client.post(
        "/api/v1/dialogue/chat",
        json={"query": "你好"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert chat.status_code == 403

    # 审计可查（验收：删除操作在审计表中可查）
    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["delete"]
    assert rows[0].target_username == "doomed"
    assert "dialogues=2" in rows[0].detail


@pytest.mark.asyncio
async def test_delete_super_admin_protected_and_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    uid = await _seed_user(db_session, settings.ADMIN_USERNAME)
    resp = await client.delete(f"{API}/{uid}", headers=_auth())
    assert resp.status_code == 400
    assert "受保护" in resp.json()["detail"]
    assert await db_session.get(User, uid) is not None

    resp = await client.delete(f"{API}/9999", headers=_auth())
    assert resp.status_code == 404


# ── 审计日志 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_logs_list_filter_and_pagination(
    client: AsyncClient, db_session: AsyncSession
):
    uid = await _seed_user(db_session, "auditme")
    await client.put(f"{API}/{uid}/disabled", json={"disabled": True}, headers=_auth())
    await client.put(f"{API}/{uid}/disabled", json={"disabled": False}, headers=_auth())
    await client.post(f"{API}/{uid}/reset-password", json={}, headers=_auth())
    await client.delete(f"{API}/{uid}", headers=_auth())

    resp = await client.get(f"{API}/audit-logs", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    # 时间倒序：delete 最新
    assert [i["action"] for i in data["items"]] == [
        "delete",
        "reset_password",
        "enable",
        "disable",
    ]
    assert data["items"][0]["target_username"] == "auditme"

    # 动作筛选
    resp = await client.get(f"{API}/audit-logs?action=disable", headers=_auth())
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["action"] == "disable"

    # 关键词搜索（目标用户名）
    resp = await client.get(f"{API}/audit-logs?keyword=auditme", headers=_auth())
    assert resp.json()["total"] == 4

    # 分页
    resp = await client.get(f"{API}/audit-logs?page=1&page_size=2", headers=_auth())
    assert resp.json()["total"] == 4
    assert len(resp.json()["items"]) == 2


# ── RBAC：operator 只读，敏感操作要求 admin ─────────────────────────────


@pytest.fixture
def _set_operator_token(monkeypatch):
    monkeypatch.setattr(
        settings, "ADMIN_TOKENS", {OPERATOR_TOKEN: "operator"}
    )
    return OPERATOR_TOKEN


@pytest.mark.asyncio
async def test_rbac_operator_read_only(
    client: AsyncClient, db_session: AsyncSession, _set_operator_token: str
):
    uid = await _seed_user(db_session, "op_target")
    op_headers = _auth(OPERATOR_TOKEN)

    # 只读放行
    assert (await client.get(API, headers=op_headers)).status_code == 200
    assert (await client.get(f"{API}/{uid}", headers=op_headers)).status_code == 200
    assert (await client.get(f"{API}/audit-logs", headers=op_headers)).status_code == 200

    # 创建（常规写）放行
    resp = await client.post(
        API, json={"username": "op_created", "password": "secret123"}, headers=op_headers
    )
    assert resp.status_code == 201

    # 敏感操作 403
    assert (
        await client.put(f"{API}/{uid}/disabled", json={"disabled": True}, headers=op_headers)
    ).status_code == 403
    assert (
        await client.post(f"{API}/{uid}/reset-password", json={}, headers=op_headers)
    ).status_code == 403
    assert (await client.delete(f"{API}/{uid}", headers=op_headers)).status_code == 403

    # 被 403 拒绝的敏感操作未执行、未写审计；仅创建写了一条审计（operator=x-admin-token 标签）
    user = await db_session.get(User, uid)
    assert user is not None and user.is_disabled is False
    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["create"]
    assert rows[0].operator == "x-admin-token"
    assert rows[0].target_username == "op_created"
