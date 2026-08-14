"""管理员 agent 管理工具集（批次6 B4）单测。

覆盖：
1. admin 上下文校验：全部工具 is_admin=False 时拒绝执行（PermissionError）
2. 知识库：查询/新增/更新/删除（两阶段二次确认：首次待确认→确认执行）、
   目标不存在、审计日志
3. 记忆：按用户查询（管理员例外跨用户）、删除两阶段、审计日志
4. 公告：列表/发布下架（含审计）、目标不存在
5. 统计：对话量/用户量/评估分布（只读）
6. RAG 重建索引：documents + knowledge 两命名空间 + 审计
7. 首页数据：热门景点/推荐路线（只读）
8. 二次确认：TTL 过期失效、重复确认失效、确认执行时目标已被删
"""

import json
import logging

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import rag
from app.core.config import settings
from app.models.announcement import Announcement, STATUS_DRAFT, STATUS_PUBLISHED
from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.models.knowledge import KnowledgeBase
from app.models.user import User
from app.models.visitor import Visitor
from app.services import announcement_service, memory_service
from app.services.admin_agent import confirm, tools
from app.services.memory_service import MemoryEntry

# 默认以管理员上下文调用（is_admin=True 注入）；越权用例显式传 False
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
    """工具私有会话工厂绑定测试内存库（monkeypatch tools.async_session_factory）。

    让工具的 _db_session 与 db_session fixture 同一引擎：seed 数据
    （db_session）与工具读写（tool_db）共享同一内存库。
    """
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(tools, "async_session_factory", factory)
    return factory


async def _seed_knowledge(db_session, title="灵山简介", category="景点资料") -> int:
    kb = KnowledgeBase(
        title=title, category=category, content="灵山风景区位于…", tags=["山"]
    )
    db_session.add(kb)
    await db_session.commit()
    await db_session.refresh(kb)
    return kb.id


async def _seed_announcement(
    db_session, title="开园时间调整", content="本周六开园时间调整为 8:00"
) -> int:
    ann = Announcement(title=title, content=content, status=STATUS_DRAFT)
    db_session.add(ann)
    await db_session.commit()
    await db_session.refresh(ann)
    return ann.id


async def _seed_memory(user: str, content: str) -> str:
    result = await memory_service.save_user_memory(
        user, [MemoryEntry(fact_type="preference", content=content)]
    )
    assert result.get("written") == 1
    entries = await memory_service.list_user_memories(user)
    return memory_service.memory_id_of(entries[0])


def _parse(data: str) -> dict:
    return json.loads(data)


# ── admin 上下文校验（每个工具调用前） ───────────────────────────────────


@pytest.mark.asyncio
async def test_all_tools_reject_non_admin_context():
    """全部工具：is_admin=False 时拒绝执行（抛 PermissionError）。"""
    cases = [
        (tools.query_knowledge, {"keyword": "x"}),
        (tools.create_knowledge, {"title": "t", "category": "c", "content": "b"}),
        (tools.update_knowledge, {"kb_id": 1, "title": "t"}),
        (tools.delete_knowledge, {"kb_id": 1}),
        (tools.query_user_memories, {"user": "1"}),
        (tools.delete_user_memory, {"memory_id": "abc"}),
        (tools.list_announcements, {}),
        (tools.create_announcement, {"title": "t", "content": "b"}),
        (tools.update_announcement, {"announcement_id": 1, "title": "t"}),
        (tools.delete_announcement, {"announcement_id": 1}),
        (tools.set_announcement_status, {"announcement_id": 1, "status": "published"}),
        (tools.query_statistics, {}),
        (tools.rebuild_rag_index, {}),
        (tools.view_home_highlights, {}),
        (tools.list_character_models, {}),
        (tools.set_character_model, {"character": "Mao"}),
        (tools.confirm_operation, {"operation_id": "abc"}),
    ]
    for t, args in cases:
        with pytest.raises(PermissionError, match="管理员上下文缺失"):
            await t.ainvoke({**args, "is_admin": False})


