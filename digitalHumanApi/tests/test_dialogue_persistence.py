"""对话链路落库（dialogues 表）单测（2026-08-11 船长调试发现）。

背景：deepagents 装配层本地对话链路（默认引擎）历史上从不写 dialogues 表
（仅 Dify 轮询/回填写入），导致管理后台数据大屏（dialogues 计数/情感趋势/
热榜）与感受度报告（evaluations）永远为空。本测试断言：本地对话完成后
dialogues 表写入用户消息 + AI 回复两行（游客与登录用户各一），visitors
记录按需 upsert（外键约束），落库失败不阻断对话响应（演示不翻车），
主链路异常回落 Dify 时本地不落库（数据源在 Dify 侧，由轮询/回填负责）。
"""

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.security import create_access_token
from app.models.dialogue import Dialogue, DialogueRole
from app.models.visitor import Visitor
from app.services import chat_agent, deepagents


@pytest.fixture(autouse=True)
def _pin_dialogue_engine(monkeypatch):
    """固定默认引擎为 langchain（与生产默认一致）+ 清理会话存储。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "langchain")
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


async def _rows(db):
    """查询 dialogues 表全部行（按 id 升序）。"""
    result = await db.execute(select(Dialogue).order_by(Dialogue.id))
    return list(result.scalars().all())


def _dg_result(answer="装配层回答", conversation_id="cid-dg"):
    """构造装配层 chat() 的 dict 返回（响应契约字段）。"""
    return {
        "answer": answer,
        "conversation_id": conversation_id,
        "rag_context": "",
        "rag_sources": [],
        "intent": "simple",
    }


# ── 非流式落库 ───────────────────────────────────────────────────────────


async def test_guest_dialogue_persisted_to_db(client, db_session, monkeypatch):
    """游客（无 token，user 默认 visitor）对话后 dialogues 落两行 + visitor 记录。"""
    async def fake_chat(**kwargs):
        return _dg_result(answer="你好呀，欢迎来到景区！", conversation_id="cid-g")

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await client.post("/api/v1/dialogue/chat", json={"query": "你好"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "你好呀，欢迎来到景区！"

    rows = await _rows(db_session)
    assert len(rows) == 2
    assert rows[0].role == DialogueRole.user
    assert rows[0].content == "你好"
    assert rows[1].role == DialogueRole.assistant
    assert rows[1].content == "你好呀，欢迎来到景区！"
    assert all(r.visitor_id == "visitor" for r in rows)
    assert all(r.conversation_id == "cid-g" for r in rows)
    # external_message_id 生成且同轮两条互不相同（unique 约束可去重）
    assert rows[0].external_message_id and rows[1].external_message_id
    assert rows[0].external_message_id != rows[1].external_message_id
    # visitors 记录已确保存在（外键约束）
    v = await db_session.execute(select(Visitor).where(Visitor.id == "visitor"))
    assert v.scalar_one_or_none() is not None


async def test_login_user_dialogue_persisted(client, db_session, monkeypatch):
    """登录用户（Bearer JWT）对话后落库，visitor_id=用户 id。"""
    # 先落库用户 id=42（2026-08-13 对话门禁：注册用户 id 须在 users 表中存在）
    from app.models.user import User
    from app.services import auth_service

    db_session.add(
        User(id=42, username="persist_user", password_hash=auth_service.hash_password("x"))
    )
    await db_session.commit()

    token, _ = create_access_token(subject="42")
    async def fake_chat(**kwargs):
        assert kwargs["user"] == "42"
        return _dg_result(answer="您好，请问有什么可以帮您？", conversation_id="cid-u")

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await client.post(
        "/api/v1/dialogue/chat",
        json={"query": "介绍一下灵山景区"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = await _rows(db_session)
    assert len(rows) == 2
    assert all(r.visitor_id == "42" for r in rows)
    assert rows[0].content == "介绍一下灵山景区"
    assert rows[1].content == "您好，请问有什么可以帮您？"
    v = await db_session.execute(select(Visitor).where(Visitor.id == "42"))
    assert v.scalar_one_or_none() is not None


async def test_guest_token_dialogue_persisted(client, db_session, monkeypatch):
    """游客 token（guest-xxx）对话后落库，visitor_id=游客 id。"""
    token, _ = create_access_token(subject="guest-abc", token_type="guest")
    async def fake_chat(**kwargs):
        assert kwargs["user"] == "guest-abc"
        return _dg_result(answer="好的", conversation_id="cid-g2")

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await client.post(
        "/api/v1/dialogue/chat",
        json={"query": "你好"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    rows = await _rows(db_session)
    assert len(rows) == 2
    assert all(r.visitor_id == "guest-abc" for r in rows)
    v = await db_session.execute(select(Visitor).where(Visitor.id == "guest-abc"))
    assert v.scalar_one_or_none() is not None


async def test_multi_turn_persists_each_round(client, db_session, monkeypatch):
    """多轮对话（同 conversation_id）每轮各落两行，visitor 不重复 upsert。"""
    async def fake_chat(**kwargs):
        return _dg_result(answer=f"回答:{kwargs['query']}", conversation_id="cid-m")

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    for q in ("第一问", "第二问"):
        resp = await client.post(
            "/api/v1/dialogue/chat", json={"query": q, "conversation_id": "cid-m"}
        )
        assert resp.status_code == 200
    rows = await _rows(db_session)
    assert len(rows) == 4
    assert [r.content for r in rows] == [
        "第一问",
        "回答:第一问",
        "第二问",
        "回答:第二问",
    ]
    visitors = await db_session.execute(select(Visitor))
    assert len(list(visitors.scalars().all())) == 1  # 不重复 upsert


async def test_visitor_upsert_atomic_on_duplicate(db_session):
    """visitor upsert 原子化：行不存在时重复插入（并发首条消息场景）不抛错。

    旧实现 select 未命中后 db.add + flush，两个并发 /chat 请求（同一新
    visitor）同时命中该路径时，后提交的 flush 触发重复主键 IntegrityError，
    被 catch-all 吞掉并回滚——本轮 dialogues 两行一并静默丢失。现实现改用
    SQLite INSERT ... ON CONFLICT DO NOTHING，重复插入为 no-op，随后
    本轮对话仍正常落库。
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    # 模拟两个并发请求在行不存在时都执行原子插入：第二次为重复主键
    for _ in range(2):
        await db_session.execute(
            sqlite_insert(Visitor).values(id="visitor").on_conflict_do_nothing()
        )
    # 外键满足且不重复 upsert，随后对话仍正常落两行
    await chat_agent.record_dialogue(
        db_session,
        user="visitor",
        conversation_id="cid-race",
        query="你好",
        answer="回答",
    )
    rows = await _rows(db_session)
    assert len(rows) == 2
    assert all(r.visitor_id == "visitor" for r in rows)
    visitors = await db_session.execute(select(Visitor))
    assert len(list(visitors.scalars().all())) == 1


