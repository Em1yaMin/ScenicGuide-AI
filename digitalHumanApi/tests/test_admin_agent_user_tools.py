"""管理员 agent 账号管理工具（2026-08-13 船长需求）单测。

覆盖：
1. admin 上下文校验：全部账号工具 is_admin=False 时拒绝（PermissionError）
2. 列表/详情：只读查询（含对话/记忆条数概览）、不存在错误
3. 创建：成功（含审计落库 operator=admin-agent）、重名失败
4. 禁用：两阶段二次确认（pending → confirm 执行；确认前未生效）、
   已禁用/超管保护拒绝、审计落库
5. 启用：直接执行（恢复操作），审计落库
6. 重置密码：两阶段二次确认（生成临时密码 / 指定新密码）、审计落库
7. 删除：两阶段二次确认（pending 含影响概览）、级联清理（对话/评估/记忆）、
   超管保护拒绝、目标已被删如实报告、审计落库
8. 二次确认机制：TTL 过期失效、重复确认失效（confirm.py 既有语义）
"""
import json
import logging

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import rag
from app.core.config import settings
from app.models.admin_audit import AdminAuditLog
from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.models.user import User
from app.models.visitor import Visitor
from app.services import auth_service, memory_service
from app.services.admin_agent import confirm, tools
from app.services.memory_service import MemoryEntry

ADMIN = {"is_admin": True}


@pytest.fixture(autouse=True)
def _isolate_rag_and_pending(tmp_path, monkeypatch):
    """每个测试独立 RAG 数据目录 + 清空向量库/待确认项缓存（防跨测试污染）。"""
    monkeypatch.setattr(rag, "RAG_DATA_DIR", tmp_path)
    with rag._vectorstore_lock:
        rag._vectorstores.clear()
    with confirm._lock:
        confirm._pending.clear()
    yield
    with rag._vectorstore_lock:
        rag._vectorstores.clear()
    with confirm._lock:
        confirm._pending.clear()


@pytest.fixture
async def tool_db(db_engine, monkeypatch):
    """工具私有会话工厂绑定测试内存库（monkeypatch tools.async_session_factory）。"""
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(tools, "async_session_factory", factory)
    return factory


