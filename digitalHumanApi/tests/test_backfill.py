"""backfill.py 补拉脚本测试。

覆盖：分页拉取、去重与幂等、限流间隔、退避重试。
使用 httpx.MockTransport 模拟 Dify 响应（无外部 mock 库）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dialogue import Dialogue
from app.models.visitor import Visitor
from app.tasks.backfill import (
    BackfillStats,
    backfill_user,
    fetch_conversations_paginated,
    fetch_messages_paginated,
)

BASE_URL = "http://dify-test/v1"
HEADERS = {"Authorization": "Bearer test-key", "Content-Type": "application/json"}

# 分页响应工厂 ----------------------------------------------------------------


def _conv(conv_id: str, created_at: int = 1700000000) -> Dict:
    return {"id": conv_id, "name": conv_id, "created_at": created_at}


def _msg(msg_id: str, role: str, created_at: int = 1700000000, content: str = "hi") -> Dict:
    return {
        "id": msg_id,
        "role": role,
        "created_at": created_at,
        "content": content,
    }


def _page_response(items: List[Dict], cursor: str | None, has_more: bool) -> Dict:
    data: Dict = {"limit": 100, "has_more": has_more, "data": items}
    if cursor:
        data["cursor"] = cursor
    return data


def make_mock_client(
    conversations: Dict[str, Dict],
    messages: Dict[str, Dict],
) -> httpx.AsyncClient:
    """构造 MockTransport client。

    conversations: {cursor_param: page_dict}（None 键为首页）
    messages: {conversation_id: {cursor_param: page_dict}}（None 键为首页）
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if request.url.path.endswith("/conversations"):
            cursor = params.get("cursor")
            page = conversations.get(cursor)
            if page is None:
                return httpx.Response(404, json={"error": "unknown cursor"})
            return httpx.Response(200, json=page)
        if request.url.path.endswith("/messages"):
            conv_id = params.get("conversation_id")
            cursor = params.get("cursor")
            conv_pages = messages.get(conv_id, {})
            page = conv_pages.get(cursor)
            if page is None:
                return httpx.Response(404, json={"error": "unknown cursor"})
            return httpx.Response(200, json=page)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE_URL
    )


