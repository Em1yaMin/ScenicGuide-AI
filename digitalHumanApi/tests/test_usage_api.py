"""LLM token 用量报表 API（app/routers/usage.py + app/services/usage_report.py）测试（批次7 C5）。

覆盖：overview 聚合正确性（趋势序列/按模型/按链路/合计）、时间范围与粒度、
明细查询分页与筛选、CSV 导出、鉴权（401/operator 放行）、参数校验 422。
"""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.usage import LLMUsage


@pytest.fixture(autouse=True)
def _pin_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "admin-secret")


def _mk_usage(model, chain, inp, out, duration, created_at, user=None, cost=None):
    return LLMUsage(
        model=model,
        input_tokens=inp,
        output_tokens=out,
        total_tokens=inp + out,
        duration_ms=duration,
        chain=chain,
        user=user,
        cost=cost,
        created_at=created_at,
    )


@pytest.fixture
async def seeded_client():
    """内存库 + 种子数据（3 模型 × 2 链路 × 3 天）+ 客户端。

    基准时间固定为 2026-08-03（UTC naive，过去时间），overview 测试
    显式传 start/end 覆盖默认 30 天范围；默认范围行为单独测试。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    base = datetime(2026, 8, 3, 12, 0, 0)

    def _cost_a(inp, out):
        return inp / 1e6 * 1.0 + out / 1e6 * 2.0  # 与 MODEL_PRICES 测试单价一致

    async with factory() as session:
        session.add_all(
            [
                # 8/1：对话 ×2（模型 A/B）+ 推荐 ×1
                _mk_usage("model-a", "对话", 100, 50, 1000, base - timedelta(days=2), user="u1", cost=_cost_a(100, 50)),
                _mk_usage("model-b", "对话", 200, 100, 2000, base - timedelta(days=2), user="u2"),
                _mk_usage("model-a", "推荐", 50, 25, 500, base - timedelta(days=2), cost=_cost_a(50, 25)),
                # 8/2：评估 ×2（模型 A）+ 管理员agent ×1（模型 B）
                _mk_usage("model-a", "评估", 300, 150, 3000, base - timedelta(days=1), cost=_cost_a(300, 150)),
                _mk_usage("model-a", "评估", 10, 5, 300, base - timedelta(days=1), cost=_cost_a(10, 5)),
                _mk_usage("model-b", "管理员agent", 400, 200, 4000, base - timedelta(days=1), user="admin"),
                # 8/3：压缩 ×1（模型 A）
                _mk_usage("model-a", "压缩", 500, 250, 5000, base, cost=_cost_a(500, 250)),
            ]
        )
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
    await engine.dispose()


# ── 鉴权 ──


async def _get(client, url, token=None):
    headers = {"X-Admin-Token": token} if token else {}
    return await client.get(url, headers=headers)


@pytest.mark.asyncio
async def test_overview_requires_admin_token(seeded_client):
    resp = await _get(seeded_client, "/api/admin/usage/overview")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_overview_wrong_token_401(seeded_client):
    resp = await _get(seeded_client, "/api/admin/usage/overview", token="wrong")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_overview_operator_allowed(seeded_client, monkeypatch):
    """查询类端点 operator 角色可访问（与知识库/记忆查询一致）。"""
    monkeypatch.setattr(settings, "ADMIN_TOKENS", {"op-token": "operator"})
    resp = await _get(seeded_client, "/api/admin/usage/overview", token="op-token")
    assert resp.status_code == 200


# ── overview 聚合正确性 ──


@pytest.mark.asyncio
async def test_overview_totals_and_groups(seeded_client):
    resp = await _get(seeded_client, "/api/admin/usage/overview?start=2026-08-01&end=2026-08-04", token="admin-secret")
    assert resp.status_code == 200
    data = resp.json()

    # 合计：7 次调用；input=100+200+50+300+10+400+500=1560；output=50+100+25+150+5+200+250=780
    totals = data["totals"]
    assert totals["calls"] == 7
    assert totals["input_tokens"] == 1560
    assert totals["output_tokens"] == 780
    assert totals["total_tokens"] == 2340
    # 平均耗时 = 全部 duration 均值（1000+2000+500+3000+300+4000+5000)/7
    assert totals["avg_duration_ms"] == (1000 + 2000 + 500 + 3000 + 300 + 4000 + 5000) // 7

    # 按模型聚合（total_tokens 降序）：model-a=100+50+300+10+500=960 in + 480 out=1440；
    # model-b=200+400=600 in + 300 out=900
    by_model = {m["name"]: m for m in data["by_model"]}
    assert list(data["by_model"]) == sorted(
        data["by_model"], key=lambda x: x["total_tokens"], reverse=True
    )
    assert by_model["model-a"]["calls"] == 5
    assert by_model["model-a"]["total_tokens"] == 1440
    assert by_model["model-b"]["calls"] == 2
    assert by_model["model-b"]["total_tokens"] == 900

    # 按链路聚合：对话 2 次 / 推荐 1 / 评估 2 / 管理员agent 1 / 压缩 1
    by_chain = {c["name"]: c for c in data["by_chain"]}
    assert by_chain["对话"]["calls"] == 2
    assert by_chain["对话"]["total_tokens"] == 450
    assert by_chain["评估"]["calls"] == 2
    assert by_chain["评估"]["total_tokens"] == 465
    assert by_chain["管理员agent"]["calls"] == 1
    assert by_chain["管理员agent"]["total_tokens"] == 600
    assert by_chain["压缩"]["total_tokens"] == 750

    # 趋势序列（日粒度）：3 个桶，按日期升序
    series = data["series"]
    assert len(series) == 3
    assert [s["bucket"] for s in series] == sorted(s["bucket"] for s in series)
    # 最近一天（压缩 1 次）
    assert series[-1]["calls"] == 1
    assert series[-1]["total_tokens"] == 750


@pytest.mark.asyncio
async def test_overview_granularity_month(seeded_client):
    resp = await _get(
        seeded_client, "/api/admin/usage/overview?granularity=month&start=2026-08-01&end=2026-08-04", token="admin-secret"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["granularity"] == "month"
    assert len(data["series"]) == 1  # 所有种子数据同月
    assert data["series"][0]["calls"] == 7


@pytest.mark.asyncio
async def test_overview_invalid_granularity_422(seeded_client):
    resp = await _get(
        seeded_client, "/api/admin/usage/overview?granularity=hour", token="admin-secret"
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_overview_invalid_date_422(seeded_client):
    resp = await _get(
        seeded_client, "/api/admin/usage/overview?start=not-a-date", token="admin-secret"
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_overview_start_after_end_422(seeded_client):
    resp = await _get(
        seeded_client,
        "/api/admin/usage/overview?start=2026-09-01&end=2026-08-01",
        token="admin-secret",
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_overview_time_range_filter(seeded_client):
    """时间范围筛选：仅最近 1 天（压缩 1 次）。"""
    resp = await _get(
        seeded_client,
        "/api/admin/usage/overview?start=2020-01-01",
        token="admin-secret",
    )
    # start 早于所有数据 → 全量
    assert resp.json()["totals"]["calls"] == 7

    resp = await _get(
        seeded_client,
        "/api/admin/usage/overview?start=2026-08-01&end=2026-08-02",
        token="admin-secret",
    )
    data = resp.json()
    # [8/8 00:00, 8/9 00:00)：对话 2 + 推荐 1 = 3 次（8/9 的评估在边界外）
    assert data["totals"]["calls"] == 3
    assert data["start"] == "2026-08-01T00:00:00"


@pytest.mark.asyncio
async def test_overview_default_range_last_30_days():
    """不传时间参数：默认最近 30 天（真实 now 前 1 天数据可见）。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async with factory() as session:
        session.add_all(
            [
                _mk_usage("model-a", "对话", 10, 5, 100, now - timedelta(days=1)),
                _mk_usage("model-a", "对话", 10, 5, 100, now - timedelta(days=31)),  # 超出 30 天
            ]
        )
        await session.commit()

    async def override_get_db():
        async with factory() as session:
            yield session
            await session.commit()

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/admin/usage/overview", headers={"X-Admin-Token": "admin-secret"}
        )
    app.dependency_overrides.clear()
    await engine.dispose()

    data = resp.json()
    assert data["totals"]["calls"] == 1


