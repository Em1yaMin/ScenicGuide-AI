"""对话接口双轨开关（app/routers/dialogue.py）单测。

覆盖：默认装配层引擎（DIALOGUE_ENGINE=langchain 走 deepagents）、
DIALOGUE_ENGINE=dify 强制 Dify、装配层异常自动回落 Dify（响应字段兼容）、
Dify 兜底失败时礼貌报错。
"""

import pytest

from app.core.config import settings
from app.routers import dialogue
from app.services import chat_agent, deepagents


@pytest.fixture(autouse=True)
def _pin_dialogue_engine(monkeypatch):
    """固定默认引擎为 langchain（与生产默认一致）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "langchain")
    # 清理会话存储（装配层与 chat_agent 共用），避免跨测试污染归属判定
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


async def _chat(client, query="你好", **kwargs):
    return await client.post(
        "/api/v1/dialogue/chat", json={"query": query, **kwargs}
    )


def _dg_result(answer="装配层回答", conversation_id="cid-dg", **extra):
    """构造装配层 chat() 的 dict 返回（响应契约字段）。"""
    return {
        "answer": answer,
        "conversation_id": conversation_id,
        "rag_context": "【知识库】...",
        "rag_sources": [{"source": "s1.md", "score": 0.9, "namespace": "documents"}],
        "intent": "simple",
        **extra,
    }


# ── 装配层主链路（默认） ───────────────────────────────────────────────────


async def test_default_engine_langchain(client, monkeypatch):
    """默认（DIALOGUE_ENGINE=langchain）走 deepagents 装配层，响应字段兼容。"""
    called = {}

    async def fake_chat(**kwargs):
        called["kwargs"] = kwargs
        return _dg_result()

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await _chat(client, "有什么推荐？")
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "装配层回答"
    assert data["conversation_id"] == "cid-dg"
    assert data["rag_context"] == "【知识库】..."
    assert data["rag_sources"] == [
        {"source": "s1.md", "score": 0.9, "namespace": "documents"}
    ]
    # 批次8 B3：无路线规划时 route_data 为 null
    assert data.get("route_data") is None
    # user 标识透传（无 token 时回退请求体 user）
    assert called["kwargs"]["user"] == "visitor"
    assert called["kwargs"]["query"] == "有什么推荐？"
    assert called["kwargs"]["use_rag"] is True


async def test_default_engine_passes_route_data(client, monkeypatch):
    """批次8 B3：装配层 chat() 返回 route_data 时响应原样透出。"""
    route = {"ok": True, "data": {"mode": "walking", "distance": "1200",
                                    "duration": "960", "steps": []}}

    async def fake_chat(**kwargs):
        return _dg_result(route_data=route)

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await _chat(client, "从黄鹤楼到户部巷怎么走？")
    assert resp.status_code == 200
    assert resp.json()["route_data"] == route


async def test_langchain_passes_use_rag_and_top_k(client, monkeypatch):
    """请求参数透传：use_rag/rag_top_k/conversation_id 传给装配层。"""
    called = {}

    async def fake_chat(**kwargs):
        called["kwargs"] = kwargs
        return _dg_result(conversation_id="cid-x")

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    await _chat(client, use_rag=False, rag_top_k=5, conversation_id="cid-1")
    assert called["kwargs"]["use_rag"] is False
    assert called["kwargs"]["rag_top_k"] == 5
    assert called["kwargs"]["conversation_id"] == "cid-1"


# ── 双轨开关 ───────────────────────────────────────────────────────────────


async def test_engine_dify_forced(client, monkeypatch):
    """DIALOGUE_ENGINE=dify 强制走 Dify（不调用装配层）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "dify")

    async def fake_dify(query, user, conversation_id):
        return {"answer": "dify 回答", "conversation_id": "cid-dify"}

    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "dify 回答"
    assert data["conversation_id"] == "cid-dify"


async def test_engine_invalid_value_defaults_to_langchain(client, monkeypatch):
    """非法引擎值按 langchain 处理（宽容配置，双轨容错）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "garbage")

    async def fake_chat(**kwargs):
        return _dg_result()

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await _chat(client)
    assert resp.json()["answer"] == "装配层回答"


# ── 故障回落 ───────────────────────────────────────────────────────────────


async def test_langchain_failure_falls_back_to_dify(client, monkeypatch):
    """装配层链路异常 → 自动回落 Dify，响应字段兼容。"""
    calls = {"langchain": 0, "dify": 0}

    async def boom(**kwargs):
        calls["langchain"] += 1
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        calls["dify"] += 1
        return {"answer": "dify 兜底回答", "conversation_id": "cid-fb"}

    monkeypatch.setattr(deepagents, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "dify 兜底回答"
    assert data["conversation_id"] == "cid-fb"
    assert calls == {"langchain": 1, "dify": 1}


async def test_fallback_blank_cid_for_langchain_owned_session(client, monkeypatch):
    """跨轮回落：id 由会话存储生成（装配层生成）时，Dify 收到空 conversation_id
    （避免 400 conversation_not_exist，让 Dify 新建会话兜底回答）。"""
    dify_calls = []

    # 真实生成一个会话 id（登记为模块生成，归属判定复用 chat_agent 集合）
    lc_cid = chat_agent._get_or_create_conversation("")

    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        dify_calls.append(conversation_id)
        return {"answer": "dify 兜底回答", "conversation_id": "cid-new"}

    monkeypatch.setattr(deepagents, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client, conversation_id=lc_cid)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "dify 兜底回答"
    assert data["conversation_id"] == "cid-new"
    assert dify_calls == [""]


async def test_fallback_keeps_dify_owned_conversation_id(client, monkeypatch):
    """跨轮回落：id 由 Dify 生成（会话存储仅复用为会话 key）时原样透传，
    保证 Dify 侧多轮上下文延续（回归：上轮修复曾误判此路径）。"""
    dify_calls = []

    # 模拟 R2：Dify 生成的 id 被复用建会（不登记为模块生成）
    reused = chat_agent._get_or_create_conversation("dify-cid")
    assert reused == "dify-cid"
    assert chat_agent.has_conversation("dify-cid") is False

    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        dify_calls.append(conversation_id)
        return {"answer": "dify 兜底回答", "conversation_id": conversation_id}

    monkeypatch.setattr(deepagents, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client, conversation_id="dify-cid")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == "dify-cid"
    assert dify_calls == ["dify-cid"]


async def test_dify_fallback_failure_returns_polite_error(client, monkeypatch):
    """双链路均失败 → 礼貌错误文案，接口仍 200（演示不翻车）。"""
    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        raise RuntimeError("Dify 也不可用")

    monkeypatch.setattr(deepagents, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client)
    assert resp.status_code == 200
    data = resp.json()
    assert "暂时不可用" in data["answer"]
    assert data["conversation_id"] == ""
