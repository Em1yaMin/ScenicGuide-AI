"""LLM token 记账核心（app/services/usage_tracker.py）单测（批次7 C5）。

覆盖：record 入队/字段归一、链路上下文（usage_context contextvar 传播）、
批量 flush 落库（可注入 session factory）、队列满兜底（调度立即 flush +
丢最旧）、关闭后丢弃、开关关闭零开销、单价表解析与 cost 计算。
"""
import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base
from app.models.usage import LLMUsage
from app.services import usage_tracker as ut
from app.services.usage_tracker import (
    CHAIN_ADMIN_AGENT,
    CHAIN_COMPACTION,
    CHAIN_DIALOGUE,
    CHAIN_OTHER,
    _compute_cost,
    _parse_model_prices,
    usage_context,
    usage_tracker,
)


@pytest.fixture(autouse=True)
def _reset_tracker():
    """每个测试重置 tracker（清队列/统计/关闭态），并恢复默认队列容量。"""
    usage_tracker.reset(maxsize=settings.USAGE_QUEUE_MAX_SIZE)
    yield
    usage_tracker.reset(maxsize=settings.USAGE_QUEUE_MAX_SIZE)


# ── record 入队 ──


def test_record_usage_enqueues_normalized():
    usage_tracker.record_usage(
        model="deepseek-v4-flash",
        input_tokens=10,
        output_tokens=20,
        duration_ms=1234,
    )
    assert usage_tracker.pending_count() == 1
    assert usage_tracker.recorded == 1
    # 未标注链路：默认 CHAIN_OTHER、user 为空
    pending = usage_tracker._queue.get_nowait()
    assert pending["model"] == "deepseek-v4-flash"
    assert pending["input_tokens"] == 10
    assert pending["output_tokens"] == 20
    assert pending["total_tokens"] == 30
    assert pending["duration_ms"] == 1234
    assert pending["chain"] == CHAIN_OTHER
    assert pending["user"] is None
    assert pending["cost"] is None


def test_record_usage_negative_tokens_clamped():
    usage_tracker.record_usage(model="m", input_tokens=-5, output_tokens=3, duration_ms=0)
    pending = usage_tracker._queue.get_nowait()
    assert pending["input_tokens"] == 0
    assert pending["output_tokens"] == 3
    assert pending["total_tokens"] == 3


def test_record_usage_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "USAGE_TRACKING_ENABLED", False)
    usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
    assert usage_tracker.pending_count() == 0
    assert usage_tracker.recorded == 0


def test_record_usage_closed_drops(monkeypatch):
    async def _close():
        await usage_tracker.shutdown()
    asyncio.run(_close())
    usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
    assert usage_tracker.pending_count() == 0


# ── usage_context（链路/用户标注） ──


def test_usage_context_sets_chain_and_user():
    with usage_context(CHAIN_DIALOGUE, "u-1"):
        usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
    pending = usage_tracker._queue.get_nowait()
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-1"


def test_usage_context_resets_after_exit():
    with usage_context(CHAIN_COMPACTION):
        pass
    usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
    assert usage_tracker._queue.get_nowait()["chain"] == CHAIN_OTHER


def test_usage_context_invalid_chain_falls_back():
    with usage_context("未知链路"):
        usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
    assert usage_tracker._queue.get_nowait()["chain"] == CHAIN_OTHER


def test_usage_context_explicit_chain_overrides_context():
    with usage_context(CHAIN_COMPACTION):
        usage_tracker.record_usage(
            model="m", input_tokens=1, output_tokens=1, duration_ms=1,
            chain=CHAIN_ADMIN_AGENT, user="admin",
        )
    pending = usage_tracker._queue.get_nowait()
    assert pending["chain"] == CHAIN_ADMIN_AGENT
    assert pending["user"] == "admin"


def test_usage_context_propagates_to_thread():
    """contextvars 经 asyncio.to_thread 传播（推荐/评估链路依赖此语义）。"""
    async def run():
        with usage_context(CHAIN_DIALOGUE, "u-thread"):
            await asyncio.to_thread(
                usage_tracker.record_usage,
                model="m", input_tokens=1, output_tokens=1, duration_ms=1,
            )
    asyncio.run(run())
    pending = usage_tracker._queue.get_nowait()
    assert pending["chain"] == CHAIN_DIALOGUE
    assert pending["user"] == "u-thread"


# ── 批量 flush 落库 ──


@pytest.mark.asyncio
async def test_flush_now_writes_rows_to_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    usage_tracker.record_usage(model="m1", input_tokens=10, output_tokens=20, duration_ms=5)
    usage_tracker.record_usage(model="m2", input_tokens=1, output_tokens=2, duration_ms=7)

    written = await usage_tracker.flush_now(session_factory=factory)
    assert written == 2
    assert usage_tracker.pending_count() == 0
    assert usage_tracker.flushed == 2

    async with factory() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(LLMUsage))).scalars().all()
    assert len(rows) == 2
    by_model = {r.model: r for r in rows}
    assert by_model["m1"].input_tokens == 10
    assert by_model["m1"].total_tokens == 30
    assert by_model["m2"].duration_ms == 7
    await engine.dispose()


@pytest.mark.asyncio
async def test_flush_now_empty_queue_noop():
    assert await usage_tracker.flush_now() == 0