# ── 知识库工具 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_knowledge_filters(db_session, tool_db):
    """query_knowledge：分页 + 关键词筛选，只读不改库。"""
    kid = await _seed_knowledge(db_session, title="灵山日出攻略", category="攻略")
    await _seed_knowledge(db_session, title="灵山美食", category="攻略")

    result = _parse(await tools.query_knowledge.ainvoke({**ADMIN}))
    assert result["total"] == 2
    assert len(result["items"]) == 2

    result = _parse(await tools.query_knowledge.ainvoke({"keyword": "美食", **ADMIN}))
    assert result["total"] == 1
    assert result["items"][0]["title"] == "灵山美食"

    result = _parse(await tools.query_knowledge.ainvoke({"category": "景点资料", **ADMIN}))
    assert result["total"] == 0

    # 条目仍在库中（只读）
    assert await db_session.get(KnowledgeBase, kid) is not None


@pytest.mark.asyncio
async def test_create_knowledge_and_audit(db_session, tool_db, caplog):
    """create_knowledge：新增条目 + 审计日志（operator=admin-agent）。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    result = _parse(
        await tools.create_knowledge.ainvoke(
            {"title": "灵山门票", "category": "FAQ", "content": "成人票 60 元", "tags": "门票,价格", **ADMIN}
        )
    )
    assert result["status"] == "ok"
    item = result["item"]
    assert item["title"] == "灵山门票"
    assert item["tags"] == ["门票", "价格"]
    # 落库
    assert await db_session.get(KnowledgeBase, item["id"]) is not None
    # 审计
    assert any(
        "operator=admin-agent tool=create_knowledge" in r.getMessage()
        and "action=create" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_create_knowledge_rejects_blank(db_session, tool_db):
    """create_knowledge：标题/正文为空拒绝。"""
    result = _parse(
        await tools.create_knowledge.ainvoke(
            {"title": "  ", "category": "FAQ", "content": "正文", **ADMIN}
        )
    )
    assert result["message"] == "标题与正文不能为空"


@pytest.mark.asyncio
async def test_update_knowledge_partial_and_audit(db_session, tool_db, caplog):
    """update_knowledge：部分更新（仅非空字段）+ 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    kid = await _seed_knowledge(db_session)
    result = _parse(await tools.update_knowledge.ainvoke({"kb_id": kid, "title": "新标题", **ADMIN}))
    assert result["status"] == "ok"
    assert result["item"]["title"] == "新标题"
    assert result["item"]["category"] == "景点资料"  # 未更新字段保持
    assert any(
        "operator=admin-agent tool=update_knowledge" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_update_knowledge_not_found(db_session, tool_db):
    """update_knowledge：目标不存在返回错误。"""
    result = _parse(await tools.update_knowledge.ainvoke({"kb_id": 999, "title": "x", **ADMIN}))
    assert result["status"] == "error"
    assert "不存在" in result["message"]


@pytest.mark.asyncio
async def test_delete_knowledge_two_phase_confirmation(db_session, tool_db, caplog):
    """delete_knowledge 两阶段：首次待确认不删除 → confirm 后真正删除 + 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    kid = await _seed_knowledge(db_session, title="待删条目")

    # 第一阶段：返回待确认（不执行删除）
    result = _parse(await tools.delete_knowledge.ainvoke({"kb_id": kid, **ADMIN}))
    assert result["status"] == "pending_confirmation"
    assert result["tool"] == "delete_knowledge"
    op_id = result["operation_id"]
    assert "待删条目" in result["summary"]
    assert await db_session.get(KnowledgeBase, kid) is not None  # 未删除

    # 确认执行
    confirmed = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert confirmed["status"] == "ok"
    assert confirmed["deleted"] == kid
    assert await db_session.get(KnowledgeBase, kid) is None  # 已删除
    assert any(
        "operator=admin-agent tool=delete_knowledge" in r.getMessage()
        and "action=delete" in r.getMessage()
        for r in caplog.records
    )

    # 重复确认失效（防重放）
    replay = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert replay["status"] == "error"
    assert "过期" in replay["message"]


@pytest.mark.asyncio
async def test_delete_knowledge_not_found(db_session, tool_db):
    """delete_knowledge：目标不存在直接报错（不进入待确认）。"""
    result = _parse(await tools.delete_knowledge.ainvoke({"kb_id": 999, **ADMIN}))
    assert result["status"] == "error"
    assert "不存在" in result["message"]


@pytest.mark.asyncio
async def test_confirm_after_ttl_expiry(db_session, tool_db, monkeypatch):
    """二次确认：待确认项 TTL 过期后失效（需重新发起删除）。"""
    monkeypatch.setattr(settings, "ADMIN_AGENT_CONFIRM_TTL_SECONDS", 0)
    kid = await _seed_knowledge(db_session)
    result = _parse(await tools.delete_knowledge.ainvoke({"kb_id": kid, **ADMIN}))
    op_id = result["operation_id"]
    # TTL=0 已过期：确认失败，数据未删
    failed = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert failed["status"] == "error"
    assert await db_session.get(KnowledgeBase, kid) is not None


@pytest.mark.asyncio
async def test_confirm_executor_target_already_deleted(db_session, tool_db):
    """确认执行时目标已被其他操作删除：如实报告（不抛错、不误删其他）。"""
    kid = await _seed_knowledge(db_session, title="将被他人删除")
    result = _parse(await tools.delete_knowledge.ainvoke({"kb_id": kid, **ADMIN}))
    op_id = result["operation_id"]
    # 他人（直接经 service）删除目标
    from app.services import knowledge_service

    async with tool_db() as db:
        assert await knowledge_service.delete_knowledge(db, kid)
        await db.commit()
    confirmed = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert confirmed["status"] == "error"
    assert "已不存在" in confirmed["message"]


# ── 记忆工具（管理员例外：跨用户） ────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_user_memories_admin_exception():
    """query_user_memories：管理员可查任意注册用户记忆（跨用户例外）。"""
    await _seed_memory("7", "喜欢自然风光")
    result = _parse(await tools.query_user_memories.ainvoke({"user": "7", **ADMIN}))
    assert result["total"] == 1
    assert result["items"][0]["content"] == "喜欢自然风光"
    assert result["items"][0]["memory_id"]


@pytest.mark.asyncio
async def test_query_user_memories_guest_empty():
    """query_user_memories：游客/匿名用户无记忆（返回空列表）。"""
    result = _parse(await tools.query_user_memories.ainvoke({"user": "visitor", **ADMIN}))
    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_delete_user_memory_two_phase_and_audit(caplog):
    """delete_user_memory 两阶段：首次待确认 → confirm 删除 + 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    memory_id = await _seed_memory("7", "喜欢拍照")

    result = _parse(await tools.delete_user_memory.ainvoke({"memory_id": memory_id, **ADMIN}))
    assert result["status"] == "pending_confirmation"
    assert result["tool"] == "delete_user_memory"
    op_id = result["operation_id"]
    assert "喜欢拍照" in result["summary"]
    # 未确认前记忆仍在
    assert await memory_service.list_user_memories("7")

    confirmed = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert confirmed["status"] == "ok"
    assert confirmed["deleted"] == 1
    assert await memory_service.list_user_memories("7") == []
    assert any(
        "operator=admin-agent tool=delete_user_memory" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_delete_user_memory_not_found():
    """delete_user_memory：目标不存在直接报错（不进入待确认）。"""
    result = _parse(await tools.delete_user_memory.ainvoke({"memory_id": "no-such-id", **ADMIN}))
    assert result["status"] == "error"
    assert "不存在" in result["message"]


# ── 公告工具 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_announcement_default_draft_and_audit(db_session, tool_db, caplog):
    """create_announcement：默认草稿入库 + 审计；草稿不在访客端/首页可见。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    result = _parse(
        await tools.create_announcement.ainvoke(
            {"title": "雨天路滑提醒", "content": "提醒游客雨天路滑，小心出行", **ADMIN}
        )
    )
    assert result["status"] == "ok"
    item = result["item"]
    assert item["status"] == "draft"
    assert item["pinned"] is False
    ann = await db_session.get(Announcement, item["id"])
    assert ann is not None and ann.title == "雨天路滑提醒"
    assert any(
        "operator=admin-agent tool=create_announcement" in r.getMessage()
        and "action=create" in r.getMessage()
        for r in caplog.records
    )
    # 草稿不在访客端公开列表（首页不可见，需发布）
    async with tool_db() as db:
        public = await announcement_service.list_published_announcements(db)
    assert public.total == 0


@pytest.mark.asyncio
async def test_create_announcement_published_visible_on_home(tool_db):
    """create_announcement：status=published 创建即发布，访客端/首页可见。"""
    result = _parse(
        await tools.create_announcement.ainvoke(
            {
                "title": "开园公告",
                "content": "今日正常开园",
                "status": "published",
                "pinned": True,
                **ADMIN,
            }
        )
    )
    assert result["status"] == "ok"
    assert result["item"]["status"] == "published"
    assert result["item"]["pinned"] is True
    async with tool_db() as db:
        public = await announcement_service.list_published_announcements(db)
    assert public.total == 1
    assert public.items[0].title == "开园公告"


@pytest.mark.asyncio
async def test_create_announcement_invalid_inputs(tool_db):
    """create_announcement：空白标题/正文、非法状态报错（不落库）。"""
    bad = _parse(
        await tools.create_announcement.ainvoke(
            {"title": "  ", "content": "内容", **ADMIN}
        )
    )
    assert "标题与正文不能为空" in bad["message"]
    bad = _parse(
        await tools.create_announcement.ainvoke(
            {"title": "标题", "content": "  ", **ADMIN}
        )
    )
    assert "标题与正文不能为空" in bad["message"]
    bad = _parse(
        await tools.create_announcement.ainvoke(
            {"title": "标题", "content": "内容", "status": "invalid", **ADMIN}
        )
    )
    assert "仅支持" in bad["message"]
    async with tool_db() as db:
        total = (
            await db.execute(select(func.count(Announcement.id)))
        ).scalar_one()
    assert total == 0


@pytest.mark.asyncio
async def test_update_announcement_partial_update_and_audit(db_session, tool_db, caplog):
    """update_announcement：部分更新（仅更新传入字段）+ 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    aid = await _seed_announcement(db_session, title="旧标题", content="旧内容")
    result = _parse(
        await tools.update_announcement.ainvoke(
            {"announcement_id": aid, "title": "新标题", "pinned": True, **ADMIN}
        )
    )
    assert result["status"] == "ok"
    assert result["item"]["title"] == "新标题"
    assert result["item"]["pinned"] is True
    ann = await db_session.get(Announcement, aid)
    assert ann.title == "新标题"
    assert ann.content == "旧内容"  # 未传字段保持不变
    assert ann.status == STATUS_DRAFT
    assert any(
        "operator=admin-agent tool=update_announcement" in r.getMessage()
        and "action=update" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_update_announcement_invalid_and_not_found(db_session, tool_db):
    """update_announcement：无更新字段/非法状态/目标不存在报错。"""
    aid = await _seed_announcement(db_session)
    empty = _parse(
        await tools.update_announcement.ainvoke({"announcement_id": aid, **ADMIN})
    )
    assert empty["status"] == "error"
    assert "没有可更新的字段" in empty["message"]
    bad_status = _parse(
        await tools.update_announcement.ainvoke(
            {"announcement_id": aid, "status": "invalid", **ADMIN}
        )
    )
    assert "仅支持" in bad_status["message"]
    not_found = _parse(
        await tools.update_announcement.ainvoke(
            {"announcement_id": 999, "title": "x", **ADMIN}
        )
    )
    assert not_found["status"] == "error"
    assert "不存在" in not_found["message"]


@pytest.mark.asyncio
async def test_delete_announcement_two_phase_confirmation_and_audit(
    db_session, tool_db, caplog
):
    """delete_announcement 两阶段：首次待确认不删除 → confirm 后真正删除 + 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    aid = await _seed_announcement(db_session, title="待删公告")

    # 第一阶段：返回待确认（不执行删除）
    result = _parse(await tools.delete_announcement.ainvoke({"announcement_id": aid, **ADMIN}))
    assert result["status"] == "pending_confirmation"
    assert result["tool"] == "delete_announcement"
    op_id = result["operation_id"]
    assert "待删公告" in result["summary"]
    assert await db_session.get(Announcement, aid) is not None  # 未删除

    # 确认执行
    confirmed = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert confirmed["status"] == "ok"
    assert confirmed["deleted"] == aid
    assert await db_session.get(Announcement, aid) is None  # 已删除
    assert any(
        "operator=admin-agent tool=delete_announcement" in r.getMessage()
        and "action=delete" in r.getMessage()
        for r in caplog.records
    )

    # 重复确认失效（防重放）
    replay = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert replay["status"] == "error"
    assert "过期" in replay["message"]


@pytest.mark.asyncio
async def test_delete_announcement_not_found(db_session, tool_db):
    """delete_announcement：目标不存在直接报错（不进入待确认）。"""
    result = _parse(await tools.delete_announcement.ainvoke({"announcement_id": 999, **ADMIN}))
    assert result["status"] == "error"
    assert "不存在" in result["message"]


@pytest.mark.asyncio
async def test_delete_announcement_confirm_target_already_deleted(db_session, tool_db):
    """确认执行时公告已被其他操作删除：如实报告（不抛错、不误删其他）。"""
    aid = await _seed_announcement(db_session, title="将被他人删除")
    result = _parse(await tools.delete_announcement.ainvoke({"announcement_id": aid, **ADMIN}))
    op_id = result["operation_id"]
    # 他人（直接经 service）删除目标
    async with tool_db() as db:
        assert await announcement_service.delete_announcement(db, aid)
        await db.commit()
    confirmed = _parse(await tools.confirm_operation.ainvoke({"operation_id": op_id, **ADMIN}))
    assert confirmed["status"] == "error"
    assert "已不存在" in confirmed["message"]


@pytest.mark.asyncio
async def test_list_announcements_status_filter(db_session, tool_db):
    """list_announcements：状态/关键词筛选（只读）。"""
    await _seed_announcement(db_session)  # draft
    aid2 = await _seed_announcement(
        db_session, title="活动预告", content="下月将举办灯光节"
    )
    ann2 = await db_session.get(Announcement, aid2)
    ann2.status = STATUS_PUBLISHED
    await db_session.commit()

    result = _parse(await tools.list_announcements.ainvoke({**ADMIN}))
    assert result["total"] == 2

    result = _parse(await tools.list_announcements.ainvoke({"status": "published", **ADMIN}))
    assert result["total"] == 1
    assert result["items"][0]["title"] == "活动预告"

    result = _parse(await tools.list_announcements.ainvoke({"keyword": "开园", **ADMIN}))
    assert result["total"] == 1


@pytest.mark.asyncio
async def test_set_announcement_status_publish_unpublish_and_audit(db_session, tool_db, caplog):
    """set_announcement_status：发布/下架切换 + 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    aid = await _seed_announcement(db_session)

    published = _parse(
        await tools.set_announcement_status.ainvoke(
            {"announcement_id": aid, "status": "published", **ADMIN}
        )
    )
    assert published["status"] == "ok"
    assert published["new_status"] == "published"
    ann = await db_session.get(Announcement, aid)
    assert ann.status == STATUS_PUBLISHED
    assert any(
        "operator=admin-agent tool=set_announcement_status" in r.getMessage()
        and "action=publish" in r.getMessage()
        for r in caplog.records
    )

    unpublish = _parse(
        await tools.set_announcement_status.ainvoke(
            {"announcement_id": aid, "status": "draft", **ADMIN}
        )
    )
    assert unpublish["status"] == "ok"
    assert unpublish["new_status"] == "draft"


@pytest.mark.asyncio
async def test_set_announcement_status_invalid_and_not_found(db_session, tool_db):
    """set_announcement_status：非法状态/目标不存在报错。"""
    bad = _parse(
        await tools.set_announcement_status.ainvoke(
            {"announcement_id": 1, "status": "invalid", **ADMIN}
        )
    )
    assert "仅支持" in bad["message"]

    not_found = _parse(
        await tools.set_announcement_status.ainvoke(
            {"announcement_id": 999, "status": "published", **ADMIN}
        )
    )
    assert not_found["status"] == "error"


# ── 统计工具（只读） ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_statistics_counts(db_session, tool_db):
    """query_statistics：对话量/用户量/评估分布聚合。"""
    # 用户
    db_session.add(User(username="alice", password_hash="x"))
    await db_session.commit()
    # 对话 + 评估（两轮对话，一条评估）
    visitor_id = "v-agent-stats"
    db_session.add(Visitor(id=visitor_id))
    d1 = Dialogue(visitor_id=visitor_id, role=DialogueRole.user, content="问1")
    d2 = Dialogue(visitor_id=visitor_id, role=DialogueRole.assistant, content="答1")
    db_session.add_all([d1, d2])
    await db_session.commit()
    db_session.add(
        Evaluation(
            dialogue_id=d1.id,
            sentiment_label="positive",
            satisfaction_score=5,
            concern_tags=["门票"],
            mentioned_spots=["灵山"],
        )
    )
    await db_session.commit()

    result = _parse(await tools.query_statistics.ainvoke({**ADMIN}))
    assert result["total_dialogues"] == 2
    assert result["total_users"] == 1
    assert result["total_evaluations"] == 1
    assert result["sentiment_distribution"] == {"positive": 1, "negative": 0, "neutral": 0}
    assert result["satisfaction_distribution"] == {"5": 1}
    assert result["hot_issues"] == [{"concern": "门票", "count": 1}]
    assert result["spot_heatmap"] == [{"spot_name": "灵山", "mention_count": 1}]


# ── RAG 重建索引工具 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rebuild_rag_index_both_namespaces_and_audit(
    db_session, tool_db, caplog, monkeypatch
):
    """rebuild_rag_index：documents + knowledge 两命名空间重建 + 审计。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    await _seed_knowledge(db_session, title="索引条目")

    calls = {}

    async def fake_index_documents(**kwargs):
        calls["documents"] = kwargs
        return {
            "status": "ok", "documents": 2, "chunks": 10,
            "embedding_backend": "tfidf",
        }

    async def fake_index_knowledge_entries(entries, namespace=None):
        calls["knowledge"] = {"entries": entries, "namespace": namespace}
        return {
            "status": "ok", "documents": len(entries),
            "embedding_backend": "tfidf",
        }

    monkeypatch.setattr(tools.rag_service, "index_documents", fake_index_documents)
    monkeypatch.setattr(tools.rag_service, "index_knowledge_entries", fake_index_knowledge_entries)

    result = _parse(await tools.rebuild_rag_index.ainvoke({**ADMIN}))
    assert result["status"] == "ok"
    assert result["documents"]["documents"] == 2
    assert result["knowledge"]["documents"] == 1
    assert calls["documents"]["namespace"] == "documents"
    assert calls["knowledge"]["entries"][0]["title"] == "索引条目"
    assert any(
        "operator=admin-agent tool=rebuild_rag_index" in r.getMessage()
        and "action=rebuild" in r.getMessage()
        for r in caplog.records
    )


# ── 首页数据工具（只读） ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_view_home_highlights_reads_static_data():
    """view_home_highlights：读取首页景点/路线（真实静态数据源，只读）。"""
    result = _parse(await tools.view_home_highlights.ainvoke({**ADMIN}))
    assert "spots" in result and "routes" in result
    # app/data/spots.json 与 routes.json 为仓库内数据源，应非空
    assert len(result["spots"]) > 0
    assert len(result["routes"]) > 0
    assert "name" in result["spots"][0]


# ── 二次确认工具边界 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_operation_unknown_id():
    """confirm_operation：未知 operation_id / 空 id 返回错误。"""
    result = _parse(await tools.confirm_operation.ainvoke({"operation_id": "", **ADMIN}))
    assert "operation_id" in result["message"]
    result = _parse(await tools.confirm_operation.ainvoke({"operation_id": "nope", **ADMIN}))
    assert result["status"] == "error"
    assert "过期" in result["message"]