async def _seed_user(db_session, username: str, *, disabled: bool = False) -> int:
    user = User(
        username=username,
        password_hash=auth_service.hash_password("secret123"),
        is_disabled=disabled,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user.id


async def _seed_dialogue(db_session, user_id: int) -> int:
    vid = str(user_id)
    if await db_session.get(Visitor, vid) is None:
        db_session.add(Visitor(id=vid))
    db_session.add(Dialogue(visitor_id=vid, content="你好", role=DialogueRole.user))
    await db_session.commit()
    result = await db_session.execute(
        select(Dialogue).where(Dialogue.visitor_id == vid)
    )
    return result.scalars().all()[-1].id


async def _seed_memory(user_id: int, content: str = "喜欢拍照") -> None:
    result = await memory_service.save_user_memory(
        str(user_id), [MemoryEntry(fact_type="preference", content=content)]
    )
    assert result.get("written") == 1


async def _audit_rows(db_session) -> list[AdminAuditLog]:
    result = await db_session.execute(
        select(AdminAuditLog).order_by(AdminAuditLog.id)
    )
    return list(result.scalars().all())


def _parse(data: str) -> dict:
    return json.loads(data)


# ── admin 上下文校验 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_tools_reject_non_admin_context():
    """全部账号工具：is_admin=False 时拒绝执行（抛 PermissionError）。"""
    cases = [
        (tools.list_users, {}),
        (tools.get_user_detail, {"user_id": 1}),
        (tools.create_user, {"username": "u", "password": "secret123"}),
        (tools.disable_user, {"user_id": 1}),
        (tools.enable_user, {"user_id": 1}),
        (tools.reset_user_password, {"user_id": 1}),
        (tools.delete_user, {"user_id": 1}),
    ]
    for tool_fn, kwargs in cases:
        with pytest.raises(PermissionError):
            await tool_fn.ainvoke({**kwargs, 'is_admin': False})


# ── 列表 / 详情（只读） ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_users_and_detail(tool_db, db_session):
    uid = await _seed_user(db_session, "reader", disabled=True)
    await _seed_dialogue(db_session, uid)
    await _seed_memory(uid, "喜欢拍照")

    result = _parse(await tools.list_users.ainvoke(ADMIN))
    assert result["total"] >= 1
    item = next(i for i in result["items"] if i["username"] == "reader")
    assert item["is_disabled"] is True
    assert item["dialogue_count"] == 1
    assert item["memory_count"] == 1

    result = _parse(await tools.list_users.ainvoke({"keyword": "reader", **ADMIN}))
    assert result["total"] == 1

    result = _parse(await tools.get_user_detail.ainvoke({"user_id": uid, **ADMIN}))
    assert result["status"] == "ok"
    assert result["user"]["username"] == "reader"

    result = _parse(await tools.get_user_detail.ainvoke({"user_id": 99999, **ADMIN}))
    assert result["status"] == "error"


# ── 创建 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_user_success_with_audit(tool_db, db_session):
    result = _parse(
        await tools.create_user.ainvoke({"username": "newbie", "password": "secret123", "nickname": "新人", **ADMIN})
    )
    assert result["status"] == "ok"
    assert result["user"]["username"] == "newbie"
    assert result["user"]["nickname"] == "新人"

    rows = await _audit_rows(db_session)
    assert len(rows) == 1
    assert rows[0].operator == "admin-agent"
    assert rows[0].action == "create"
    assert rows[0].target_username == "newbie"


@pytest.mark.asyncio
async def test_create_user_duplicate_and_weak_password(tool_db, db_session):
    await _seed_user(db_session, "dup")
    result = _parse(
        await tools.create_user.ainvoke({"username": "dup", "password": "secret123", **ADMIN})
    )
    assert result["status"] == "error"

    result = _parse(
        await tools.create_user.ainvoke({"username": "shortpw", "password": "12345", **ADMIN})
    )
    assert result["status"] == "error"


# ── 禁用（两阶段二次确认） ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disable_user_two_phase_confirm(tool_db, db_session):
    uid = await _seed_user(db_session, "doomed_disable")

    # 首次调用：只登记待确认，未实际禁用
    first = _parse(await tools.disable_user.ainvoke({"user_id": uid, **ADMIN}))
    assert first["status"] == "pending_confirmation"
    assert first["tool"] == "disable_user"
    assert "doomed_disable" in first["summary"]
    user = await db_session.get(User, uid, populate_existing=True)
    assert user.is_disabled is False
    assert await _audit_rows(db_session) == []

    # 确认后真正禁用 + 审计
    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": first["operation_id"], **ADMIN}))
    assert done["status"] == "ok"
    assert done["username"] == "doomed_disable"
    user = await db_session.get(User, uid, populate_existing=True)
    assert user.is_disabled is True
    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["disable"]
    assert rows[0].operator == "admin-agent"


@pytest.mark.asyncio
async def test_disable_user_confirm_not_executed_before(tool_db, db_session):
    """确认前禁用未生效：pending 登记后用户仍可用（登录校验不受影响）。"""
    uid = await _seed_user(db_session, "pending_disable")
    await tools.disable_user.ainvoke({"user_id": uid, **ADMIN})
    user = await db_session.get(User, uid, populate_existing=True)
    assert user.is_disabled is False


@pytest.mark.asyncio
async def test_disable_user_protected_and_already_disabled(tool_db, db_session):
    uid = await _seed_user(db_session, settings.ADMIN_USERNAME)
    result = _parse(await tools.disable_user.ainvoke({"user_id": uid, **ADMIN}))
    assert result["status"] == "error"
    assert "受保护" in result["message"]

    uid2 = await _seed_user(db_session, "already_disabled", disabled=True)
    result = _parse(await tools.disable_user.ainvoke({"user_id": uid2, **ADMIN}))
    assert result["status"] == "error"

    result = _parse(await tools.disable_user.ainvoke({"user_id": 99999, **ADMIN}))
    assert result["status"] == "error"


# ── 启用（直接执行） ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enable_user_direct_with_audit(tool_db, db_session):
    uid = await _seed_user(db_session, "restore_me", disabled=True)
    result = _parse(await tools.enable_user.ainvoke({"user_id": uid, **ADMIN}))
    assert result["status"] == "ok"
    assert result["enabled"] == uid
    user = await db_session.get(User, uid, populate_existing=True)
    assert user.is_disabled is False

    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["enable"]

    # 未禁用账号启用：幂等拒绝
    result = _parse(await tools.enable_user.ainvoke({"user_id": uid, **ADMIN}))
    assert result["status"] == "error"


# ── 重置密码（两阶段二次确认） ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_password_two_phase_generated(tool_db, db_session):
    uid = await _seed_user(db_session, "reset_target")

    first = _parse(await tools.reset_user_password.ainvoke({"user_id": uid, **ADMIN}))
    assert first["status"] == "pending_confirmation"
    # 确认前密码未变
    user = await db_session.get(User, uid, populate_existing=True)
    assert auth_service.verify_password("secret123", user.password_hash)

    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": first["operation_id"], **ADMIN}))
    assert done["status"] == "ok"
    assert done["generated"] is True
    assert len(done["new_password"]) >= 8
    user = await db_session.get(User, uid, populate_existing=True)
    assert auth_service.verify_password(done["new_password"], user.password_hash)
    assert not auth_service.verify_password("secret123", user.password_hash)

    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["reset_password"]
    assert "generated" in rows[0].detail


@pytest.mark.asyncio
async def test_reset_password_explicit_new_password(tool_db, db_session):
    uid = await _seed_user(db_session, "explicit_pw")
    first = _parse(
        await tools.reset_user_password.ainvoke({"user_id": uid, "new_password": "brandnew123", **ADMIN})
    )
    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": first["operation_id"], **ADMIN}))
    assert done["status"] == "ok"
    assert done["generated"] is False
    assert done["new_password"] == "brandnew123"


# ── 删除（两阶段二次确认 + 级联清理） ───────────────────────────────────


@pytest.mark.asyncio
async def test_delete_user_two_phase_with_impact_and_cascade(tool_db, db_session):
    uid = await _seed_user(db_session, "doomed_agent")
    d1 = await _seed_dialogue(db_session, uid)
    await _seed_dialogue(db_session, uid)
    db_session.add(Evaluation(dialogue_id=d1, sentiment_label="positive"))
    await db_session.commit()
    await _seed_memory(uid, "喜欢拍照")

    # 首次调用：pending + 影响概览（对话数/记忆数），未删除
    first = _parse(await tools.delete_user.ainvoke({"user_id": uid, **ADMIN}))
    assert first["status"] == "pending_confirmation"
    assert first["impact"]["dialogues"] == 2
    assert first["impact"]["memories"] == 1
    assert "级联清理" in first["summary"]
    assert await db_session.get(User, uid) is not None
    assert await _audit_rows(db_session) == []

    # 确认后：级联清理 + 审计
    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": first["operation_id"], **ADMIN}))
    assert done["status"] == "ok"
    assert done["deleted_dialogues"] == 2
    assert done["deleted_evaluations"] == 1
    assert done["deleted_memories"] == 1
    assert await db_session.get(User, uid) is None
    assert (
        await db_session.execute(
            select(func.count()).select_from(Dialogue).where(Dialogue.visitor_id == str(uid))
        )
    ).scalar_one() == 0
    assert (
        await db_session.execute(select(func.count()).select_from(Evaluation))
    ).scalar_one() == 0
    assert await memory_service.count_user_memory(str(uid)) == 0

    rows = await _audit_rows(db_session)
    assert [r.action for r in rows] == ["delete"]
    assert rows[0].target_username == "doomed_agent"
    assert "dialogues=2" in rows[0].detail


@pytest.mark.asyncio
async def test_delete_user_protected_and_already_gone(tool_db, db_session):
    uid = await _seed_user(db_session, settings.ADMIN_USERNAME)
    result = _parse(await tools.delete_user.ainvoke({"user_id": uid, **ADMIN}))
    assert result["status"] == "error"
    assert "受保护" in result["message"]

    result = _parse(await tools.delete_user.ainvoke({"user_id": 99999, **ADMIN}))
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_delete_user_confirm_target_already_deleted(tool_db, db_session):
    """确认执行时目标已被其他操作删除：如实报告，不报错、不重复删。"""
    uid = await _seed_user(db_session, "race_gone")
    first = _parse(await tools.delete_user.ainvoke({"user_id": uid, **ADMIN}))
    # 确认前目标被他人删除（直接删库行）
    await db_session.execute(
        __import__("sqlalchemy").delete(User).where(User.id == uid)
    )
    await db_session.commit()
    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": first["operation_id"], **ADMIN}))
    assert done["status"] == "error"
    assert "已不存在" in done["message"]


# ── 二次确认机制（TTL / 重放） ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_expired_and_replay_rejected(tool_db, db_session):
    uid = await _seed_user(db_session, "ttl_user")
    first = _parse(await tools.disable_user.ainvoke({"user_id": uid, **ADMIN}))
    op_id = first["operation_id"]

    # 篡改 created_at 使其过期
    with confirm._lock:
        confirm._pending[op_id].created_at -= confirm._ttl_seconds() + 10
    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert done["status"] == "error"

    # 重新发起 + 重复确认（防重放）
    first2 = _parse(await tools.disable_user.ainvoke({"user_id": uid, **ADMIN}))
    done = _parse(await tools.confirm_operation.ainvoke({"operation_id": first2["operation_id"], **ADMIN}))
    assert done["status"] == "ok"
    replay = _parse(await tools.confirm_operation.ainvoke({"operation_id": first2["operation_id"], **ADMIN}))
    assert replay["status"] == "error"
