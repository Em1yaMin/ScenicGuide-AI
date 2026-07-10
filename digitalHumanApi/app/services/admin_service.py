import logging
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evaluation import Evaluation
from app.schemas.admin import (
    ConcernRankItem,
    DashboardResponse,
    HotIssueItem,
    ReportResponse,
    SentimentPieItem,
    SentimentTrendItem,
    SpotHeatmapItem,
)

logger = logging.getLogger(__name__)


async def get_dashboard_stats(db: AsyncSession) -> DashboardResponse:
    """Aggregate dashboard statistics from evaluations."""

    # 1. Sentiment distribution
    sentiment_result = await db.execute(
        select(
            Evaluation.sentiment_label,
            func.count(Evaluation.id).label("cnt"),
        )
        .where(Evaluation.sentiment_label.isnot(None))
        .group_by(Evaluation.sentiment_label)
    )
    sentiment_map: Dict[str, int] = {
        "positive": 0,
        "negative": 0,
        "neutral": 0,
    }
    for row in sentiment_result.all():
        label = (row.sentiment_label or "").lower()
        if label in sentiment_map:
            sentiment_map[label] = row.cnt

    sentiment_trend = [
        SentimentTrendItem(
            date="today",
            positive=sentiment_map.get("positive", 0),
            negative=sentiment_map.get("negative", 0),
            neutral=sentiment_map.get("neutral", 0),
        )
    ]

    # 2. Satisfaction distribution (by score)
    score_result = await db.execute(
        select(Evaluation.satisfaction_score, func.count(Evaluation.id))
        .where(Evaluation.satisfaction_score.isnot(None))
        .group_by(Evaluation.satisfaction_score)
    )
    satisfaction_distribution: Dict[str, int] = {}
    for row in score_result.all():
        score = row.satisfaction_score
        if score is not None:
            satisfaction_distribution[str(score)] = row[1]

    # 3. Hot issues — aggregate concern_tags (JSON array) in Python
    all_concerns = await db.execute(
        select(Evaluation.concern_tags).where(
            Evaluation.concern_tags.isnot(None)
        )
    )
    concern_counter: Dict[str, int] = {}
    for row in all_concerns.all():
        tags = row.concern_tags
        if isinstance(tags, list):
            for tag in tags:
                concern_counter[tag] = concern_counter.get(tag, 0) + 1

    hot_issues = [
        HotIssueItem(concern=tag, count=count)
        for tag, count in sorted(
            concern_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # 4. Spot heatmap — aggregate mentioned_spots (JSON array) in Python
    all_spots = await db.execute(
        select(Evaluation.mentioned_spots).where(
            Evaluation.mentioned_spots.isnot(None)
        )
    )
    spot_counter: Dict[str, int] = {}
    for row in all_spots.all():
        spots = row.mentioned_spots
        if isinstance(spots, list):
            for spot in spots:
                spot_counter[spot] = spot_counter.get(spot, 0) + 1

    spot_heatmap = [
        SpotHeatmapItem(spot_name=spot, mention_count=count)
        for spot, count in sorted(
            spot_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    return DashboardResponse(
        sentiment_trend=sentiment_trend,
        satisfaction_distribution=satisfaction_distribution,
        hot_issues=hot_issues,
        spot_heatmap=spot_heatmap,
    )


async def get_report_data(db: AsyncSession) -> ReportResponse:
    """Aggregate report data from evaluations."""

    # 1. Sentiment stats — same aggregation as dashboard
    sentiment_result = await db.execute(
        select(
            Evaluation.sentiment_label,
            func.count(Evaluation.id).label("cnt"),
        )
        .where(Evaluation.sentiment_label.isnot(None))
        .group_by(Evaluation.sentiment_label)
    )
    sentiment_stats = [
        SentimentPieItem(
            label=(row.sentiment_label or "").capitalize(),
            count=row.cnt,
        )
        for row in sentiment_result.all()
    ]

    # 2. Concern ranking — aggregate concern_tags (JSON array) in Python
    all_concerns = await db.execute(
        select(Evaluation.concern_tags).where(
            Evaluation.concern_tags.isnot(None)
        )
    )
    concern_counter: Dict[str, int] = {}
    for row in all_concerns.all():
        tags = row.concern_tags
        if isinstance(tags, list):
            for tag in tags:
                concern_counter[tag] = concern_counter.get(tag, 0) + 1

    concern_ranking = [
        ConcernRankItem(concern=tag, count=count)
        for tag, count in sorted(
            concern_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # 3. Suggestions — extract from raw_result JSON field
    all_raw = await db.execute(
        select(Evaluation.raw_result).where(
            Evaluation.raw_result.isnot(None)
        )
    )
    suggestions: List[str] = []
    seen: set = set()
    for row in all_raw.all():
        raw = row.raw_result
        if isinstance(raw, dict):
            suggestion_val = raw.get("suggestions")
            if isinstance(suggestion_val, list):
                for s in suggestion_val:
                    if isinstance(s, str) and s not in seen:
                        suggestions.append(s)
                        seen.add(s)
            elif isinstance(suggestion_val, str) and suggestion_val not in seen:
                suggestions.append(suggestion_val)
                seen.add(suggestion_val)

    return ReportResponse(
        sentiment_stats=sentiment_stats,
        concern_ranking=concern_ranking,
        suggestions=suggestions,
    )