@pytest.mark.asyncio
async def test_overview_empty_range(seeded_client):
    resp = await _get(
        seeded_client,
        "/api/admin/usage/overview?start=2000-01-01&end=2000-02-01",
        token="admin-secret",
    )
    data = resp.json()
    assert data["totals"]["calls"] == 0
    assert data["series"] == []
    assert data["by_model"] == []
    assert data["by_chain"] == []


@pytest.mark.asyncio
async def test_overview_cost_aggregation(seeded_client, monkeypatch):
    """配置单价表后：cost 按模型单价聚合（未配置单价的模型 cost 为 None）。

    种子数据中 model-a 各行已按 1.0/2.0（元/百万）预置 cost。
    """
    monkeypatch.setattr(
        settings, "MODEL_PRICES", "model-a:1.0,2.0"
    )
    resp = await _get(seeded_client, "/api/admin/usage/overview?start=2026-08-01&end=2026-08-04", token="admin-secret")
    data = resp.json()
    # model-a：960 input + 480 output → 960/1e6*1 + 480/1e6*2 = 0.00192
    by_model = {m["name"]: m for m in data["by_model"]}
    assert by_model["model-a"]["cost"] == pytest.approx(0.00192)
    assert by_model["model-b"]["cost"] is None
    # 合计：仅汇总有 cost 的行（model-a 5 行）
    assert data["totals"]["cost"] == pytest.approx(0.00192)


