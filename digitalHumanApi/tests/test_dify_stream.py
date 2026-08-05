"""Dify 流式客户端（app/services/dify_stream.py）单测。

覆盖：ThinkFilter 跨 chunk 标签分割过滤、stream_dify_chat 的 SSE 事件
解析（message → token / message_end → done / error → 抛错）、非 SSE
数据行容错、未收到 message_end 时尽力收尾。
"""

import json

import httpx
import pytest

from app.core.config import settings
from app.services import dify_stream


# ── ThinkFilter ───────────────────────────────────────────────────────────


def test_think_filter_removes_think_block():
    """完整 think 块被移除，块外文本透出（尾部短文本延迟到 flush）。"""
    f = dify_stream.ThinkFilter()
    out = f.feed("开始<think>内部思考</think>回答")
    assert out == "开始"  # 尾部可能是不完整标签，延迟输出
    assert f.flush() == "回答"


def test_think_filter_handles_split_tags():
    """跨 chunk 分割的标签（<th / ink>、</th / ink>）正确处理。"""
    f = dify_stream.ThinkFilter()
    assert f.feed("前<th") == ""  # 短 buffer 保留等待标签完整
    assert f.feed("ink>思考中") == "前"  # 识别完整 <think>，进入思考模式
    assert f.feed("</th") == ""
    assert f.feed("ink>后") == ""  # 闭合后残留延迟输出
    assert f.flush() == "后"


def test_think_filter_flush():
    """flush 返回残留；处于 think 块内时返回空串。"""
    f = dify_stream.ThinkFilter()
    f.feed("<think>未闭合")
    assert f.flush() == ""
    f2 = dify_stream.ThinkFilter()
    f2.feed("正常文本")
    assert f2.flush() == "正常文本"


# ── stream_dify_chat ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _pin_dify_config(monkeypatch):
    monkeypatch.setattr(settings, "DIFY_API_URL", "https://dify.test/v1")
    monkeypatch.setattr(settings, "DIFY_API_KEY", "app-test-key")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _patch_httpx(monkeypatch, handler):
    """让 dify_stream 内部的 httpx.AsyncClient 使用 MockTransport。

    注意：MockTransport 构造时内部会创建 AsyncClient，必须先构造 transport；
    fake_client 内也须用替换前的原始类，避免递归。
    """
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(dify_stream.httpx, "AsyncClient", fake_client)


async def test_stream_dify_chat_token_and_done(monkeypatch):
    """message 事件 → token（think 块被过滤）；message_end → done（会话 id）。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        body = "".join(
            [
                _sse({"event": "ping"}),
                _sse({"event": "message", "answer": "你好<think>思考</think>"}),
                _sse({"event": "message", "answer": "景区"}),
                _sse({"event": "message_end", "answer": "你好景区", "conversation_id": "cid-1"}),
            ]
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    _patch_httpx(monkeypatch, handler)
    events = [
        e
        async for e in dify_stream.stream_dify_chat(
            "几点开门？", user="visitor", conversation_id=""
        )
    ]
    # think 块被过滤；ping 事件忽略
    assert events == [
        {"type": "token", "text": "你好"},
        {"type": "token", "text": "景区"},
        {"type": "done", "conversation_id": "cid-1"},
    ]
    # 请求体：streaming 模式 + 参数透传
    payload = calls[0]
    assert payload["response_mode"] == "streaming"
    assert payload["query"] == "几点开门？"
    assert payload["user"] == "visitor"
    assert payload["conversation_id"] == ""


async def test_stream_dify_chat_error_event_raises(monkeypatch):
    """Dify 返回 error 事件 → 抛 RuntimeError（由路由层转 error 事件）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = _sse({"event": "error", "message": "服务内部错误"})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    _patch_httpx(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="服务内部错误"):
        async for _ in dify_stream.stream_dify_chat("你好"):
            pass


async def test_stream_dify_chat_non_sse_lines_ignored(monkeypatch):
    """非 `data: {json}` 行（心跳/注释）被忽略，不影响事件解析。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            ": keep-alive comment\n\n"
            "hello\n\n"
            + _sse({"event": "message", "answer": "答"})
            + _sse({"event": "message_end", "conversation_id": "cid-x"})
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    _patch_httpx(monkeypatch, handler)
    events = [e async for e in dify_stream.stream_dify_chat("你好")]
    assert events == [
        {"type": "token", "text": "答"},
        {"type": "done", "conversation_id": "cid-x"},
    ]


async def test_stream_dify_chat_stream_end_without_message_end(monkeypatch):
    """流结束但未收到 message_end（异常断开）：尽力 flush 残留并收尾 done。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = _sse({"event": "message", "answer": "部分回答"})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    _patch_httpx(monkeypatch, handler)
    events = [e async for e in dify_stream.stream_dify_chat("你好")]
    assert events == [
        {"type": "token", "text": "部分回答"},
        {"type": "done", "conversation_id": ""},
    ]


async def test_stream_dify_chat_http_error_raises(monkeypatch):
    """非 2xx 响应 → raise_for_status 抛错（由路由层转 error 事件）。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _patch_httpx(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in dify_stream.stream_dify_chat("你好"):
            pass


async def test_stream_dify_chat_not_configured(monkeypatch):
    """Dify 未配置 → RuntimeError（与 blocking 路径一致）。"""
    monkeypatch.setattr(settings, "DIFY_API_URL", "")
    with pytest.raises(RuntimeError, match="not configured"):
        async for _ in dify_stream.stream_dify_chat("你好"):
            pass
