"""后台任务：定期聚合 Dashboard 数据并调用 Dify Workflow 刷新推荐问句缓存。"""
import asyncio
import logging
from typing import List

from app.services.recommend_service import fetch_recommended_questions

logger = logging.getLogger(__name__)

RECOMMEND_INTERVAL_SECONDS = 300

# Module-level cache — safe under asyncio single-thread (no preemption).
# If migrating to multi-worker or multi-thread, wrap with asyncio.Lock.
_recommend_cache: List[str] = []


def get_cached_questions() -> List[str]:
    """Return current cached recommended questions.

    Synchronous getter for API endpoints. Returns empty list before the first
    successful refresh cycle.
    """
    return _recommend_cache


async def refresh_cycle() -> int:
    """One refresh cycle. Returns number of questions cached, or 0 on failure.

    Implements stale-while-revalidate: only overwrites cache on success.
    """
    global _recommend_cache
    questions = await fetch_recommended_questions()
    if questions is not None:
        _recommend_cache = questions
        logger.info(
            "Refreshed %d recommended questions", len(questions)
        )
        return len(questions)
    logger.warning(
        "Refresh cycle — workflow returned None, keeping %d cached questions",
        len(_recommend_cache),
    )
    return 0


async def recommend_loop() -> None:
    """Background loop: refresh recommended questions every N seconds.

    Follows analysis_loop pattern (app/tasks/analysis.py:160-179):
    while True → try/except → asyncio.sleep → CancelledError.
    """
    logger.info(
        "Recommend task started (interval: %ds)",
        RECOMMEND_INTERVAL_SECONDS,
    )
    try:
        while True:
            try:
                await refresh_cycle()
            except Exception:
                logger.exception(
                    "Recommend refresh cycle failed, will retry in %ds",
                    RECOMMEND_INTERVAL_SECONDS,
                )
            await asyncio.sleep(RECOMMEND_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        logger.info("Recommend task cancelled, shutting down")
