import asyncio
import logging
from datetime import datetime, timezone
from typing import List

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.dialogue import Dialogue, DialogueRole
from app.models.visitor import Visitor
from app.services.dify_client import dify_client

logger = logging.getLogger(__name__)


async def poll_once() -> int:
    """Execute one polling cycle. Returns number of new messages inserted.

    Strategy: light4ai passes a fixed user (DIFY_LIGHT4AI_USER) to Dify for
    all conversations. We query /conversations?user=xxx with that fixed user,
    then /messages?conversation_id=xxx for each conversation.

    All dialogues are attributed to a virtual visitor with id = that user.
    Individual visitor-level correlation is lost (light4ai limitation), but
    aggregate stats (sentiment, satisfaction, concerns, spot heatmap) work fine.
    """
    user = settings.DIFY_LIGHT4AI_USER
    new_count = 0
    async with async_session_factory() as db:
        # Upsert virtual visitor for the light4ai user
        existing = await db.execute(
            select(Visitor).where(Visitor.id == user)
        )
        if existing.scalar_one_or_none() is None:
            db.add(Visitor(id=user))
            await db.flush()
            logger.info("Created virtual visitor for light4ai user: %s", user)

        # Fetch all conversations for this user
        conversations = await dify_client.get_conversations(user=user)
        if not conversations:
            logger.debug("No conversations found for user %s", user)
            return 0

        for conv in conversations:
            conversation_id = conv.get("id")
            if not conversation_id:
                continue

            messages = await dify_client.get_conversation_messages(
                conversation_id, user=user
            )

            for msg in messages:
                external_id = msg.get("id")
                if not external_id:
                    continue

                # Dedup by external_message_id UNIQUE constraint
                existing_msg = await db.execute(
                    select(Dialogue).where(
                        Dialogue.external_message_id == external_id
                    )
                )
                if existing_msg.scalar_one_or_none() is not None:
                    continue

                # Map role string to enum
                role_str = msg.get("role", "user")
                try:
                    role = DialogueRole(role_str)
                except ValueError:
                    role = DialogueRole.user

                # Use Dify created_at if available, else fall back to now
                created_at = msg.get("created_at")
                if isinstance(created_at, (int, float)):
                    timestamp = datetime.fromtimestamp(created_at, tz=timezone.utc)
                else:
                    timestamp = datetime.now(timezone.utc)

                dialogue = Dialogue(
                    visitor_id=user,
                    conversation_id=conversation_id,
                    external_message_id=external_id,
                    content=msg.get("content", "") or msg.get("query", ""),
                    role=role,
                    timestamp=timestamp,
                )
                db.add(dialogue)
                new_count += 1

        if new_count > 0:
            try:
                await db.commit()
                logger.info(
                    "Polled %d new messages from %d conversations (user=%s)",
                    new_count,
                    len(conversations),
                    user,
                )
            except Exception:
                await db.rollback()
                logger.warning(
                    "Polling commit rolled back, will retry next cycle"
                )
                return 0

    return new_count


async def polling_loop() -> None:
    """Run the polling loop every 30 seconds until cancelled."""
    logger.info(
        "Polling task started (interval: 30s, user=%s)", settings.DIFY_LIGHT4AI_USER
    )
    try:
        while True:
            try:
                await poll_once()
            except Exception:
                logger.exception("Polling cycle failed, will retry in 30s")
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Polling task cancelled, shutting down")
