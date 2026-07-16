"""推荐问句服务：聚合 Dashboard 数据 + 调用 Dify Workflow + 输出解析。"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation
from app.services.dify_client import dify_client
from app.services.dify_helpers import parse_workflow_output

logger = logging.getLogger(__name__)


def _parse_recommend_output(raw: Any) -> Optional[List[str]]:
    """Parse Dify workflow output and extract question list.

    Delegates think/markdown/JSON parsing to
    app/tasks/analysis.py:_parse_workflow_output(), then extracts the
    `questions` array with alias fallback.
    """
    parsed = parse_workflow_output(raw)
    if not isinstance(parsed, dict):
        return None
    # Extract questions with alias fallback
    questions = (
        parsed.get("questions")
        or parsed.get("recommended_questions")
        or parsed.get("suggestions")
    )
    if not isinstance(questions, list):
        return None
    # Deduplicate + strip
    cleaned: List[str] = []
    seen: set = set()
    for q in questions:
        if isinstance(q, str):
            stripped = q.strip()
            if stripped and stripped not in seen:
                cleaned.append(stripped)
                seen.add(stripped)
    return cleaned if cleaned else None


async def _aggregate_dashboard_7d(db: AsyncSession) -> Optional[Dict[str, Any]]:
    """Aggregate 7-day dashboard data for workflow input.

    Returns:
        Dict with concern_tags (JSON str), mentioned_spots (JSON str),
        sentiment_summary (str), total_dialogues (int).
        Returns None if no evaluation data exists (skip workflow call).
    """
    seven_days_ago = (
        datetime.now(timezone.utc).date() - timedelta(days=6)
    ).isoformat()

    # NOTE: func.date() prevents index usage on timestamp column.
    # For SQLite with small datasets this is negligible (~100 rows).
    # If data grows, rewrite as range query:
    #   Dialogue.timestamp >= seven_days_ago_start
    #   AND Dialogue.timestamp < today_start

    # 1. concern_tags — aggregate JSON arrays with 7-day filter
    concern_result = await db.execute(
        select(Evaluation.concern_tags)
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Evaluation.concern_tags.isnot(None),
            func.date(Dialogue.timestamp) >= seven_days_ago,
        )
    )
    concern_counter: Dict[str, int] = {}
    for row in concern_result.all():
        tags = row.concern_tags
        if isinstance(tags, list):
            for tag in tags:
                concern_counter[tag] = concern_counter.get(tag, 0) + 1
    concern_tags = [
        {"tag": tag, "count": count}
        for tag, count in sorted(
            concern_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # 2. mentioned_spots — aggregate JSON arrays with 7-day filter
    spot_result = await db.execute(
        select(Evaluation.mentioned_spots)
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Evaluation.mentioned_spots.isnot(None),
            func.date(Dialogue.timestamp) >= seven_days_ago,
        )
    )
    spot_counter: Dict[str, int] = {}
    for row in spot_result.all():
        spots = row.mentioned_spots
        if isinstance(spots, list):
            for spot in spots:
                spot_counter[spot] = spot_counter.get(spot, 0) + 1
    mentioned_spots = [
        {"spot": spot, "count": count}
        for spot, count in sorted(
            spot_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # 3. sentiment_summary — count labels in 7-day window
    sentiment_result = await db.execute(
        select(
            Evaluation.sentiment_label,
            func.count(Evaluation.id).label("cnt"),
        )
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Evaluation.sentiment_label.isnot(None),
            func.date(Dialogue.timestamp) >= seven_days_ago,
        )
        .group_by(Evaluation.sentiment_label)
    )
    sentiment_map = {"positive": 0, "negative": 0, "neutral": 0}
    for row in sentiment_result.all():
        label = (row.sentiment_label or "").lower()
        if label in sentiment_map:
            sentiment_map[label] = row.cnt
    sentiment_summary = (
        f"positive:{sentiment_map['positive']}, "
        f"negative:{sentiment_map['negative']}, "
        f"neutral:{sentiment_map['neutral']}"
    )

    # 4. total_dialogues — count dialogues in 7-day window
    dialogue_count_result = await db.execute(
        select(func.count(Dialogue.id)).where(
            func.date(Dialogue.timestamp) >= seven_days_ago
        )
    )
    total_dialogues = dialogue_count_result.scalar() or 0

    # Safety: skip workflow if no evaluation data exists.
    # We check evaluation-derived data (concern_tags + mentioned_spots) rather
    # than total_dialogues because polling_loop (30s) inserts dialogues before
    # analysis_loop (300s) runs evaluations — leading to total_dialogues>0 but
    # empty concern_tags/mentioned_spots, which would produce low-quality
    # recommendations from Dify.
    if not concern_tags and not mentioned_spots:
        logger.debug(
            "No evaluation data in 7-day window (dialogues=%d), skipping workflow call",
            total_dialogues,
        )
        return None

    # NOTE: Dify server rejects non-ASCII in request body (Flask encoding issue).
    # Use ensure_ascii=True (default) and English labels to keep inputs ASCII-safe.
    return {
        "concern_tags": json.dumps(concern_tags, ensure_ascii=True),
        "mentioned_spots": json.dumps(mentioned_spots, ensure_ascii=True),
        "sentiment_summary": sentiment_summary,
        # Dify text-input fields require string values
        "total_dialogues": str(total_dialogues),
    }


async def fetch_recommended_questions() -> Optional[List[str]]:
    """Full cycle: aggregate 7-day data → call Dify Workflow → parse → return questions.

    Returns:
        List of question strings on success, None on any failure (Dify call,
        empty output, parse error). Caller implements stale-while-revalidate.
    """
    # Phase 1: Aggregate dashboard data
    async with async_session_factory() as db:
        inputs = await _aggregate_dashboard_7d(db)

    if inputs is None:
        return None

    # Phase 2: Call Dify Recommend Workflow (uses DIFY_RECOMMEND_WORKFLOW_API_KEY)
    result = await dify_client.trigger_recommend_workflow(inputs)
    if result is None:
        logger.warning("Dify recommend workflow returned None")
        return None

    raw_output = ""
    try:
        raw_output = (
            result.get("data", {})
            .get("outputs", {})
            .get("text", "")
        )
    except AttributeError:
        logger.warning(
            "Unexpected Dify response structure for recommend workflow"
        )
        return None

    if not raw_output:
        logger.warning("Empty workflow output for recommend workflow")
        return None

    # Phase 3: Parse output
    return _parse_recommend_output(raw_output)
