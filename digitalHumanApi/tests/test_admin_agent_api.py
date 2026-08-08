"""管理员 agent 对话 API（批次6 B4）测试：鉴权 + 非流式 + 流式 SSE。

覆盖：
1. 鉴权：两个端点（chat / chat/stream）无 token / 错误 token → 401
2. 非流式 /chat：X-Admin-Token 通过后返回 answer/conversation_id；
   LLM 未配置（LLMConfigError）→ 503
3. 流式 /chat/stream：SSE 事件序列（step → token → done）+ 响应头
   （text/event-stream / no-cache / X-Accel-Buffering）；LLM 未配置 →
   error(fallback=false) 事件

不依赖真实 LLM：monkeypatch admin_agent.chat / chat_stream。
"""

import json

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.llm import LLMConfigError
from app.services import admin_agent

ADMIN_TOKEN = "test-admin-token"
API = "/api/admin/agent"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)


def _auth(token: str = ADMIN_TOKEN) -> dict:
    return {"X-Admin-Token": token}


def _parse_sse(text: str) -> list:
    """解析 SSE 响应体为事件 dict 列表。"""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            events.append(json.loads(line[5:].strip()))
    return events


# ── 鉴权 ──────────────────────────────────────────────────────────────────


async def test_both_endpoints_require_admin_token(client: AsyncClient):
    """全部端点：无 token / 错误 token → 401（X-Admin-Token 鉴权）。"""
    body = {"query": "查询知识库"}
    for url in (f"{API}/chat", f"{API}/chat/stream"):
        resp = await client.post(url, json=body)
        assert resp.status_code == 401, f"{url} 无 token 应 401"
        resp = await client.post(url, json=body, headers=_auth("wrong-token"))
        assert resp.status_code == 401, f"{url} 错误 token 应 401"


# ── 非流式 /chat ──────────────────────────────────────────────────────────


async def test_chat_returns_answer_with_valid_token(client, monkeypatch):
    """/chat：X-Admin-Token 通过后返回 answer/conversation_id。"""
    captured = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"answer": "知识库共 3 条", "conversation_id": "cid-1"}

    monkeypatch.setattr(admin_agent, "chat", fake_chat)
    resp = await client.post(
        f"{API}/chat",
        json={"query": "查询知识库", "conversation_id": "cid-1"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["answer"] == "知识库共 3 条"
    assert data["conversation_id"] == "cid-1"
    assert captured["query"] == "查询知识库"
    assert captured["conversation_id"] == "cid-1"


async def test_chat_llm_not_configured_returns_503(client, monkeypatch):
    """/chat：LLM 未配置（LLMConfigError）→ 503 明确报错。"""

    async def raise_config(*args, **kwargs):
        raise LLMConfigError("no llm key")

    monkeypatch.setattr(admin_agent, "chat", raise_config)
    resp = await client.post(
        f"{API}/chat", json={"query": "你好"}, headers=_auth()
    )
    assert resp.status_code == 503
    assert "不可用" in resp.json()["detail"]


async def test_chat_unexpected_error_returns_503(client, monkeypatch):
    """/chat：其他异常 → 503。"""

    async def raise_err(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(admin_agent, "chat", raise_err)
    resp = await client.post(
        f"{API}/chat", json={"query": "你好"}, headers=_auth()
    )
    assert resp.status_code == 503


# ── 流式 /chat/stream ─────────────────────────────────────────────────────


async def test_stream_sse_event_sequence(client, monkeypatch):
    """/chat/stream：SSE step → token → done + 响应头（契约与用户侧一致）。"""
    captured = {}

    async def fake_stream(**kwargs):
        captured.update(kwargs)
        yield {"type": "step", "tool": "query_knowledge", "label": "正在查询知识库…"}
        yield {"type": "token", "text": "知识库"}
        yield {"type": "token", "text": "共 3 条"}
        yield {"type": "done", "conversation_id": "cid-s"}

    monkeypatch.setattr(admin_agent, "chat_stream", fake_stream)
    resp = await client.post(
        f"{API}/chat/stream",
        json={"query": "查询知识库"},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"
    events = _parse_sse(resp.text)
    assert [e["type"] for e in events] == ["step", "token", "token", "done"]
    assert events[0] == {
        "type": "step",
        "tool": "query_knowledge",
        "label": "正在查询知识库…",
    }
    assert events[-1]["conversation_id"] == "cid-s"
    assert captured["query"] == "查询知识库"


async def test_stream_llm_not_configured_yields_error_event(client, monkeypatch):
    """/chat/stream：LLM 未配置 → error(fallback=false) 事件（无 Dify 兜底）。"""

    async def raise_config(*args, **kwargs):
        raise LLMConfigError("no llm key")

    monkeypatch.setattr(admin_agent, "chat_stream", raise_config)
    resp = await client.post(
        f"{API}/chat/stream", json={"query": "你好"}, headers=_auth()
    )
    assert resp.status_code == 200  # SSE 已开始，错误以事件形式送达
    events = _parse_sse(resp.text)
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["fallback"] is False
    assert "不可用" in events[0]["message"]


async def test_stream_unexpected_error_yields_error_event(client, monkeypatch):
    """/chat/stream：其他异常 → error 事件后结束。"""

    async def raise_err(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(admin_agent, "chat_stream", raise_err)
    resp = await client.post(
        f"{API}/chat/stream", json={"query": "你好"}, headers=_auth()
    )
    events = _parse_sse(resp.text)
    assert events[0]["type"] == "error"
    assert events[0]["fallback"] is False
