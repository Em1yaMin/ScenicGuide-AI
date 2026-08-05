"""对话接口双轨开关（app/routers/dialogue.py）单测。

覆盖：默认 LangChain 引擎、DIALOGUE_ENGINE=dify 强制 Dify、
LangChain 异常自动回落 Dify（响应字段兼容）、Dify 兜底失败时礼貌报错。
"""

import pytest

from app.core.config import settings
from app.routers import dialogue
from app.services import chat_agent


@pytest.fixture(autouse=True)
def _pin_dialogue_engine(monkeypatch):
    """固定默认引擎为 langchain（与生产默认一致）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "langchain")


async def _chat(client, query="你好", **kwargs):
    return await client.post(
        "/api/v1/dialogue/chat", json={"query": query, **kwargs}
    )


# ── LangChain 主链路（默认） ───────────────────────────────────────────────


async def test_default_engine_langchain(client, monkeypatch):
    """默认（DIALOGUE_ENGINE=langchain）走 chat_agent，响应字段兼容。"""
    called = {}

    async def fake_chat(**kwargs):
        called["kwargs"] = kwargs
        return chat_agent.ChatAgentResult(
            answer="langchain 回答",
            conversation_id="cid-lc",
            rag_context="【知识库】...",
            rag_sources=[{"source": "s1.md", "score": 0.9, "namespace": "documents"}],
        )

    monkeypatch.setattr(chat_agent, "chat", fake_chat)
    resp = await _chat(client, "有什么推荐？")
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "langchain 回答"
    assert data["conversation_id"] == "cid-lc"
    assert data["rag_context"] == "【知识库】..."
    assert data["rag_sources"] == [
        {"source": "s1.md", "score": 0.9, "namespace": "documents"}
    ]
    # user 标识透传（无 token 时回退请求体 user）
    assert called["kwargs"]["user"] == "visitor"
    assert called["kwargs"]["query"] == "有什么推荐？"
    assert called["kwargs"]["use_rag"] is True


async def test_langchain_passes_use_rag_and_top_k(client, monkeypatch):
    """请求参数透传：use_rag/rag_top_k/conversation_id 传给 chat_agent。"""
    called = {}

    async def fake_chat(**kwargs):
        called["kwargs"] = kwargs
        return chat_agent.ChatAgentResult(answer="ok", conversation_id="cid-x")

    monkeypatch.setattr(chat_agent, "chat", fake_chat)
    await _chat(client, use_rag=False, rag_top_k=5, conversation_id="cid-1")
    assert called["kwargs"]["use_rag"] is False
    assert called["kwargs"]["rag_top_k"] == 5
    assert called["kwargs"]["conversation_id"] == "cid-1"


# ── 双轨开关 ───────────────────────────────────────────────────────────────


async def test_engine_dify_forced(client, monkeypatch):
    """DIALOGUE_ENGINE=dify 强制走 Dify（不调用 chat_agent）。"""
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
        return chat_agent.ChatAgentResult(answer="langchain 回答", conversation_id="cid")

    monkeypatch.setattr(chat_agent, "chat", fake_chat)
    resp = await _chat(client)
    assert resp.json()["answer"] == "langchain 回答"


# ── 故障回落 ───────────────────────────────────────────────────────────────


async def test_langchain_failure_falls_back_to_dify(client, monkeypatch):
    """LangChain 链路异常 → 自动回落 Dify，响应字段兼容。"""
    calls = {"langchain": 0, "dify": 0}

    async def boom(**kwargs):
        calls["langchain"] += 1
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        calls["dify"] += 1
        return {"answer": "dify 兜底回答", "conversation_id": "cid-fb"}

    monkeypatch.setattr(chat_agent, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "dify 兜底回答"
    assert data["conversation_id"] == "cid-fb"
    assert calls == {"langchain": 1, "dify": 1}


async def test_dify_fallback_failure_returns_polite_error(client, monkeypatch):
    """双链路均失败 → 礼貌错误文案，接口仍 200（演示不翻车）。"""
    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify(query, user, conversation_id):
        raise RuntimeError("Dify 也不可用")

    monkeypatch.setattr(chat_agent, "chat", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat", fake_dify)
    resp = await _chat(client)
    assert resp.status_code == 200
    data = resp.json()
    assert "暂时不可用" in data["answer"]
    assert data["conversation_id"] == ""