# ── 明细查询 ──


@pytest.mark.asyncio
async def test_details_pagination_and_order(seeded_client):
    resp = await _get(
        seeded_client, "/api/admin/usage/details?page=1&page_size=3", token="admin-secret"
    )
    data = resp.json()
    assert data["total"] == 7
    assert data["page"] == 1
    assert len(data["items"]) == 3
    # created_at 倒序：最新（压缩）在前
    assert data["items"][0]["chain"] == "压缩"

    resp = await _get(
        seeded_client, "/api/admin/usage/details?page=3&page_size=3", token="admin-secret"
    )
    data = resp.json()
    assert len(data["items"]) == 1


@pytest.mark.asyncio
async def test_details_filters(seeded_client):
    resp = await _get(
        seeded_client,
        "/api/admin/usage/details?chain=对话&model=model-a",
        token="admin-secret",
    )
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["chain"] == "对话"
    assert data["items"][0]["user"] == "u1"

    resp = await _get(
        seeded_client, "/api/admin/usage/details?user=admin", token="admin-secret"
    )
    assert resp.json()["total"] == 1

    resp = await _get(
        seeded_client, "/api/admin/usage/details?chain=不存在", token="admin-secret"
    )
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_details_requires_auth(seeded_client):
    resp = await _get(seeded_client, "/api/admin/usage/details")
    assert resp.status_code == 401


# ── CSV 导出 ──


@pytest.mark.asyncio
async def test_export_csv_content(seeded_client):
    resp = await _get(
        seeded_client, "/api/admin/usage/export?chain=对话", token="admin-secret"
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers.get("content-disposition", "")
    # 去 BOM 解析
    text = resp.content.decode("utf-8-sig")
    reader = list(csv.reader(io.StringIO(text)))
    assert reader[0] == [
        "id", "created_at", "model", "chain", "user", "input_tokens",
        "output_tokens", "total_tokens", "duration_ms", "cost",
    ]
    # 筛选链路的 2 行（对话）
    assert len(reader) == 3
    assert reader[1][3] == "对话"


@pytest.mark.asyncio
async def test_export_csv_requires_auth(seeded_client):
    resp = await _get(seeded_client, "/api/admin/usage/export")
    assert resp.status_code == 401