# ── 流式落库 ─────────────────────────────────────────────────────────────


async def test_stream_done_persists_dialogue(client, db_session, monkeypatch):
    """流式对话：done 事件处落库（用户消息 + 完整回答拼接）。"""
    async def fake_stream(**kwargs):
        yield {"type": "step", "tool": "search_knowledge", "label": "正在检索知识库…"}
        yield {"type": "token", "text": "流式"}
        yield {"type": "token", "text": "回答"}
        yield {
            "type": "done",
            "conversation_id": "cid-s",
            "rag_context": "",
            "rag_sources": [],
        }

    monkeypatch.setattr(deepagents, "chat_stream", fake_stream)
    resp = await client.post(
        "/api/v1/dialogue/chat", json={"query": "你好", "streaming": True}
    )
    assert resp.status_code == 200
    rows = await _rows(db_session)
    assert len(rows) == 2
    assert rows[0].role == DialogueRole.user
    assert rows[0].content == "你好"
    assert rows[1].role == DialogueRole.assistant
    assert rows[1].content == "流式回答"
    assert all(r.conversation_id == "cid-s" for r in rows)


# ── 降级 / 兜底 ──────────────────────────────────────────────────────────


async def test_record_failure_does_not_block_response(client, monkeypatch):
    """落库失败（DB 异常）仅告警，对话响应不受影响（演示不翻车）。"""
    async def fake_chat(**kwargs):
        return _dg_result(answer="正常回答", conversation_id="cid-x")

    monkeypatch.setattr(deepagents, "chat", fake_chat)

    async def boom(db, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(chat_agent, "record_dialogue", boom)
    resp = await client.post("/api/v1/dialogue/chat", json={"query": "你好"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "正常回答"


async def test_fallback_to_dify_does_not_record_local(client, db_session, monkeypatch):
    """主链路异常回落 Dify：本地不落库（数据源在 Dify，由轮询/回填负责）。"""
    from app.routers import dialogue

    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        return {"answer": "dify 兜底回答", "conversation_id": "cid-fb"}

    monkeypatch.setattr(deepagents, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await client.post("/api/v1/dialogue/chat", json={"query": "你好"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "dify 兜底回答"
    assert await _rows(db_session) == []
