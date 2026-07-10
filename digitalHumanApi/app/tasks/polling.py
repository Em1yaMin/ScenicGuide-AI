import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_factory
from app.models.dialogue import Dialogue, DialogueRole
from app.models.visitor import Visitor
from app.services.dify_client import dify_client

logger = logging.getLogger(__name__)


async def poll_once() -> int:
    """Execute one polling cycle. Returns number of new messages inserted."""
    conversations = await dify_client.get_conversations()
    if not conversations:
        logger.debug("No active conversations found")
        return 0

    new_count = 0
    async with async_session_factory() as db:
        for conv in conversations:
            conversation_id = conv.get("id")
            if not conversation_id:
                continue

            # Extract visitor_id from conversation metadata
            metadata: Dict[str, Any] = conv.get("metadata", {}) or {}
            visitor_id: Optional[str] = metadata.get("visitor_id")

            # Skip conversations without a valid visitor_id — FK constraint
            if not visitor_id:
                logger.debug(
                    "Skipping conversation %s: no visitor_id in metadata",
                    conversation_id,
                )
                continue

            # Upsert visitor if not exists
            existing = await db.execute(
                select(Visitor).where(Visitor.id == visitor_id)
            )
            if existing.scalar_one_or_none() is None:
                db.add(Visitor(id=visitor_id))

            messages = await dify_client.get_conversation_messages(
                conversation_id
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

                dialogue = Dialogue(
                    visitor_id=visitor_id,
                    conversation_id=conversation_id,
                    external_message_id=external_id,
                    content=msg.get("content", ""),
                    role=role,
                    timestamp=datetime.now(timezone.utc),
                )
                db.add(dialogue)
                new_count += 1

        if new_count > 0:
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                logger.warning(
                    "Polling commit rolled back due to IntegrityError "
                    "(duplicate external_message_id), will retry next cycle"
                )
                return 0
            logger.info(
                "Polled %d new messages from %d conversations",
                new_count,
                len(conversations),
            )

    return new_count


async def polling_loop() -> None:
    """Run the polling loop every 30 seconds until cancelled."""
    logger.info("Polling task started (interval: 30s)")
    try:
        while True:
            try:
                await poll_once()
            except Exception:
                logger.exception("Polling cycle failed, will retry in 30s")
            await asyncio.sleep(30)
    except asyncio.CancelledError:
        logger.info("Polling task cancelled, shutting down")