@pytest.mark.asyncio
async def test_flush_now_failure_drops_without_raise(monkeypatch):
    """写库失败：告警并丢弃本批（防死循环），不抛异常。"""
    monkeypatch.setattr(
        ut, "_session_factory", lambda: (_ for _ in ()).throw(RuntimeError("db locked"))
    )
    usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
    assert await usage_tracker.flush_now() == 0
    assert usage_tracker.failed == 1
    assert usage_tracker.pending_count() == 0


@pytest.mark.asyncio
async def test_flush_loop_periodic_and_shutdown(monkeypatch):
    """flush 循环：定时落库；shutdown 兜底 flush 剩余并关闭记账。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "USAGE_FLUSH_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(ut, "_session_factory", factory)

    loop_task = asyncio.create_task(usage_tracker.flush_loop())
    try:
        # 等待 flush 循环任务真正开始（绑定事件循环）
        for _ in range(50):
            if usage_tracker._loop is not None:
                break
            await asyncio.sleep(0.01)
        assert usage_tracker._loop is not None
        usage_tracker.record_usage(model="m", input_tokens=1, output_tokens=1, duration_ms=1)
        await asyncio.sleep(0.15)
        assert usage_tracker.flushed == 1
    finally:
        loop_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop_task

    # shutdown 兜底：关闭前入队的记录被 flush
    usage_tracker.record_usage(model="m2", input_tokens=2, output_tokens=2, duration_ms=2)
    await usage_tracker.shutdown()
    assert usage_tracker.pending_count() == 0
    assert usage_tracker.flushed == 2
    # 关闭后新记录丢弃
    usage_tracker.record_usage(model="m3", input_tokens=1, output_tokens=1, duration_ms=1)
    assert usage_tracker.pending_count() == 0
    await engine.dispose()


# ── 队列满兜底 ──


@pytest.mark.asyncio
async def test_queue_full_triggers_immediate_flush(monkeypatch):
    """队列满：调度一次立即 flush（run_coroutine_threadsafe），新记录不丢。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    usage_tracker.reset(maxsize=2)
    loop = asyncio.get_running_loop()
    usage_tracker._loop = loop

    usage_tracker.record_usage(model="a", input_tokens=1, output_tokens=1, duration_ms=1)
    usage_tracker.record_usage(model="b", input_tokens=1, output_tokens=1, duration_ms=1)
    # 第三次入队触发满：调度立即 flush
    usage_tracker.record_usage(model="c", input_tokens=1, output_tokens=1, duration_ms=1)
    # 给 run_coroutine_threadsafe 调度的时间
    for _ in range(50):
        if usage_tracker.pending_count() == 0:
            break
        await asyncio.sleep(0.01)
    assert usage_tracker.pending_count() == 0
    assert usage_tracker.recorded == 3
    await engine.dispose()


def test_queue_full_without_loop_drops_oldest(monkeypatch):
    """队列满且无绑定事件循环（纯单元测试环境）：丢弃最旧腾位，新记录保留。"""
    usage_tracker.reset(maxsize=2)
    usage_tracker.record_usage(model="a", input_tokens=1, output_tokens=1, duration_ms=1)
    usage_tracker.record_usage(model="b", input_tokens=1, output_tokens=1, duration_ms=1)
    usage_tracker.record_usage(model="c", input_tokens=1, output_tokens=1, duration_ms=1)
    assert usage_tracker.pending_count() == 2
    assert usage_tracker.dropped == 1
    # 最旧 a 被丢弃，b/c 保留
    assert usage_tracker._queue.get_nowait()["model"] == "b"
    assert usage_tracker._queue.get_nowait()["model"] == "c"


# ── 单价表与 cost ──


def test_parse_model_prices():
    prices = _parse_model_prices("deepseek-v4-flash:1.0,2.0;deepseek-chat:0.5,1.5")
    assert prices == {"deepseek-v4-flash": (1.0, 2.0), "deepseek-chat": (0.5, 1.5)}


def test_parse_model_prices_ignores_invalid():
    assert _parse_model_prices("") == {}
    assert _parse_model_prices("bad;deepseek-v4-flash:1.0,2.0") == {
        "deepseek-v4-flash": (1.0, 2.0)
    }


def test_compute_cost(monkeypatch):
    monkeypatch.setattr(
        settings, "MODEL_PRICES", "deepseek-v4-flash:1.0,2.0"
    )
    # 1M input × 1.0 + 500K output × 2.0 = 2.0 元
    assert _compute_cost("deepseek-v4-flash", 1_000_000, 500_000) == 2.0
    # 未配置单价的模型 → None（订阅制只记 token 不记钱）
    assert _compute_cost("unknown-model", 100, 100) is None
    # 未配置单价表 → None
    monkeypatch.setattr(settings, "MODEL_PRICES", "")
    assert _compute_cost("deepseek-v4-flash", 100, 100) is None


def test_record_usage_computes_cost(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_PRICES", "deepseek-v4-flash:1.0,2.0")
    usage_tracker.record_usage(
        model="deepseek-v4-flash", input_tokens=1_000_000, output_tokens=500_000, duration_ms=1
    )
    pending = usage_tracker._queue.get_nowait()
    assert pending["cost"] == 2.0
