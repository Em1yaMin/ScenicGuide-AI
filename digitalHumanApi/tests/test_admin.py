import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation
from app.models.visitor import Visitor
from app.services.admin_service import get_report_data


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
