import json
from datetime import datetime, timedelta, timezone

import pytest

from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.models.visitor import Visitor
from app.tasks.analysis import _normalize_string_list


def test_normalize_string_list_strips_and_dedupes():
    assert _normalize_string_list([" 故宫 ", "故宫", 123, ""]) == ["故宫"]


def test_normalize_string_list_returns_none_for_non_list():
    assert _normalize_string_list("not a list") is None


def test_normalize_string_list_returns_none_for_all_empty():
    assert _normalize_string_list(["", "  "]) is None


# ── JSON 解析测试 ──

from app.services.analytics_service import _extract_json


def test_extract_json_direct():
    data = {"key": "value", "num": 42}
    assert _extract_json(json.dumps(data)) == data


def test_extract_json_from_code_block():
    text = '```json\n{"key": "value"}\n```'
    assert _extract_json(text) == {"key": "value"}


def test_extract_json_from_plain_block():
    text = '```\n{"key": "value"}\n```'
    assert _extract_json(text) == {"key": "value"}


def test_extract_json_nested_in_text():
    text = 'Here is my analysis: {"hot_questions": [{"question": "票价", "count": 10}]} End.'
    assert _extract_json(text) == {"hot_questions": [{"question": "票价", "count": 10}]}


def test_extract_json_invalid_returns_none():
    assert _extract_json("not json at all") is None


# ── API 端点测试 ──


@pytest.mark.anyio
async def test_analysis_overview_empty(client):
    """Overview 端点在无数据时返回空结构。"""
    response = await client.get("/api/v1/analysis/overview?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["total_dialogues"] == 0
    assert body["hot_questions"] == []
    assert body["suggestions"] == []
    assert body["satisfaction_direction"] == "平稳"


@pytest.mark.anyio
async def test_analysis_overview_with_data(client, db_session):
    """Overview 端点在有时返回聚合结果。"""
    now = datetime.now(timezone.utc)
    visitor_id = "v-test-1"

    # 创建 Visitor 记录（Dialogue 有外键约束）
    db_session.add(Visitor(id=visitor_id))
    await db_session.flush()

    # 创建对话
    d1 = Dialogue(
        visitor_id=visitor_id,
        content="请问故宫怎么走？",
        role=DialogueRole.user,
        timestamp=now - timedelta(hours=2),
    )
    d2 = Dialogue(
        visitor_id=visitor_id,
        content="故宫在东侧，步行约10分钟",
        role=DialogueRole.assistant,
        timestamp=now - timedelta(hours=2, minutes=1),
    )
    d3 = Dialogue(
        visitor_id=visitor_id,
        content="门票多少钱？",
        role=DialogueRole.user,
        timestamp=now - timedelta(hours=1),
    )
    db_session.add_all([d1, d2, d3])
    await db_session.flush()

    # 创建评估
    e1 = Evaluation(
        dialogue_id=d1.id,
        sentiment_label="neutral",
        satisfaction_score=3,
        concern_tags=["路线指引"],
        mentioned_spots=["故宫"],
    )
    e2 = Evaluation(
        dialogue_id=d3.id,
        sentiment_label="negative",
        satisfaction_score=2,
        concern_tags=["票务咨询"],
    )
    db_session.add_all([e1, e2])
    await db_session.commit()

    response = await client.get("/api/v1/analysis/overview?days=30")
    assert response.status_code == 200
    body = response.json()
    # 应有对话数和基本结构
    assert body["total_dialogues"] == 3
    assert isinstance(body["hot_questions"], list)
    assert isinstance(body["peak_hours"], list)
    assert isinstance(body["satisfaction_trend"], list)


@pytest.mark.anyio
async def test_analysis_trends_empty(client):
    """Trends 端点在无数据时返回空列表。"""
    response = await client.get("/api/v1/analysis/trends?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["daily_conversations"] == []
    assert body["daily_satisfaction"] == []


@pytest.mark.anyio
async def test_analysis_trends_with_data(client, db_session):
    """Trends 端点返回正确的每日统计。"""
    now = datetime.now(timezone.utc)
    visitor_id = "v-trend-1"

    # 创建 Visitor 记录（Dialogue 有外键约束）
    db_session.add(Visitor(id=visitor_id))
    await db_session.flush()

    dialogues = []
    for i in range(3):
        d = Dialogue(
            visitor_id=visitor_id,
            content=f"问题{i}",
            role=DialogueRole.user,
            timestamp=now - timedelta(days=i),
        )
        dialogues.append(d)
    db_session.add_all(dialogues)
    await db_session.commit()

    response = await client.get("/api/v1/analysis/trends?days=30")
    assert response.status_code == 200
    body = response.json()
    assert len(body["daily_conversations"]) >= 1
    total = sum(d["count"] for d in body["daily_conversations"])
    assert total == 3


@pytest.mark.anyio
async def test_analysis_overview_days_param(client):
    """days 参数正确限制查询范围。"""
    response = await client.get("/api/v1/analysis/overview?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["total_dialogues"] == 0  # 空数据但不会报错