# 分页逻辑 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_conversations_paginated_follows_cursor():
    client = make_mock_client(
        conversations={
            None: _page_response([_conv("c1"), _conv("c2")], "cur-1", True),
            "cur-1": _page_response([_conv("c3")], None, False),
        },
        messages={},
    )
    result = await fetch_conversations_paginated(
        client, BASE_URL, HEADERS, "u1", interval=0
    )
    assert [c["id"] for c in result] == ["c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_fetch_messages_paginated_follows_cursor():
    client = make_mock_client(
        conversations={},
        messages={
            "conv-1": {
                None: _page_response(
                    [_msg("m1", "user"), _msg("m2", "assistant")], "cur-m", True
                ),
                "cur-m": _page_response([_msg("m3", "user")], None, False),
            }
        },
    )
    result = await fetch_messages_paginated(
        client, BASE_URL, HEADERS, "conv-1", "u1", interval=0
    )
    assert [m["id"] for m in result] == ["m1", "m2", "m3"]


@pytest.mark.asyncio
async def test_fetch_conversations_stops_when_no_more():
    client = make_mock_client(
        conversations={None: _page_response([_conv("c1")], None, False)},
        messages={},
    )
    result = await fetch_conversations_paginated(
        client, BASE_URL, HEADERS, "u1", interval=0
    )
    assert [c["id"] for c in result] == ["c1"]


@pytest.mark.asyncio
async def test_fetch_conversations_skips_failed_page():
    """中间页失败（重试耗尽）时跳过该页，不中断整体。"""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("cursor") == "bad":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(
            200,
            json=_page_response(
                [_conv("c1")], "bad", True
            ) if params.get("cursor") is None
            else _page_response([], None, False),
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=BASE_URL
    )
    result = await fetch_conversations_paginated(
        client, BASE_URL, HEADERS, "u1", interval=0, max_retries=2, backoff_base=0
    )
    assert [c["id"] for c in result] == ["c1"]


# 去重与幂等 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_dedup_and_idempotent(db_session: AsyncSession):
    client = make_mock_client(
        conversations={
            None: _page_response([_conv("conv-1")], None, False),
        },
        messages={
            "conv-1": {
                None: _page_response(
                    [_msg("m1", "user"), _msg("m2", "assistant")], None, False
                ),
            }
        },
    )

    # 第一次：两条全部插入
    stats = await backfill_user(
        db_session, client, BASE_URL, HEADERS, "u1", interval=0
    )
    assert stats.inserted == 2
    assert stats.messages_seen == 2
    assert stats.skipped_duplicates == 0
    assert stats.conversations_seen == 1

    count = (
        await db_session.execute(select(func.count()).select_from(Dialogue))
    ).scalar_one()
    assert count == 2

    # 第二次（重跑）：幂等，不重复插入
    stats2 = await backfill_user(
        db_session, client, BASE_URL, HEADERS, "u1", interval=0
    )
    assert stats2.inserted == 0
    assert stats2.skipped_duplicates == 2
    count2 = (
        await db_session.execute(select(func.count()).select_from(Dialogue))
    ).scalar_one()
    assert count2 == 2


@pytest.mark.asyncio
async def test_backfill_dedup_across_conversations(db_session: AsyncSession):
    """同一 message id 出现在两个 conversation 里（异常数据）也只插一次。"""
    client = make_mock_client(
        conversations={
            None: _page_response([_conv("conv-1"), _conv("conv-2")], None, False),
        },
        messages={
            "conv-1": {None: _page_response([_msg("m1", "user")], None, False)},
            "conv-2": {None: _page_response([_msg("m1", "user")], None, False)},
        },
    )
    stats = await backfill_user(
        db_session, client, BASE_URL, HEADERS, "u1", interval=0
    )
    assert stats.inserted == 1
    assert stats.skipped_duplicates == 1


@pytest.mark.asyncio
async def test_backfill_fields_mapping(db_session: AsyncSession):
    """role / created_at / content 字段映射与 polling 一致。"""
    created = 1700000123
    client = make_mock_client(
        conversations={None: _page_response([_conv("conv-1")], None, False)},
        messages={
            "conv-1": {
                None: _page_response(
                    [
                        _msg("m1", "user", created, "你好"),
                        _msg("m2", "assistant", created, "你好呀"),
                    ],
                    None,
                    False,
                )
            }
        },
    )
    await backfill_user(db_session, client, BASE_URL, HEADERS, "u1", interval=0)
    rows = (
        (await db_session.execute(select(Dialogue).order_by(Dialogue.id))).scalars()
    ).all()
    assert rows[0].role.value == "user"
    assert rows[0].content == "你好"
    assert rows[1].role.value == "assistant"
    assert rows[0].timestamp == datetime.fromtimestamp(
        created, tz=timezone.utc
    ).replace(tzinfo=None)
    assert rows[0].conversation_id == "conv-1"
    assert rows[0].visitor_id == "u1"
    visitor = (
        await db_session.execute(select(Visitor).where(Visitor.id == "u1"))
    ).scalar_one()
    assert visitor is not None


# 限流与退避 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_respects_interval(db_session: AsyncSession, monkeypatch):
    """interval > 0 时每页之间 sleep；interval=0 不 sleep。"""
    sleeps: List[float] = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    monkeypatch.setattr("app.tasks.backfill.asyncio.sleep", fake_sleep)

    client = make_mock_client(
        conversations={
            None: _page_response([_conv("c1")], "cur", True),
            "cur": _page_response([_conv("c2")], None, False),
        },
        messages={
            "c1": {None: _page_response([], None, False)},
            "c2": {None: _page_response([], None, False)},
        },
    )
    await backfill_user(
        db_session, client, BASE_URL, HEADERS, "u1", interval=1.5
    )
    assert sleeps, "interval>0 时必须 sleep"
    assert all(s == 1.5 for s in sleeps)

    sleeps.clear()
    await backfill_user(db_session, client, BASE_URL, HEADERS, "u1", interval=0)
    assert sleeps == []


@pytest.mark.asyncio
async def test_request_retry_with_backoff(monkeypatch):
    """首次 500 后重试成功；退避等待时间按指数增长。"""
    from app.tasks.backfill import _request_with_retry

    calls = {"n": 0}
    sleeps: List[float] = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    monkeypatch.setattr("app.tasks.backfill.asyncio.sleep", fake_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await _request_with_retry(
        client, "GET", f"{BASE_URL}/conversations",
        params={"user": "u1"}, max_retries=3, backoff_base=1.0,
    )
    assert result == {"ok": True}
    assert calls["n"] == 2
    assert sleeps == [1.0]  # 第一次失败后等待 1s


@pytest.mark.asyncio
async def test_request_passes_headers(monkeypatch):
    """请求必须携带调用方传入的 headers（回归：曾漏传 Authorization）。"""
    from app.tasks.backfill import _request_with_retry

    seen: Dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await _request_with_retry(
        client,
        "GET",
        f"{BASE_URL}/conversations",
        params={"user": "u1"},
        headers=HEADERS,
    )
    assert result == {"ok": True}
    assert seen.get("authorization") == "Bearer test-key"


@pytest.mark.asyncio
async def test_request_returns_none_after_retries_exhausted(monkeypatch):
    from app.tasks.backfill import _request_with_retry

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await _request_with_retry(
        client, "GET", f"{BASE_URL}/conversations",
        params={"user": "u1"}, max_retries=3, backoff_base=0,
    )
    assert result is None
