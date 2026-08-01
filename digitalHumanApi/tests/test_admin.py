import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation
from app.models.visitor import Visitor
from app.services.admin_service import get_dashboard_stats, get_report_data


@pytest.mark.asyncio
async def test_report_data_excludes_non_canonical_sentiment(
    db_session: AsyncSession,
):
    visitor = Visitor(id="test-visitor")
    db_session.add(visitor)
    await db_session.flush()

    d1 = Dialogue(visitor_id="test-visitor", content="x")
    d2 = Dialogue(visitor_id="test-visitor", content="y")
    db_session.add_all([d1, d2])
    await db_session.flush()

    db_session.add_all([
        Evaluation(dialogue_id=d1.id, sentiment_label="mixed"),
        Evaluation(dialogue_id=d2.id, sentiment_label="positive"),
    ])
    await db_session.commit()

    result = await get_report_data(db_session)
    labels = {item.label for item in result.sentiment_stats}
    assert "Mixed" not in labels
    assert "Positive" in labels


@pytest.mark.asyncio
async def test_dashboard_total_conversations_counts_all_dialogues(
    db_session: AsyncSession,
):
    visitor = Visitor(id="test-visitor")
    db_session.add(visitor)
    await db_session.flush()

    old_ts = datetime.now(timezone.utc) - timedelta(days=30)
    db_session.add_all([
        Dialogue(visitor_id="test-visitor", content="a", timestamp=old_ts),
        Dialogue(visitor_id="test-visitor", content="b"),
        Dialogue(visitor_id="test-visitor", content="c"),
    ])
    await db_session.commit()

    result = await get_dashboard_stats(db_session)
    assert result.total_conversations == 3


@pytest.mark.asyncio
async def test_dashboard_sentiment_trend_covers_full_range_with_backfill(
    db_session: AsyncSession,
):
    visitor = Visitor(id="test-visitor")
    db_session.add(visitor)
    await db_session.flush()

    old_ts = datetime.now(timezone.utc) - timedelta(days=30)
    d_old = Dialogue(visitor_id="test-visitor", content="old", timestamp=old_ts)
    d_new = Dialogue(visitor_id="test-visitor", content="new")
    db_session.add_all([d_old, d_new])
    await db_session.flush()

    db_session.add_all([
        Evaluation(dialogue_id=d_old.id, sentiment_label="positive"),
        Evaluation(dialogue_id=d_new.id, sentiment_label="neutral"),
    ])
    await db_session.commit()

    result = await get_dashboard_stats(db_session)
    trend = result.sentiment_trend

    # 全量日期序列：从最早对话日期到今天，逐日连续
    assert trend[0].date == old_ts.date().isoformat()
    assert trend[-1].date == datetime.now(timezone.utc).date().isoformat()
    assert len(trend) == (datetime.now(timezone.utc).date() - old_ts.date()).days + 1
    # 超过 7 天窗口的旧数据也计入
    assert trend[0].positive == 1
    # 中间无数据日期回填零值
    assert all(
        item.positive == 0 and item.negative == 0 and item.neutral == 0
        for item in trend[1:-1]
    )
    assert trend[-1].neutral == 1
    # 日期序列严格连续（无缺口）
    dates = [item.date for item in trend]
    assert dates == sorted(dates)
    assert len(set(dates)) == len(dates)
