import asyncio
import contextlib
import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.tasks.analysis as analysis_mod
from app.core.config import settings
from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.models.visitor import Visitor
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


# ── AI 运营概览结果缓存（提速：进程内 TTL + 并发防抖） ──


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


@pytest.mark.anyio
async def test_overview_cache_hit_returns_cached(db_session, monkeypatch):
    """同 days 二次调用命中缓存：LLM 只调用一次，命中响应带 cached=True 且 analysis_timestamp 不变。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.cached is False
    assert chain.calls == 1

    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.cached is True
    assert chain.calls == 1  # 缓存命中不触发第二次 LLM 分析
    assert second.analysis_timestamp == first.analysis_timestamp
    assert second.total_dialogues == first.total_dialogues == 1
    assert second.hot_questions == first.hot_questions


@pytest.mark.anyio
async def test_overview_cache_isolated_by_days(db_session, monkeypatch):
    """不同 days 参数互不串缓存：各自首次调用触发分析，随后各自命中。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    r7 = await analytics_service.get_ai_overview(db_session, days=7)
    r30 = await analytics_service.get_ai_overview(db_session, days=30)
    assert chain.calls == 2  # 7 天与 30 天各自分析一次
    assert r7.cached is False and r30.cached is False

    r7b = await analytics_service.get_ai_overview(db_session, days=7)
    r30b = await analytics_service.get_ai_overview(db_session, days=30)
    assert chain.calls == 2  # 各自命中缓存，无新增分析
    assert r7b.cached is True and r30b.cached is True
    assert r7b.analysis_timestamp == r7.analysis_timestamp
    assert r30b.analysis_timestamp == r30.analysis_timestamp


@pytest.mark.anyio
async def test_overview_cache_ttl_expiry_recomputes(db_session, monkeypatch):
    """TTL 过期后重新分析（cached=False），缓存重新写入。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)
    # TTL 设为负数：写入即过期，下次调用必然重新分析
    monkeypatch.setattr(analytics_service, "OVERVIEW_CACHE_TTL_SECONDS", -1)

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.cached is False
    assert chain.calls == 1

    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.cached is False
    assert chain.calls == 2
    assert second.analysis_timestamp != first.analysis_timestamp


@pytest.mark.anyio
async def test_overview_concurrent_requests_share_inflight(db_session, monkeypatch):
    """同键并发请求合并：in-flight 共享同一 LLM 调用，刷新堆积不放大成本。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain(delay=0.3)
    _enable_fake_llm(monkeypatch, chain)

    start = time.monotonic()
    r1, r2, r3 = await asyncio.gather(
        analytics_service.get_ai_overview(db_session, days=30),
        analytics_service.get_ai_overview(db_session, days=30),
        analytics_service.get_ai_overview(db_session, days=30),
    )
    elapsed = time.monotonic() - start

    assert chain.calls == 1  # 三次并发仅触发一次 LLM 分析
    assert r1.analysis_timestamp == r2.analysis_timestamp == r3.analysis_timestamp
    assert elapsed < 0.8  # 远小于 3×0.3s 串行耗时
    # in-flight 清理干净：后续请求不再共享旧任务，而是命中已写入的缓存
    assert analytics_service._ai_overview_inflight == {}
    r4 = await analytics_service.get_ai_overview(db_session, days=30)
    assert r4.cached is True
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_empty_result_not_cached(db_session, monkeypatch):
    """空数据结果不缓存：新对话入库后立即重新分析，不被旧空结果遮蔽。"""
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.total_dialogues == 0
    assert first.cached is False
    assert chain.calls == 0  # 空库早返回，不触发 LLM

    await _seed_overview_data(db_session)
    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.total_dialogues == 1  # 未命中旧空结果
    assert second.cached is False
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_endpoint_second_call_cached(client, db_session, monkeypatch):
    """HTTP 端点同 days 二次调用命中缓存：带 cached 标记且不重复调 LLM。"""
    await _seed_overview_data(db_session, visitor_id="v-cache-http")
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    r1 = await client.get("/api/v1/analysis/overview?days=30")
    r2 = await client.get("/api/v1/analysis/overview?days=30")
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True
    assert r2.json()["total_dialogues"] == 1
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_creator_cancel_keeps_shared_task(db_session, monkeypatch):
    """创建者请求被取消时共享 in-flight 任务不受影响：等待者拿到结果且缓存照常写入。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain(delay=0.3)
    _enable_fake_llm(monkeypatch, chain)

    creator = asyncio.create_task(
        analytics_service.get_ai_overview(db_session, days=30)
    )
    await asyncio.sleep(0.05)
    waiter = asyncio.create_task(
        analytics_service.get_ai_overview(db_session, days=30)
    )
    await asyncio.sleep(0.05)
    creator.cancel()
    with pytest.raises(asyncio.CancelledError):
        await creator

    result = await waiter
    assert result.total_dialogues == 1
    assert chain.calls == 1  # 创建者取消未中断共享分析

    cached = await analytics_service.get_ai_overview(db_session, days=30)
    assert cached.cached is True  # 后台任务完成时已写缓存
    assert chain.calls == 1


@pytest.mark.anyio
async def test_overview_returned_object_isolated_from_cache(db_session, monkeypatch):
    """调用方改动返回对象不影响缓存：命中路径读到的仍是原结果。"""
    await _seed_overview_data(db_session)
    chain = _CountingChain()
    _enable_fake_llm(monkeypatch, chain)

    first = await analytics_service.get_ai_overview(db_session, days=30)
    assert first.hot_questions
    first.hot_questions[0].question = "被调用方改动"

    second = await analytics_service.get_ai_overview(db_session, days=30)
    assert second.cached is True
    assert second.hot_questions[0].question == "门票多少钱"


# ── LLM 降级链测试（DB 统计兜底） ──


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
            raise RuntimeError("simulated local LLM evaluation outage")
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


# ── 本地 LLM 量化评估（阶段4：Dify Workflow 评估本地化） ──


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
