import asyncio
import contextlib
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

import app.tasks.analysis as analysis_mod
from app.core.config import settings
from app.models.analysis_report import AnalysisReport
from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.models.visitor import Visitor
from app.schemas.analysis import AIOverviewResponse
from app.services import analytics_service
from app.tasks.analysis import _normalize_string_list, analysis_loop, batch_evaluate


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
    """Overview 端点在无落库记录时返回待生成空结构（后台分析不阻塞当前响应）。"""
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

    # 预热：等价后台分析已完成（落库 + 进程内缓存），端点秒回真实结果
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )

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


# ── AI 运营概览结果落库持久化（2026-08-15：库命中秒回 + 后台静默刷新） ──


class _CountingChain:
    """模拟 LLM 分析链：计数调用次数，可选延迟（用于并发/缓存计时断言）。"""

    def __init__(self, delay: float = 0.0):
        self.calls = 0
        self.delay = delay

    async def ainvoke(self, inputs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
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


async def _seed_overview_data(db_session, visitor_id="v-cache", content="门票多少钱"):
    """写入 1 条对话 + 1 条评估，供 overview 非空分析使用。"""
    now = datetime.now(timezone.utc)
    db_session.add(Visitor(id=visitor_id))
    d = Dialogue(
        visitor_id=visitor_id, content=content, role=DialogueRole.user,
        timestamp=now - timedelta(hours=1),
    )
    db_session.add(d)
    await db_session.flush()
    db_session.add(Evaluation(
        dialogue_id=d.id, sentiment_label="neutral",
        satisfaction_score=3, concern_tags=["票务咨询"],
    ))
    await db_session.commit()


def _enable_fake_llm(monkeypatch, chain):
    """开启 LLM 并注入计数链（复用既有测试的 monkeypatch 模式）。"""
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(analytics_service, "_get_analysis_chain", lambda: chain)


def _enable_real_background_refresh(monkeypatch, db_session):
    """恢复真实后台刷新实现，并注入指向测试 db_session 的 session 工厂。

    conftest autouse 把 _background_refresh patch 为 noop（防写真实文件库）；
    需要真实后台行为（落库/刷新链路）的测试显式覆盖。
    """

    @contextlib.asynccontextmanager
    async def _factory():
        yield db_session

    monkeypatch.setattr(analytics_service, "_db_session_factory", _factory)

    async def _real_background_refresh(key: str, days: int) -> None:
        async with _factory() as session:
            await analytics_service._compute_and_persist_ai_overview(
                session, key, days
            )

    monkeypatch.setattr(
        analytics_service, "_background_refresh", _real_background_refresh
    )


@pytest.mark.anyio
async def test_overview_persists_report_to_db(db_session, monkeypatch):
    """分析完成后落库：analysis_reports 一行（days/响应快照/分析时间戳正确），upsert 不膨胀。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )

    reports = (
        await db_session.execute(select(AnalysisReport))
    ).scalars().all()
    assert len(reports) == 1
    report = reports[0]
    assert report.days == 30
    parsed = AIOverviewResponse.model_validate_json(report.response_json)
    assert parsed.total_dialogues == 1
    assert parsed.hot_questions[0].question == "门票多少钱"
    assert report.analysis_timestamp == datetime.fromisoformat(
        parsed.analysis_timestamp
    ).replace(tzinfo=None)  # SQLite 读回 naive UTC
    assert report.created_at is not None and report.updated_at is not None
    # 进程内缓存同步填充
    assert "overview:30" in analytics_service._ai_overview_cache

    # upsert 语义：再次分析覆盖同一行，不膨胀
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    reports = (
        await db_session.execute(select(AnalysisReport))
    ).scalars().all()
    assert len(reports) == 1
    assert chain.calls == 2


@pytest.mark.anyio
async def test_overview_db_hit_seconds_fast(db_session, monkeypatch):
    """冷启动（等价服务重启）后库中新鲜记录秒回 + 后台静默刷新一次；后续访问命中缓存不再刷新。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    # 模拟上次分析已落库，随后进程内缓存丢失（等价服务重启）
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    analytics_service.reset_ai_overview_cache()

    started = {"n": 0}

    async def _fake_refresh(key, days):
        started["n"] += 1

    monkeypatch.setattr(analytics_service, "_background_refresh", _fake_refresh)

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.cached is True
    assert first.total_dialogues == 1
    assert first.hot_questions[0].question == "门票多少钱"
    assert chain.calls == 1  # 秒回不触发同步 LLM 分析
    # 冷启动触发一次后台刷新（验收：后台自动重新分析并更新库）
    assert analytics_service._ai_overview_inflight.get("overview:30") is not None
    task = analytics_service._ai_overview_inflight["overview:30"]
    await task
    assert started["n"] == 1

    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.cached is True
    assert chain.calls == 1  # 进程内缓存命中，无新分析
    assert started["n"] == 1  # 不重复触发刷新
    assert second.analysis_timestamp == first.analysis_timestamp


@pytest.mark.anyio
async def test_overview_db_isolated_by_days(db_session, monkeypatch):
    """不同 days 参数互不串库记录：各自落库一行，各自秒回。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:7", 7
    )
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    analytics_service.reset_ai_overview_cache()

    r7 = await analytics_service.get_ai_overview(db_session, days=7)
    r30 = await analytics_service.get_ai_overview(db_session, days=30)
    assert r7.cached is True and r30.cached is True
    reports = (
        await db_session.execute(
            select(AnalysisReport).order_by(AnalysisReport.days)
        )
    ).scalars().all()
    assert [r.days for r in reports] == [7, 30]
    assert chain.calls == 2  # 7/30 天各自分析一次，后续全部命中


@pytest.mark.anyio
async def test_overview_db_stale_triggers_background_refresh(db_session, monkeypatch):
    """库记录旧于阈值：秒回旧结果 + 后台刷新；完成后库更新、后续命中新结果。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    analytics_service.reset_ai_overview_cache()
    # 把库记录改旧（超过 TTL 阈值）
    report = await analytics_service._load_latest_report(db_session, 30)
    old_updated = report.updated_at
    report.updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=analytics_service.OVERVIEW_CACHE_TTL_SECONDS + 1
    )
    await db_session.commit()
    old_snapshot = AIOverviewResponse.model_validate_json(report.response_json)

    started = {"n": 0}

    async def _fake_refresh(key, days):
        started["n"] += 1
        await analytics_service._compute_and_persist_ai_overview(
            db_session, key, days
        )

    monkeypatch.setattr(analytics_service, "_background_refresh", _fake_refresh)

    response = await analytics_service.get_ai_overview(db_session, days=30)
    assert response.cached is True  # 秒回旧结果，不阻塞
    assert response.analysis_timestamp == old_snapshot.analysis_timestamp
    # 后台刷新已调度（in-flight 登记）
    assert analytics_service._ai_overview_inflight.get("overview:30") is not None

    # 等待后台刷新完成：库更新 + 进程内缓存填充
    task = analytics_service._ai_overview_inflight["overview:30"]
    await task
    assert started["n"] == 1  # 恰好一次刷新
    report2 = await analytics_service._load_latest_report(db_session, 30)
    # SQLite 往返后 updated_at 为 naive；写入侧 aware —— 归一化后比较
    assert report2.updated_at.replace(tzinfo=None) > old_updated.replace(
        tzinfo=None
    )  # 刷新后落库时间更新
    assert chain.calls == 2

    r2 = await analytics_service.get_ai_overview(db_session, days=30)
    assert r2.cached is True
    assert r2.analysis_timestamp != old_snapshot.analysis_timestamp  # 已是新结果
    assert chain.calls == 2


