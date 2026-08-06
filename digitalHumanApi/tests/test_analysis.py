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


# ── LLM 降级链测试（DB 统计兜底） ──

import asyncio

import app.tasks.analysis as analysis_mod
from app.core.config import settings
from app.services import analytics_service
from app.tasks.analysis import analysis_loop


class _FakeChain:
    """模拟 LLM 分析链（返回合法 JSON）。"""

    async def ainvoke(self, inputs):
        return json.dumps({
            "hot_questions": [
                {"question": "门票多少钱", "count": 3, "category": "票务"},
            ],
            "satisfaction_summary": "整体满意度平稳，票务咨询为主",
            "satisfaction_direction": "平稳",
            "unanswered_questions": [
                {"question": "怎么退票", "reason": "游客反复追问"},
            ],
            "peak_hours": [{"hour": 10, "count": 5, "label": "10:00-10:59"}],
            "suggestions": ["优化退票流程"],
        })


def _raise_llm_failure():
    raise RuntimeError("simulated LLM outage")


@pytest.mark.anyio
async def test_overview_llm_failure_falls_back_to_db_stats(client, db_session, monkeypatch):
    """LLM 分析异常时回退 DB 统计：不失败、输出确定性摘要、降级标记 llm_analysis=False。"""
    now = datetime.now(timezone.utc)
    visitor_id = "v-llm-fail"
    db_session.add(Visitor(id=visitor_id))
    d0 = Dialogue(
        visitor_id=visitor_id, content="第一天问题1", role=DialogueRole.user,
        timestamp=now - timedelta(days=2, hours=1),
    )
    d1 = Dialogue(
        visitor_id=visitor_id, content="第二天问题1", role=DialogueRole.user,
        timestamp=now - timedelta(days=1, hours=1),
    )
    db_session.add_all([d0, d1])
    await db_session.flush()
    db_session.add(Evaluation(
        dialogue_id=d0.id, sentiment_label="neutral",
        satisfaction_score=2, concern_tags=["路线指引"],
    ))
    db_session.add(Evaluation(
        dialogue_id=d1.id, sentiment_label="positive",
        satisfaction_score=4, concern_tags=["票务咨询"],
    ))
    await db_session.commit()

    # LLM 已配置但调用异常 → 应捕获并回退
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(analytics_service, "_get_analysis_chain", _raise_llm_failure)

    response = await client.get("/api/v1/analysis/overview?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_analysis"] is False
    assert body["total_dialogues"] == 2
    # DB 统计兜底：热门问题来自 concern_tags
    assert any(h["question"] in ("路线指引", "票务咨询") for h in body["hot_questions"])
    # 满意度方向由 DB 趋势计算（day0 均分 2 < day1 均分 4 → 上升）
    assert body["satisfaction_direction"] == "上升"
    # 摘要包含 DB 统计信息与兜底标记
    assert "数据库统计" in body["ai_summary"]
    assert "数据库统计" in body["satisfaction_summary"]
    assert len(body["suggestions"]) > 0
    assert body["peak_hours"]


@pytest.mark.anyio
async def test_overview_llm_success_marks_llm_analysis(client, db_session, monkeypatch):
    """LLM 分析成功时 llm_analysis=True，字段来自 LLM 输出。"""
    now = datetime.now(timezone.utc)
    visitor_id = "v-llm-ok"
    db_session.add(Visitor(id=visitor_id))
    d = Dialogue(
        visitor_id=visitor_id, content="门票多少钱", role=DialogueRole.user,
        timestamp=now - timedelta(hours=1),
    )
    db_session.add(d)
    await db_session.flush()
    db_session.add(Evaluation(
        dialogue_id=d.id, sentiment_label="neutral",
        satisfaction_score=3, concern_tags=["票务咨询"],
    ))
    await db_session.commit()

    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        analytics_service, "_get_analysis_chain", lambda: _FakeChain()
    )

    response = await client.get("/api/v1/analysis/overview?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_analysis"] is True
    assert body["hot_questions"] == [
        {"question": "门票多少钱", "count": 3, "category": "票务"}
    ]
    assert body["suggestions"] == ["优化退票流程"]
    assert body["unanswered_questions"] == [
        {"question": "怎么退票", "reason": "游客反复追问"}
    ]
    assert "数据库统计" not in body["ai_summary"]


@pytest.mark.anyio
async def test_overview_without_key_uses_db_fallback(client, db_session):
    """未配置 OPENAI_API_KEY 时跳过 LLM，直接 DB 统计兜底（llm_analysis=False）。"""
    now = datetime.now(timezone.utc)
    visitor_id = "v-nokey"
    db_session.add(Visitor(id=visitor_id))
    d = Dialogue(
        visitor_id=visitor_id, content="路线怎么走", role=DialogueRole.user,
        timestamp=now - timedelta(hours=1),
    )
    db_session.add(d)
    await db_session.flush()
    db_session.add(Evaluation(
        dialogue_id=d.id, sentiment_label="neutral",
        satisfaction_score=3, concern_tags=["路线指引"],
    ))
    await db_session.commit()

    # conftest 已固定 settings.OPENAI_API_KEY 为空；此处再显式确保
    assert not settings.OPENAI_API_KEY

    response = await client.get("/api/v1/analysis/overview?days=30")
    assert response.status_code == 200
    body = response.json()
    assert body["llm_analysis"] is False
    assert any(h["question"] == "路线指引" for h in body["hot_questions"])
    assert "数据库统计" in body["ai_summary"]


@pytest.mark.anyio
async def test_analysis_loop_survives_batch_failure(monkeypatch):
    """分析定时任务在 batch_evaluate 异常时记录日志并继续下一轮，不中断。"""
    calls = {"n": 0}

    async def flaky_batch() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated Dify workflow outage")
        return 0

    original_sleep = asyncio.sleep

    async def short_sleep(_seconds: float) -> None:
        # 用原始 sleep 避免 patch 后的递归
        await original_sleep(0.01)

    monkeypatch.setattr(analysis_mod, "batch_evaluate", flaky_batch)
    monkeypatch.setattr(asyncio, "sleep", short_sleep)

    task = asyncio.create_task(analysis_loop())
    # 测试自身的等待也用原始 sleep（asyncio.sleep 已被 patch 成 0.01s）
    await original_sleep(0.1)
    task.cancel()
    # analysis_loop 在 except asyncio.CancelledError 中记录日志后正常退出（不 re-raise）
    await task

    # 第一轮失败后循环仍继续执行了后续轮次
    assert calls["n"] >= 2
