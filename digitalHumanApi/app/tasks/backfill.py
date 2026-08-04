"""Dify 历史对话一次性补拉脚本（幂等、可重跑）。

用途
----
阶段 0 迁移盘点/补拉：把 Dify 侧的对话历史按 external_message_id 去重后
补入本地 dialogues 表。与 `polling.py`（增量轮询）互补，本模块用于历史回填。

设计要点
--------
- **不动主链路**：不修改 dify_client.py / polling.py，自带分页与节流实现。
- **分页**：Dify 服务端 API 的 /conversations 与 /messages 均支持
  `limit`（1-100）+ `cursor` 分页，响应携带 `has_more` 与 `cursor`。
- **去重/幂等**：以 Dify message `id`（external_message_id）为唯一键，
  写入前预查 + 数据库 UNIQUE 约束兜底；重跑只补缺失部分。
- **节流**：相邻 HTTP 请求间隔默认 >= 1 秒（`--interval 1.0`），避免触发 Dify 限流。
- **退避重试**：HTTP 错误/超时按指数退避重试（默认 3 次，`0.5 * 2^n` 秒）。

用法
----
    cd digitalHumanApi
    python -m app.tasks.backfill [--user <dify-user>] [--interval 1.0] [--page-size 100]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.dialogue import Dialogue, DialogueRole
from app.models.visitor import Visitor

logger = logging.getLogger(__name__)

# Dify 服务端 API 单页上限
MAX_PAGE_SIZE = 100
# 默认限流保护间隔（秒）
DEFAULT_INTERVAL = 1.0


@dataclass
class BackfillStats:
    """一次补拉的统计结果。"""

    user: str
    conversations_seen: int = 0
    messages_seen: int = 0
    inserted: int = 0
    skipped_duplicates: int = 0
    failed_conversations: List[str] = field(default_factory=list)


async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
    backoff_base: float = 0.5,
    label: str = "",
) -> Optional[Dict[str, Any]]:
    """带指数退避的 HTTP 请求，返回 JSON dict；重试耗尽返回 None。"""
    for attempt in range(max_retries):
        try:
            response = await client.request(
                method, url, params=params, headers=headers
            )
            response.raise_for_status()
            return response.json()
        except (
            httpx.HTTPStatusError,
            httpx.TimeoutException,
            httpx.ConnectError,
        ) as exc:
            if attempt < max_retries - 1:
                wait = backoff_base * (2**attempt)
                logger.warning(
                    "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                    label or url,
                    attempt + 1,
                    max_retries,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "%s failed after %d attempts: %s",
                    label or url,
                    max_retries,
                    exc,
                )
        except Exception as exc:  # 非网络类异常不重试
            logger.warning("%s unexpected error: %s", label or url, exc)
            return None
    return None


async def fetch_conversations_paginated(
    client: httpx.AsyncClient,
    base_url: str,
    headers: Dict[str, str],
    user: str,
    *,
    page_size: int = MAX_PAGE_SIZE,
    interval: float = DEFAULT_INTERVAL,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> List[Dict[str, Any]]:
    """分页拉取某 user 的全部 conversations（按创建时间倒序）。

    Dify API: GET /conversations?user=xxx&limit=100&cursor=xxx
    返回 [{id, ...}, ...]；单页失败跳过该页并记日志（不中断整体）。
    """
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    collected: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params: Dict[str, Any] = {
            "user": user,
            "limit": page_size,
            "sort_by": "-created_at",
        }
        if cursor:
            params["cursor"] = cursor
        data = await _request_with_retry(
            client,
            "GET",
            f"{base_url.rstrip('/')}/conversations",
            params=params,
            headers=headers,
            max_retries=max_retries,
            backoff_base=backoff_base,
            label=f"Dify conversations (user={user})",
        )
        if data is None:
            logger.error("Skipping conversations page for user=%s", user)
            break
        items = data.get("data", [])
        collected.extend(items)
        cursor = data.get("cursor")
        if not data.get("has_more") or not cursor or not items:
            break
        if interval > 0:
            await asyncio.sleep(interval)
    return collected


async def fetch_messages_paginated(
    client: httpx.AsyncClient,
    base_url: str,
    headers: Dict[str, str],
    conversation_id: str,
    user: str,
    *,
    page_size: int = MAX_PAGE_SIZE,
    interval: float = DEFAULT_INTERVAL,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> List[Dict[str, Any]]:
    """分页拉取某个 conversation 的全部 messages（按创建时间倒序）。

    Dify API: GET /messages?conversation_id=xxx&user=xxx&limit=100&cursor=xxx
    """
    page_size = min(max(1, page_size), MAX_PAGE_SIZE)
    collected: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        params: Dict[str, Any] = {
            "conversation_id": conversation_id,
            "user": user,
            "limit": page_size,
        }
        if cursor:
            params["cursor"] = cursor
        data = await _request_with_retry(
            client,
            "GET",
            f"{base_url.rstrip('/')}/messages",
            params=params,
            headers=headers,
            max_retries=max_retries,
            backoff_base=backoff_base,
            label=f"Dify messages (conv={conversation_id})",
        )
        if data is None:
            logger.error(
                "Skipping messages page for conversation=%s", conversation_id
            )
            break
        items = data.get("data", [])
        collected.extend(items)
        cursor = data.get("cursor")
        if not data.get("has_more") or not cursor or not items:
            break
        if interval > 0:
            await asyncio.sleep(interval)
    return collected


def _to_dialogue(
    user: str, conversation_id: str, msg: Dict[str, Any]
) -> Optional[Dialogue]:
    """把 Dify message dict 映射为 Dialogue 对象（字段规则与 polling.py 一致）。"""
    external_id = msg.get("id")
    if not external_id:
        return None
    role_str = msg.get("role", "user")
    try:
        role = DialogueRole(role_str)
    except ValueError:
        role = DialogueRole.user
    created_at = msg.get("created_at")
    if isinstance(created_at, (int, float)):
        timestamp = datetime.fromtimestamp(created_at, tz=timezone.utc)
    else:
        timestamp = datetime.now(timezone.utc)
    return Dialogue(
        visitor_id=user,
        conversation_id=conversation_id,
        external_message_id=external_id,
        content=msg.get("content", "") or msg.get("query", ""),
        role=role,
        timestamp=timestamp,
    )


async def backfill_user(
    db,
    client: httpx.AsyncClient,
    base_url: str,
    headers: Dict[str, str],
    user: str,
    *,
    page_size: int = MAX_PAGE_SIZE,
    interval: float = DEFAULT_INTERVAL,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> BackfillStats:
    """补拉单个 user 的全部历史对话到本地库。幂等：重复执行不重复插入。"""
    stats = BackfillStats(user=user)

    # 确保虚拟访客存在（与 polling.py 一致）
    existing = await db.execute(select(Visitor).where(Visitor.id == user))
    if existing.scalar_one_or_none() is None:
        db.add(Visitor(id=user))
        await db.flush()

    # 已入库的 external_message_id 集合（用于预去重）
    rows = await db.execute(
        select(Dialogue.external_message_id).where(
            Dialogue.external_message_id.is_not(None)
        )
    )
    known_ids = {row[0] for row in rows.all()}

    conversations = await fetch_conversations_paginated(
        client,
        base_url,
        headers,
        user,
        page_size=page_size,
        interval=interval,
        max_retries=max_retries,
        backoff_base=backoff_base,
    )
    stats.conversations_seen = len(conversations)

    for conv in conversations:
        conversation_id = conv.get("id")
        if not conversation_id:
            continue
        messages = await fetch_messages_paginated(
            client,
            base_url,
            headers,
            conversation_id,
            user,
            page_size=page_size,
            interval=interval,
            max_retries=max_retries,
            backoff_base=backoff_base,
        )
        for msg in messages:
            external_id = msg.get("id")
            if not external_id:
                continue
            stats.messages_seen += 1
            if external_id in known_ids:
                stats.skipped_duplicates += 1
                continue
            dialogue = _to_dialogue(user, conversation_id, msg)
            if dialogue is None:
                continue
            db.add(dialogue)
            known_ids.add(external_id)
            stats.inserted += 1
        # 页间限流由 fetch 内部处理；conversation 之间也保持间隔
        if interval > 0:
            await asyncio.sleep(interval)

    if stats.inserted > 0:
        try:
            await db.commit()
            logger.info(
                "Backfill committed: user=%s inserted=%d skipped=%d "
                "(conversations=%d)",
                user,
                stats.inserted,
                stats.skipped_duplicates,
                stats.conversations_seen,
            )
        except Exception:
            await db.rollback()
            logger.exception(
                "Backfill commit rolled back for user=%s, rerun to retry", user
            )
            stats.inserted = 0
    return stats


async def backfill_all(
    *,
    users: Optional[List[str]] = None,
    page_size: int = MAX_PAGE_SIZE,
    interval: float = DEFAULT_INTERVAL,
    max_retries: int = 3,
    backoff_base: float = 0.5,
) -> List[BackfillStats]:
    """补拉配置的全部 user（默认 settings.DIFY_LIGHT4AI_USER）。"""
    targets = users if users else settings.DIFY_LIGHT4AI_USER
    if not targets:
        logger.warning("No Dify users configured, nothing to backfill")
        return []

    base_url = settings.DIFY_API_URL.rstrip("/")
    headers = {
        "Authorization": f"Bearer {settings.DIFY_API_KEY}",
        "Content-Type": "application/json",
    }
    # 独立 CLI 运行时无 FastAPI lifespan，先幂等建表（IF NOT EXISTS）
    from app.core.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    all_stats: List[BackfillStats] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        for user in targets:
            async with async_session_factory() as db:
                stats = await backfill_user(
                    db,
                    client,
                    base_url,
                    headers,
                    user,
                    page_size=page_size,
                    interval=interval,
                    max_retries=max_retries,
                    backoff_base=backoff_base,
                )
            all_stats.append(stats)
    return all_stats


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dify 历史对话补拉（幂等、可重跑）",
    )
    parser.add_argument("--user", action="append", help="Dify user（可多次指定）")
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="相邻 HTTP 请求间隔秒数（默认 1.0，Dify 限流保护）",
    )
    parser.add_argument(
        "--page-size", type=int, default=MAX_PAGE_SIZE, help="每页条数（1-100）"
    )
    parser.add_argument(
        "--max-retries", type=int, default=3, help="单页请求失败重试次数"
    )
    return parser.parse_args(argv)


async def _main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    users = args.user or None
    if users is None and not settings.DIFY_LIGHT4AI_USER:
        print("错误：未配置 DIFY_LIGHT4AI_USER，请用 --user 指定")
        return 1
    stats_list = await backfill_all(
        users=users,
        page_size=args.page_size,
        interval=args.interval,
        max_retries=args.max_retries,
    )
    for stats in stats_list:
        print(
            f"user={stats.user}: conversations={stats.conversations_seen} "
            f"messages={stats.messages_seen} inserted={stats.inserted} "
            f"skipped={stats.skipped_duplicates}"
        )
    return 0


def main() -> None:
    """CLI 入口（python -m app.tasks.backfill）。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