@pytest.mark.anyio
async def test_overview_empty_refresh_removes_stale_report(db_session, monkeypatch):
    """窗口对话清空后过期报告收敛：后台刷新空结果删除陈旧落库行与进程内缓存条目。

    覆盖「过期后台刷新 × 空数据不落库」组合——报告过期后每次请求秒回旧结果
    （cached=True）并调度刷新；若刷新结果为空时不清理，旧报告将永久陈旧。
    验证删除后后续请求走「无记录 + 窗口无对话 → 真实空态」路径。
    """
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    assert await analytics_service._load_latest_report(db_session, 30) is not None

    # 清空窗口内对话（模拟 30 天窗口无新对话），并把库记录改为过期
    from app.models.dialogue import Dialogue
    from app.models.evaluation import Evaluation

    await db_session.execute(delete(Evaluation))
    await db_session.execute(delete(Dialogue))
    await db_session.commit()
    analytics_service.reset_ai_overview_cache()
    report = await analytics_service._load_latest_report(db_session, 30)
    report.updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=analytics_service.OVERVIEW_CACHE_TTL_SECONDS + 1
    )
    await db_session.commit()

    # 过期报告路径：秒回旧快照 + 调度后台刷新（真实实现，刷新结果为空）
    _enable_real_background_refresh(monkeypatch, db_session)
    response = await analytics_service.get_ai_overview(db_session, days=30)
    assert response.cached is True
    assert response.total_dialogues == 1  # 旧快照（窗口对话已清空）

    task = analytics_service._ai_overview_inflight["overview:30"]
    await task

    # 收敛：陈旧落库行与进程内缓存条目均被清理
    assert await analytics_service._load_latest_report(db_session, 30) is None
    assert "overview:30" not in analytics_service._ai_overview_cache

    # 后续请求走真实空态路径，不再秒回过期数据
    r2 = await analytics_service.get_ai_overview(db_session, days=30)
    assert r2.total_dialogues == 0
    assert r2.cached is False
    assert "暂无对话数据" in r2.ai_summary


