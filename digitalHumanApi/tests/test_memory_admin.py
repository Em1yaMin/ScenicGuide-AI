"""记忆管理 API（批次5 B5）测试：管理端用户列表 + 记忆查看/新增/修改/删除。

覆盖：
1. 鉴权：无 token / 错误 token → 401（全部端点）
2. 用户列表：已注册用户 + 记忆条数（memory_count）
3. 按用户查看记忆（含 memory_id/fact_type/content/timestamp）；游客返回空
4. 新增记忆：成功/内容校验 422/游客 400/未注册用户 404
5. 修改记忆：删除后重建（content/fact_type 部分更新）、404、422
6. 删除记忆：跨用户可用（管理员例外）、404
7. legacy 条目派生 id：列表暴露 `legacy:{归一化 content}` 且可按其删除
8. 审计日志：删除/修改/新增记录 operator=admin
9. 空白/带空白输入：纯空白 content 422 拒绝（修改先删后建在任何删除前校验失败，
   不丢数据）；user 首尾空白归一化（" 1 " → "1"，列表/计数可见非孤儿）
"""
import logging
import time

import pytest
from httpx import AsyncClient
from langchain_core.documents import Document

from app.core import rag
from app.core.config import settings
from app.models.user import User
from app.services import memory_service, rag_service
from app.services.memory_service import MemoryEntry

ADMIN_TOKEN = "test-admin-token"
API = "/api/admin/memories"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)


@pytest.fixture(autouse=True)
def _isolate_memory_store(tmp_path, monkeypatch):
    """每个测试使用独立的 user_memory 持久化目录（避免污染真实 rag_data 与跨测试串扰）。"""
    monkeypatch.setattr(rag, "RAG_DATA_DIR", tmp_path)
    # 清空向量库进程内单例缓存（按 collection+dir 重建，保证拿到 tmp 目录的存储）
    with rag._vectorstore_lock:
        rag._vectorstores.clear()
    yield
    with rag._vectorstore_lock:
        rag._vectorstores.clear()


async def _seed_user(db_session, username: str, nickname: str | None = None) -> int:
    """直接向测试库插入注册用户，返回 id。"""
    user = User(username=username, password_hash="x", nickname=nickname)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user.id


async def _seed_memory(user: str, content: str, fact_type: str = "fact") -> dict:
    """经 memory_service 写入一条记忆，返回其结果（entry 经 result 内部生成 uuid）。"""
    result = await memory_service.save_user_memory(
        user, [MemoryEntry(fact_type=fact_type, content=content)]
    )
    assert result.get("written") == 1
    return result


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {"X-Admin-Token": token}


# ── 鉴权 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_endpoints_require_admin_token(client: AsyncClient):
    """全部管理端端点：无 token / 错误 token → 401。"""
    no_token_calls = [
        ("GET", f"{API}/users"),
        ("GET", f"{API}?user=1"),
        ("POST", f"{API}"),
        ("PUT", f"{API}/abc"),
        ("DELETE", f"{API}/abc"),
    ]
    for method, url in no_token_calls:
        resp = await client.request(method, url, json={"user": "1", "content": "x"})
        assert resp.status_code == 401, f"{method} {url} 无 token 应 401"
    for method, url in no_token_calls:
        resp = await client.request(
            method, url, json={"user": "1", "content": "x"}, headers=_auth("wrong-token")
        )
        assert resp.status_code == 401, f"{method} {url} 错误 token 应 401"


