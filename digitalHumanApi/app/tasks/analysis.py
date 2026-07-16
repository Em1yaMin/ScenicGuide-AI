import asyncio
import logging
from typing import Any, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation
from app.services.dify_client import dify_client

logger = logging.getLogger(__name__)

MAX_BATCH_SIZE = 50  # Limit evaluations per cycle


from app.services.dify_helpers import parse_workflow_output as _parse_workflow_output


def _normalize_string_list(raw: Any) -> Optional[List[str]]:
    """Strip whitespace from a list of strings; drop non-string/empty items."""
    if not isinstance(raw, list):
        return None
    normalized: List[str] = []
    seen: set = set()
    for item in raw:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped and stripped not in seen:
                normalized.append(stripped)
                seen.add(stripped)
    return normalized if normalized else None


async def evaluate_dialogue(
    db: AsyncSession, dialogue: Dialogue
) -> Optional[Evaluation]:
    """Evaluate a single dialogue via Dify workflow."""
    result = await dify_client.trigger_workflow(
        {"input": dialogue.content}
    )
    if result is None:
        return None

    # Extract text output from Dify response envelope
    raw_output = ""
    try:
        raw_output = (
            result.get("data", {}).get("outputs", {}).get("text", "")
        )
    except AttributeError:
        logger.warning(
            "Unexpected Dify response structure for dialogue %d",
            dialogue.id,
        )
        return None

    if not raw_output:
        logger.warning(
            "Empty workflow output for dialogue %d, will retry next cycle",
            dialogue.id,
        )
        return None

    parsed = _parse_workflow_output(raw_output)
    if parsed is None:
        logger.warning(
            "Parse failure for dialogue %d, will retry next cycle",
            dialogue.id,
        )
        return None

    # Map Dify output fields to Evaluation columns (flexible naming)
    sentiment_label: Optional[str] = (
        parsed.get("sentiment_label") or parsed.get("sentiment")
    )
    satisfaction_score: Optional[int] = None
    raw_score = parsed.get("satisfaction_score")
    if raw_score is not None:
        try:
            satisfaction_score = int(raw_score)
        except (ValueError, TypeError):
            pass

    concern_tags: Optional[List[str]] = (
        parsed.get("concern_tags") or parsed.get("concerns")
    )
    mentioned_spots: Optional[List[str]] = _normalize_string_list(
        parsed.get("mentioned_spots") or parsed.get("spots")
    )

    evaluation = Evaluation(
        dialogue_id=dialogue.id,
        sentiment_label=sentiment_label,
        satisfaction_score=satisfaction_score,
        concern_tags=(
            concern_tags if isinstance(concern_tags, list) else None
        ),
        mentioned_spots=mentioned_spots,
        raw_result=parsed,
    )
    return evaluation


async def batch_evaluate() -> int:
    """Run one batch evaluation cycle. Returns number evaluated."""
    async with async_session_factory() as db:
        # Find unevaluated dialogues (LEFT JOIN where evaluation IS NULL)
        result = await db.execute(
            select(Dialogue)
            .outerjoin(
                Evaluation, Evaluation.dialogue_id == Dialogue.id
            )
            .where(
                Evaluation.id.is_(None),
                Dialogue.content != "",
            )
            .limit(MAX_BATCH_SIZE)
        )
        dialogues: List[Dialogue] = list(result.scalars().all())

        if not dialogues:
            logger.debug("No dialogues pending evaluation")
            return 0

        evaluated = 0
        for dialogue in dialogues:
            # Double-check to avoid IntegrityError on unique constraint
            existing = await db.execute(
                select(Evaluation).where(
                    Evaluation.dialogue_id == dialogue.id
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue

            evaluation = await evaluate_dialogue(db, dialogue)
            if evaluation is not None:
                db.add(evaluation)
                evaluated += 1

        if evaluated > 0:
            try:
                await db.commit()
                logger.info(
                    "Evaluated %d dialogues in batch", evaluated
                )
            except Exception:
                await db.rollback()
                logger.warning(
                    "Evaluation commit rolled back, will retry next cycle"
                )
                return 0

        return evaluated


async def analysis_loop() -> None:
    """Run the analysis loop every 5 minutes until cancelled."""
    logger.info(
        "Analysis task started (interval: 300s, max batch: %d)",
        MAX_BATCH_SIZE,
    )
    try:
        while True:
            try:
                await batch_evaluate()
            except Exception:
                logger.exception(
                    "Analysis cycle failed, will retry in 300s"
                )
            await asyncio.sleep(300)
    except asyncio.CancelledError:
        logger.info("Analysis task cancelled, shutting down")