@pytest.mark.anyio
async def test_overview_empty_result_not_persisted(db_session, monkeypatch):
    """空数据结果不落库/不缓存：新对话入库后立即重新分析，不被旧空结果遮蔽。"""
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    assert (
        await db_session.execute(select(AnalysisReport))
    ).scalars().all() == []
    assert analytics_service._ai_overview_cache == {}
    assert chain.calls == 0  # 空库早返回，不触发 LLM

    await _seed_overview_data(db_session)
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    reports = (
        await db_session.execute(select(AnalysisReport))
    ).scalars().all()
    assert len(reports) == 1  # 有数据后正常落库
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_concurrent_requests_share_refresh(db_session, monkeypatch):
    """并发请求合并后台刷新：只启动一次分析任务，全部秒回 pending 空结果。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain(delay=0.2)
    _enable_fake_llm(monkeypatch, chain)
    started = {"n": 0}

    async def _slow_refresh(key, days):
        started["n"] += 1
        await analytics_service._compute_and_persist_ai_overview(
            db_session, key, days
        )

    monkeypatch.setattr(analytics_service, "_background_refresh", _slow_refresh)

    r1, r2, r3 = await asyncio.gather(
        analytics_service.get_ai_overview(db_session, days=30),
        analytics_service.get_ai_overview(db_session, days=30),
        analytics_service.get_ai_overview(db_session, days=30),
    )
    assert started["n"] == 1  # 三次并发仅启动一次后台刷新
    assert all(r.total_dialogues == 0 for r in (r1, r2, r3))  # 全部秒回不阻塞
    assert all(r.cached is False for r in (r1, r2, r3))

    # 后台完成后：落库 + 缓存命中，LLM 仅调用一次
    task = analytics_service._ai_overview_inflight["overview:30"]
    await task
    assert chain.calls == 1
    r4 = await analytics_service.get_ai_overview(db_session, days=30)
    assert r4.cached is True
    assert r4.total_dialogues == 1
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_no_db_record_pending_then_refresh(db_session, monkeypatch):
    """库中无记录：秒回待生成空结果 + 后台首次分析；完成后落库、再次访问命中。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain(delay=0.2)
    _enable_fake_llm(monkeypatch, chain)
    _enable_real_background_refresh(monkeypatch, db_session)

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.total_dialogues == 0  # 待生成空结果（不阻塞等待分析）
    assert first.cached is False
    assert chain.calls == 0  # 当前响应未同步等待 LLM

    task = analytics_service._ai_overview_inflight["overview:30"]
    await task
    assert chain.calls == 1
    reports = (
        await db_session.execute(select(AnalysisReport))
    ).scalars().all()
    assert len(reports) == 1  # 后台完成已落库

    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.total_dialogues == 1  # 命中真实结果
    assert second.cached is True
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_endpoint_second_call_cached(client, db_session, monkeypatch):
    """HTTP 端点：后台分析完成后访问，同 days 二次调用命中缓存（cached 标记 + 不重复调 LLM）。"""
    await _seed_overview_data(db_session, visitor_id="v-cache-http")
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    # 预热：等价后台刷新已完成（落库 + 进程内缓存）
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )

    r1 = await client.get("/api/v1/analysis/overview?days=30")
    r2 = await client.get("/api/v1/analysis/overview?days=30")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["cached"] is True
    assert r2.json()["cached"] is True
    assert r2.json()["total_dialogues"] == 1
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_endpoint_pending_then_background_refresh(
    client, db_session, monkeypatch
):
    """HTTP 端点首次访问秒回 pending 空结果（不阻塞），后台完成后再访问命中真实结果。

    后台完成以手动执行持久化分析等价驱动（client 请求与后台任务共用测试 session
    会交错事务；真实后台任务链路由 test_overview_no_db_record_pending_then_refresh 覆盖）。
    """
    await _seed_overview_data(db_session, visitor_id="v-http-pending")
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    r1 = await client.get("/api/v1/analysis/overview?days=30")
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["total_dialogues"] == 0  # 秒回待生成空结果，不阻塞等待分析
    assert body1["cached"] is False
    assert "后台" in body1["ai_summary"]

    # 等价后台刷新完成（落库 + 进程内缓存）
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    assert chain.calls == 1

    r2 = await client.get("/api/v1/analysis/overview?days=30")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["total_dialogues"] == 1  # 后台分析完成已落库
    assert body2["cached"] is True
    assert chain.calls == 1  # 未重复触发 LLM