# ── 用户列表 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_users_with_memory_counts(client: AsyncClient, db_session):
    """用户列表：含 id/username/nickname/记忆条数/最后登录时间；关键词过滤。"""
    uid1 = await _seed_user(db_session, "alice", nickname="小艾")
    uid2 = await _seed_user(db_session, "bob")
    await _seed_memory(str(uid1), "喜欢拍照", fact_type="preference")
    await _seed_memory(str(uid1), "带老人小孩")
    await _seed_memory(str(uid2), "偏好半日游")

    resp = await client.get(f"{API}/users", headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    by_username = {i["username"]: i for i in data["items"]}
    assert by_username["alice"]["memory_count"] == 2
    assert by_username["alice"]["nickname"] == "小艾"
    assert by_username["bob"]["memory_count"] == 1
    assert by_username["bob"]["last_login_at"] is None

    # 关键词过滤（用户名/昵称）
    resp = await client.get(f"{API}/users", params={"keyword": "bob"}, headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["username"] == "bob"
    resp = await client.get(f"{API}/users", params={"keyword": "小艾"}, headers=_auth())
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["username"] == "alice"


@pytest.mark.asyncio
async def test_list_users_pagination(client: AsyncClient, db_session):
    """用户列表分页。"""
    for i in range(3):
        await _seed_user(db_session, f"user{i}")
    resp = await client.get(
        f"{API}/users", params={"page": 2, "page_size": 2}, headers=_auth()
    )
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 1
    assert data["page"] == 2
    assert data["page_size"] == 2


# ── 按用户查看记忆 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_memories_by_user(client: AsyncClient, db_session):
    """按用户查看记忆：含 memory_id/fact_type/content/timestamp，legacy 派生 id 兼容。"""
    uid = await _seed_user(db_session, "alice")
    await _seed_memory(str(uid), "喜欢拍照", fact_type="preference")
    await _seed_memory(str(uid), "带老人小孩")

    resp = await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == str(uid)
    assert data["total"] == 2
    items = {i["content"]: i for i in data["items"]}
    photo = items["喜欢拍照"]
    assert photo["fact_type"] == "preference"
    assert photo["memory_id"]
    assert isinstance(photo["timestamp"], float) and photo["timestamp"] > 0
    assert "source_conversation_id" in photo


@pytest.mark.asyncio
async def test_list_memories_guest_empty(client: AsyncClient):
    """游客/匿名用户无长期记忆：查询返回空列表。"""
    for guest in ("visitor", "guest-abc123"):
        resp = await client.get(f"{API}", params={"user": guest}, headers=_auth())
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["items"] == []


@pytest.mark.asyncio
async def test_list_memories_legacy_derived_id(client: AsyncClient, db_session):
    """legacy 条目（无 memory_id）：列表暴露派生 id 且可按其删除。"""
    uid = await _seed_user(db_session, "alice")
    # 模拟升级前写入的旧条目：直接写入无 memory_id 的 Document
    store = rag.get_vectorstore_for_namespace(rag.NAMESPACE_USER_MEMORY)
    with rag_service._vector_index_lock:
        store.add_documents(
            [
                Document(
                    page_content="喜欢自然风光",
                    metadata={
                        "user": str(uid),
                        "fact_type": "preference",
                        "source_conversation_id": "legacy-cid",
                        "timestamp": time.time(),
                    },
                )
            ]
        )

    resp = await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["memory_id"] == f"legacy:{memory_service._normalize_content('喜欢自然风光')}"
    assert item["source_conversation_id"] == "legacy-cid"

    # 按派生 id 删除
    resp = await client.delete(f"{API}/{item['memory_id']}", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    assert await memory_service.count_user_memory(str(uid)) == 0


# ── 新增记忆 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_memory(client: AsyncClient, db_session):
    """新增记忆：成功返回 memory_id，落库可查。"""
    uid = await _seed_user(db_session, "alice")
    resp = await client.post(
        f"{API}",
        json={"user": str(uid), "fact_type": "habit", "content": "每周都来爬山"},
        headers=_auth(),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["memory_id"]
    assert data["user"] == str(uid)
    assert data["fact_type"] == "habit"
    assert data["content"] == "每周都来爬山"

    # 落库可查且可按返回 id 删除
    resp = await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())
    assert resp.json()["total"] == 1
    resp = await client.delete(f"{API}/{data['memory_id']}", headers=_auth())
    assert resp.json()["deleted"] == 1


@pytest.mark.asyncio
async def test_create_memory_content_validation(client: AsyncClient, db_session):
    """内容校验：空内容/超 200 字符/非法 fact_type → 422。"""
    uid = await _seed_user(db_session, "alice")
    resp = await client.post(
        f"{API}", json={"user": str(uid), "content": ""}, headers=_auth()
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"{API}", json={"user": str(uid), "content": "长" * 201}, headers=_auth()
    )
    assert resp.status_code == 422
    # 200 字符边界可写入
    resp = await client.post(
        f"{API}", json={"user": str(uid), "content": "长" * 200}, headers=_auth()
    )
    assert resp.status_code == 201
    resp = await client.post(
        f"{API}",
        json={"user": str(uid), "fact_type": "invalid", "content": "内容"},
        headers=_auth(),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_memory_guest_rejected(client: AsyncClient, db_session):
    """游客用户新增记忆 → 400。"""
    await _seed_user(db_session, "alice")
    for guest in ("visitor", "guest-abc123"):
        resp = await client.post(
            f"{API}", json={"user": guest, "content": "偏好"}, headers=_auth()
        )
        assert resp.status_code == 400, f"游客 {guest} 应 400"


@pytest.mark.asyncio
async def test_create_memory_unknown_user_404(client: AsyncClient, db_session):
    """目标用户未注册 → 404（含非数字 user 标识）。"""
    await _seed_user(db_session, "alice")
    for user in ("999", "not-a-number"):
        resp = await client.post(
            f"{API}", json={"user": user, "content": "偏好"}, headers=_auth()
        )
        assert resp.status_code == 404, f"user={user} 应 404"


@pytest.mark.asyncio
async def test_list_memories_user_whitespace_normalized(client: AsyncClient, db_session):
    """按用户查看：user 带首尾空白时归一化（与裸 id 同效果）。"""
    uid = await _seed_user(db_session, "alice")
    await _seed_memory(str(uid), "喜欢拍照")
    resp = await client.get(f"{API}", params={"user": f" {uid} "}, headers=_auth())
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"] == str(uid)  # 参数已归一化，响应回显归一化值
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_create_memory_blank_content_rejected(client: AsyncClient, db_session):
    """纯空白内容（" "）在 schema 层 422 拒绝（而非误导性 400）。"""
    uid = await _seed_user(db_session, "alice")
    resp = await client.post(
        f"{API}", json={"user": str(uid), "content": "   "}, headers=_auth()
    )
    assert resp.status_code == 422
    # 未产生任何条目
    assert await memory_service.count_user_memory(str(uid)) == 0


@pytest.mark.asyncio
async def test_create_memory_user_normalized(client: AsyncClient, db_session):
    """user 带首尾空白归一化后落库（" 1 " → "1"），列表/计数可见非孤儿条目。"""
    uid = await _seed_user(db_session, "alice")
    resp = await client.post(
        f"{API}", json={"user": f" {uid} ", "content": "喜欢拍照"}, headers=_auth()
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["user"] == str(uid)
    # 按裸 id 查询与用户列表计数均可见（未产生 user=" 1 " 孤儿条目）
    listed = (await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())).json()
    assert listed["total"] == 1
    users = (await client.get(f"{API}/users", headers=_auth())).json()
    assert users["items"][0]["memory_count"] == 1


# ── 修改记忆（删除后重建） ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_memory(client: AsyncClient, db_session):
    """修改记忆：content/fact_type 更新，保留 user 与来源会话，memory_id 更新。"""
    uid = await _seed_user(db_session, "alice")
    created = (
        await client.post(
            f"{API}",
            json={"user": str(uid), "fact_type": "preference", "content": "喜欢拍照"},
            headers=_auth(),
        )
    ).json()

    resp = await client.put(
        f"{API}/{created['memory_id']}",
        json={"content": "喜欢摄影", "fact_type": "habit"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "喜欢摄影"
    assert data["fact_type"] == "habit"
    assert data["user"] == str(uid)
    assert data["memory_id"] != created["memory_id"]  # 重建后 id 更新

    # 列表只剩新内容
    listed = (await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())).json()
    assert listed["total"] == 1
    assert listed["items"][0]["content"] == "喜欢摄影"
    # 旧 id 已失效
    resp = await client.delete(f"{API}/{created['memory_id']}", headers=_auth())
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_memory_partial(client: AsyncClient, db_session):
    """部分更新：只改 fact_type 或只改 content，另一字段保留。"""
    uid = await _seed_user(db_session, "alice")
    created = (
        await client.post(
            f"{API}",
            json={"user": str(uid), "fact_type": "preference", "content": "喜欢拍照"},
            headers=_auth(),
        )
    ).json()

    resp = await client.put(
        f"{API}/{created['memory_id']}", json={"fact_type": "habit"}, headers=_auth()
    )
    data = resp.json()
    assert data["fact_type"] == "habit"
    assert data["content"] == "喜欢拍照"

    resp = await client.put(
        f"{API}/{data['memory_id']}", json={"content": "喜欢拍风景"}, headers=_auth()
    )
    data = resp.json()
    assert data["fact_type"] == "habit"
    assert data["content"] == "喜欢拍风景"


@pytest.mark.asyncio
async def test_update_memory_validation_and_404(client: AsyncClient, db_session):
    """修改校验：空 body 422、超长 422、不存在 404。"""
    uid = await _seed_user(db_session, "alice")
    created = (
        await client.post(
            f"{API}", json={"user": str(uid), "content": "内容"}, headers=_auth()
        )
    ).json()

    resp = await client.put(f"{API}/{created['memory_id']}", json={}, headers=_auth())
    assert resp.status_code == 422
    resp = await client.put(
        f"{API}/{created['memory_id']}", json={"content": "长" * 201}, headers=_auth()
    )
    assert resp.status_code == 422
    resp = await client.put(
        f"{API}/no-such-id", json={"content": "新内容"}, headers=_auth()
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_memory_blank_content_no_data_loss(client: AsyncClient, db_session):
    """纯空白内容更新 → 422 且原记忆不被删除（先删后建在任何删除前校验失败）。"""
    uid = await _seed_user(db_session, "alice")
    created = (
        await client.post(
            f"{API}", json={"user": str(uid), "content": "喜欢拍照"}, headers=_auth()
        )
    ).json()

    resp = await client.put(
        f"{API}/{created['memory_id']}", json={"content": "   "}, headers=_auth()
    )
    assert resp.status_code == 422
    # 原记忆完好、旧 id 仍有效
    listed = (await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())).json()
    assert listed["total"] == 1
    assert listed["items"][0]["content"] == "喜欢拍照"
    resp = await client.delete(f"{API}/{created['memory_id']}", headers=_auth())
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_memory_content_stripped(client: AsyncClient, db_session):
    """新内容带首尾空白：保存前归一化（" 喜欢摄影 " → "喜欢摄影"）。"""
    uid = await _seed_user(db_session, "alice")
    created = (
        await client.post(
            f"{API}", json={"user": str(uid), "content": "喜欢拍照"}, headers=_auth()
        )
    ).json()

    resp = await client.put(
        f"{API}/{created['memory_id']}", json={"content": "  喜欢摄影  "}, headers=_auth()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == "喜欢摄影"
    assert data["memory_id"] != created["memory_id"]


# ── 删除记忆（跨用户管理员例外） ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_memory_cross_user(client: AsyncClient, db_session):
    """跨用户删除：管理员可按 memory_id 删除任意用户记忆（管理员例外）。"""
    uid1 = await _seed_user(db_session, "alice")
    uid2 = await _seed_user(db_session, "bob")
    await _seed_memory(str(uid1), "喜欢拍照")
    await _seed_memory(str(uid2), "带老人小孩")

    listed = (await client.get(f"{API}", params={"user": str(uid2)}, headers=_auth())).json()
    bob_memory_id = listed["items"][0]["memory_id"]

    # 删除 bob 的记忆：alice 的记忆不受影响
    resp = await client.delete(f"{API}/{bob_memory_id}", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["user"] == str(uid2)
    assert resp.json()["deleted"] == 1
    assert await memory_service.count_user_memory(str(uid2)) == 0
    assert await memory_service.count_user_memory(str(uid1)) == 1


@pytest.mark.asyncio
async def test_delete_memory_404(client: AsyncClient):
    """删除不存在的 memory_id → 404。"""
    resp = await client.delete(f"{API}/no-such-id", headers=_auth())
    assert resp.status_code == 404


# ── 审计日志 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_logs(client: AsyncClient, db_session, caplog):
    """删除/修改/新增记忆均记录审计日志（operator=admin + 目标 user/memory_id/内容）。"""
    uid = await _seed_user(db_session, "alice")

    with caplog.at_level(logging.INFO):
        created = (
            await client.post(
                f"{API}",
                json={"user": str(uid), "fact_type": "preference", "content": "喜欢拍照"},
                headers=_auth(),
            )
        ).json()
        await client.put(
            f"{API}/{created['memory_id']}", json={"content": "喜欢摄影"}, headers=_auth()
        )
        listed = (await client.get(f"{API}", params={"user": str(uid)}, headers=_auth())).json()
        await client.delete(f"{API}/{listed['items'][0]['memory_id']}", headers=_auth())

    records = [r for r in caplog.records if r.name == "app.routers.memory_admin"]
    messages = " | ".join(r.getMessage() for r in records)
    assert "管理员新增记忆: operator=admin" in messages
    assert "管理员修改记忆: operator=admin" in messages
    assert "管理员删除记忆: operator=admin" in messages
    assert str(uid) in messages
