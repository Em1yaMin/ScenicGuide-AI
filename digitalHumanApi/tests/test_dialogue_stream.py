"""对话接口流式模式（streaming=true → SSE）单测。

覆盖：SSE 事件序列（step → token → done）、响应头（text/event-stream +
no-cache）、装配层链路异常回落 Dify（error(fallback=true) 标记 + Dify
流式事件）、双链路均失败礼貌报错（error(fallback=false)）、DIALOGUE_ENGINE=dify
强制 Dify 流式、会话 ID 移交规则（会话存储生成 id 回落时传空串）。
"""

import json

import pytest

from app.core.config import settings
from app.routers import dialogue
from app.services import chat_agent, deepagents


@pytest.fixture(autouse=True)
def _pin_dialogue_engine(monkeypatch):
    """固定默认引擎为 langchain（与生产默认一致）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "langchain")
    with chat_agent._conversations_lock:
        chat_agent._conversations.clear()
        chat_agent._conversation_meta.clear()
        chat_agent._generated_conversation_ids.clear()


async def _chat(client, query="你好", **kwargs):
    return await client.post(
        "/api/v1/dialogue/chat", json={"query": query, **kwargs}
    )


def _parse_sse(text: str) -> list:
    """解析 SSE 响应体为事件 dict 列表。"""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


# ── 装配层主链路（默认） ───────────────────────────────────────────────


async def test_streaming_sse_event_sequence(client, monkeypatch):
    """streaming=true → SSE：step → token → done，事件内容与 ChatResponse 对齐。"""
    captured = {}

    async def fake_stream(**kwargs):
        captured["kwargs"] = kwargs
        yield {"type": "step", "tool": "search_knowledge", "label": "正在检索知识库…"}
        yield {"type": "token", "text": "您"}
        yield {"type": "token", "text": "好"}
        yield {
            "type": "done",
            "conversation_id": "cid-dg",
            "rag_context": "【知识库】...",
            "rag_sources": [
                {"source": "s1.md", "score": 0.9, "namespace": "documents"}
            ],
        }

    monkeypatch.setattr(deepagents, "chat_stream", fake_stream)
    resp = await _chat(client, "有什么推荐？", streaming=True)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    events = _parse_sse(resp.text)
    assert [e["type"] for e in events] == ["step", "token", "token", "done"]
    assert events[0] == {
        "type": "step",
        "tool": "search_knowledge",
        "label": "正在检索知识库…",
    }
    assert events[-1]["conversation_id"] == "cid-dg"
    assert events[-1]["rag_sources"] == [
        {"source": "s1.md", "score": 0.9, "namespace": "documents"}
    ]
    # 参数透传：user 标识（无 token 时回退请求体 user）+ 对话参数
    assert captured["kwargs"]["user"] == "visitor"
    assert captured["kwargs"]["query"] == "有什么推荐？"
    assert captured["kwargs"]["use_rag"] is True
    assert captured["kwargs"]["rag_top_k"] == 3


async def test_streaming_done_ends_stream(client, monkeypatch):
    """done 事件后流结束（无多余帧）。"""
    async def fake_stream(**kwargs):
        yield {"type": "done", "conversation_id": "cid", "rag_context": "", "rag_sources": []}

    monkeypatch.setattr(deepagents, "chat_stream", fake_stream)
    resp = await _chat(client, "你好", streaming=True)
    events = _parse_sse(resp.text)
    assert len(events) == 1
    assert events[0]["type"] == "done"


# ── 故障回落 ───────────────────────────────────────────────────────────────


async def test_streaming_langchain_failure_falls_back_to_dify(client, monkeypatch):
    """装配层流式异常 → error(fallback=true) + 切换步骤 + Dify 流式事件。"""
    calls = {"langchain": 0, "dify": 0}

    async def boom(**kwargs):
        calls["langchain"] += 1
        yield {"type": "token", "text": "部分回答"}
        raise RuntimeError("LLM 服务故障")

    async def fake_dify_stream(query, user, conversation_id):
        calls["dify"] += 1
        yield {"type": "token", "text": "dify 兜底"}
        yield {"type": "done", "conversation_id": "cid-dify"}

    monkeypatch.setattr(deepagents, "chat_stream", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat_stream", fake_dify_stream)
    resp = await _chat(client, "你好", streaming=True)
    events = _parse_sse(resp.text)
    types = [e["type"] for e in events]
    # 部分 token → error(fallback) → step(切换) → token → done
    assert types == ["token", "error", "step", "token", "done"]
    error = events[1]
    assert error["fallback"] is True
    assert error["message"]
    assert calls == {"langchain": 1, "dify": 1}


async def test_streaming_both_fail_polite_error(client, monkeypatch):
    """双链路均失败 → 单一 error(fallback=false) 礼貌报错，接口仍 200。"""
    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify_stream(query, user, conversation_id):
        raise RuntimeError("Dify 也不可用")

    monkeypatch.setattr(deepagents, "chat_stream", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat_stream", fake_dify_stream)
    resp = await _chat(client, "你好", streaming=True)
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    # 错误事件是最后一个（其后没有更多事件）
    assert events[-1]["type"] == "error"
    assert events[-1]["fallback"] is False
    assert "暂时不可用" in events[-1]["message"]


async def test_streaming_engine_dify_forced(client, monkeypatch):
    """DIALOGUE_ENGINE=dify 强制 Dify 流式（无 fallback 标记事件）。"""
    monkeypatch.setattr(settings, "DIALOGUE_ENGINE", "dify")

    async def fake_dify_stream(query, user, conversation_id):
        yield {"type": "token", "text": "dify 回答"}
        yield {"type": "done", "conversation_id": "cid-dify"}

    monkeypatch.setattr(dialogue, "_call_dify_chat_stream", fake_dify_stream)
    resp = await _chat(client, "你好", streaming=True)
    events = _parse_sse(resp.text)
    assert [e["type"] for e in events] == ["token", "done"]
    assert events[-1]["conversation_id"] == "cid-dify"


async def test_streaming_dify_done_carries_rag_fields(client, monkeypatch):
    """Dify 兜底时 done 事件透出 RAG 预检索结果（与 ChatResponse 契约对齐）。"""
    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify_stream(query, user, conversation_id):
        yield {"type": "token", "text": "答"}
        yield {"type": "done", "conversation_id": "cid-dify"}

    monkeypatch.setattr(deepagents, "chat_stream", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat_stream", fake_dify_stream)
    async def fake_prepare_rag(query, use_rag, rag_top_k):
        return (
            "【知识库】...",
            [{"source": "s.md", "score": 0.8}],
            "modified query",
        )

    monkeypatch.setattr(dialogue, "_prepare_rag_context", fake_prepare_rag)
    resp = await _chat(client, "你好", streaming=True)
    events = _parse_sse(resp.text)
    done = [e for e in events if e["type"] == "done"][0]
    assert done["rag_context"] == "【知识库】..."
    assert done["rag_sources"] == [{"source": "s.md", "score": 0.8}]


async def test_streaming_fallback_blank_cid_for_langchain_owned_session(client, monkeypatch):
    """跨轮回落：id 由会话存储生成时，Dify 收到空 conversation_id。"""
    dify_calls = []
    lc_cid = chat_agent._get_or_create_conversation("")

    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify_stream(query, user, conversation_id):
        dify_calls.append(conversation_id)
        yield {"type": "done", "conversation_id": "cid-new"}

    monkeypatch.setattr(deepagents, "chat_stream", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat_stream", fake_dify_stream)
    resp = await _chat(client, "你好", streaming=True, conversation_id=lc_cid)
    assert resp.status_code == 200
    assert dify_calls == [""]


async def test_streaming_keeps_dify_owned_conversation_id(client, monkeypatch):
    """跨轮回落：id 由 Dify 生成时原样透传（延续 Dify 侧多轮上下文）。"""
    dify_calls = []
    reused = chat_agent._get_or_create_conversation("dify-cid")
    assert chat_agent.has_conversation("dify-cid") is False

    async def boom(**kwargs):
        raise RuntimeError("LLM 服务故障")

    async def fake_dify_stream(query, user, conversation_id):
        dify_calls.append(conversation_id)
        yield {"type": "done", "conversation_id": conversation_id}

    monkeypatch.setattr(deepagents, "chat_stream", boom)
    monkeypatch.setattr(dialogue, "_call_dify_chat_stream", fake_dify_stream)
    resp = await _chat(client, "你好", streaming=True, conversation_id="dify-cid")
    assert resp.status_code == 200
    assert dify_calls == ["dify-cid"]


# ── 非流式无回归 ───────────────────────────────────────────────────────────


async def test_streaming_false_still_blocking_json(client, monkeypatch):
    """streaming=false（默认）仍返回 ChatResponse JSON（非 SSE）。"""
    async def fake_chat(**kwargs):
        return {
            "answer": "blocking 回答",
            "conversation_id": "cid-b",
            "rag_context": "",
            "rag_sources": [],
            "intent": "simple",
        }

    monkeypatch.setattr(deepagents, "chat", fake_chat)
    resp = await _chat(client, "你好")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert data["answer"] == "blocking 回答"
    assert data["conversation_id"] == "cid-b"