@pytest.mark.anyio
async def test_overview_returned_object_isolated_from_cache(db_session, monkeypatch):
    """调用方改动返回对象不影响缓存：库命中/缓存命中路径读到的仍是原结果。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    await analytics_service._compute_and_persist_ai_overview(
        db_session, "overview:30", 30
    )
    analytics_service.reset_ai_overview_cache()

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.hot_questions
    first.hot_questions[0].question = "被调用方改动"

    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.cached is True
    assert second.hot_questions[0].question == "门票多少钱"


# ── LLM 降级链测试（DB 统计兜底，直接测 _compute_ai_overview 分析层） ──


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
async def test_overview_llm_failure_falls_back_to_db_stats(db_session, monkeypatch):
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

    overview = await analytics_service._compute_ai_overview(db_session, days=30)
    assert overview.llm_analysis is False
    assert overview.total_dialogues == 2
    # DB 统计兜底：热门问题来自 concern_tags
    assert any(h.question in ("路线指引", "票务咨询") for h in overview.hot_questions)
    # 满意度方向由 DB 趋势计算（day0 均分 2 < day1 均分 4 → 上升）
    assert overview.satisfaction_direction == "上升"
    # 摘要包含 DB 统计信息与兜底标记
    assert "数据库统计" in overview.ai_summary
    assert "数据库统计" in overview.satisfaction_summary
    assert len(overview.suggestions) > 0
    assert overview.peak_hours


@pytest.mark.anyio
async def test_overview_llm_success_marks_llm_analysis(db_session, monkeypatch):
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

    overview = await analytics_service._compute_ai_overview(db_session, days=30)
    assert overview.llm_analysis is True
    assert [h.question for h in overview.hot_questions] == ["门票多少钱"]
    assert overview.suggestions == ["优化退票流程"]
    assert [u.question for u in overview.unanswered_questions] == ["怎么退票"]
    assert "数据库统计" not in overview.ai_summary


@pytest.mark.anyio
async def test_overview_without_key_uses_db_fallback(db_session):
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

    overview = await analytics_service._compute_ai_overview(db_session, days=30)
    assert overview.llm_analysis is False
    assert any(h.question == "路线指引" for h in overview.hot_questions)
    assert "数据库统计" in overview.ai_summary


# ── 本地 LLM 量化评估（阶段4：Dify Workflow 评估本地化） ──


@pytest.mark.anyio
async def test_analysis_loop_survives_batch_failure(monkeypatch):
    """分析定时任务在 batch_evaluate 异常时记录日志并继续下一轮，不中断。"""
    calls = {"n": 0}

    async def flaky_batch() -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated local LLM evaluation outage")
        return 0

    sleeps = {"n": 0}

    async def scripted_sleep(_seconds: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise asyncio.CancelledError()

    monkeypatch.setattr(analysis_mod, "batch_evaluate", flaky_batch)
    monkeypatch.setattr(asyncio, "sleep", scripted_sleep)

    await analysis_loop()

    # 第一轮失败后循环继续执行了后续轮次；CancelledError 被循环捕获后正常退出
    assert calls["n"] == 2


def _make_session_factory(db_session):
    """构造 yield 指定 db_session 的零参 asynccontextmanager 工厂（供 monkeypatch）。"""

    @contextlib.asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


async def _add_dialogue(db_session, visitor_id, content, timestamp):
    from app.models.dialogue import Dialogue, DialogueRole

    d = Dialogue(
        visitor_id=visitor_id,
        content=content,
        role=DialogueRole.user,
        timestamp=timestamp,
    )
    db_session.add(d)
    await db_session.flush()
    return d


@pytest.mark.anyio
async def test_batch_evaluate_local_llm_writes_evaluations(
    db_session, monkeypatch
):
    """批量评估：本地 LLM 评估结果写入 Evaluation 表（管理后台消费不变）。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    visitor_id = "v-eval-local"
    db_session.add(Visitor(id=visitor_id))
    await db_session.flush()
    d1 = await _add_dialogue(db_session, visitor_id, "怎么去日光岩", now - timedelta(hours=2))
    d2 = await _add_dialogue(db_session, visitor_id, "门票多少钱？", now - timedelta(hours=1))
    await db_session.flush()

    parsed = {
        "sentiment_label": "neutral",
        "satisfaction_score": 4,
        "concern_tags": ["路线规划"],
        "mentioned_spots": ["日光岩"],
    }

    async def _fake_evaluate(content, llm=None):
        return dict(parsed)

    monkeypatch.setattr(
        "app.tasks.analysis.evaluation_service.evaluate_content",
        _fake_evaluate,
    )
    monkeypatch.setattr(
        "app.tasks.analysis.async_session_factory",
        _make_session_factory(db_session),
    )

    count = await batch_evaluate()
    assert count == 2

    rows = (
        await db_session.execute(
            select(Evaluation).order_by(Evaluation.dialogue_id)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert rows[0].dialogue_id == d1.id
    assert rows[0].sentiment_label == "neutral"
    assert rows[0].concern_tags == ["路线规划"]
    assert rows[1].dialogue_id == d2.id

    # 幂等：二次运行不再重复评估
    count2 = await batch_evaluate()
    assert count2 == 0


@pytest.mark.anyio
async def test_batch_evaluate_partial_failure_silent(
    db_session, monkeypatch
):
    """部分对话评估失败（None/异常）静默跳过，其余正常写入，批次不中断。"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    visitor_id = "v-eval-partial"
    db_session.add(Visitor(id=visitor_id))
    await db_session.flush()
    d_ok = await _add_dialogue(db_session, visitor_id, "介绍一下菽庄花园", now - timedelta(hours=2))
    d_fail = await _add_dialogue(db_session, visitor_id, "你们服务太差了！", now - timedelta(hours=1))
    d_bomb = await _add_dialogue(db_session, visitor_id, "投诉！", now - timedelta(minutes=30))
    await db_session.flush()

    async def _flaky_evaluate(content, llm=None):
        if content == "你们服务太差了！":
            return None  # 静默降级（LLM 未配置/解析失败等）
        if content == "投诉！":
            raise RuntimeError("LLM down")  # 异常也不应打断批次
        return {
            "sentiment_label": "positive",
            "satisfaction_score": 5,
            "concern_tags": ["景点介绍"],
            "mentioned_spots": ["菽庄花园"],
        }

    monkeypatch.setattr(
        "app.tasks.analysis.evaluation_service.evaluate_content",
        _flaky_evaluate,
    )
    monkeypatch.setattr(
        "app.tasks.analysis.async_session_factory",
        _make_session_factory(db_session),
    )

    count = await batch_evaluate()
    assert count == 1  # 仅成功的那条写入

    rows = (await db_session.execute(select(Evaluation))).scalars().all()
    assert len(rows) == 1
    assert rows[0].dialogue_id == d_ok.id
    assert rows[0].sentiment_label == "positive"
