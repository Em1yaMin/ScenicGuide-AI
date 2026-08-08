"""LLM token 用量报表聚合（批次7 C5，管理端只读查询）。

聚合维度（参考 GitHub 用量报表风格：趋势图 + 明细表）：

- 趋势序列：按 日/周/月 粒度分桶（series，供趋势图）
- 按模型聚合（by_model）与按链路聚合（by_chain）：tokens/次数/平均耗时
- 明细查询：分页 + 筛选（时间范围/模型/链路/用户），CSV 导出同筛选

时间语义：llm_usage.created_at 存 UTC（naive 字符串）；查询参数接受 ISO
日期（2026-08-01）或 ISO datetime（2026-08-01T00:00:00[±时区]），统一转
UTC naive 后与库内字符串比较。
"""
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage import LLMUsage

logger = logging.getLogger(__name__)

# 粒度 → 桶表达式（created_at 为 "YYYY-MM-DD HH:MM:SS..."）
# week 粒度在 Python 侧计算 ISO 周标签（SQLite strftime 不支持 %G/%V）
_BUCKET_EXPR = {
    "day": func.strftime("%Y-%m-%d", LLMUsage.created_at),
    "week": LLMUsage.created_at,
    "month": func.strftime("%Y-%m", LLMUsage.created_at),
}


def _iso_week_label(dt: datetime) -> str:
    """ISO 8601 周标签（YYYY-Www，如 2026-W33）。

    与 SQLite strftime 的 %Y-W%W 不同：%W 以首个周一为第 1 周，年初产出
    W00、跨年时 12 月末落入次年 W01 桶，标签与 ISO 周不对齐；isocalendar
    按 ISO 8601 计算（周一为一周首日、含首个周四的周为第 1 周）。
    """
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

_DEFAULT_RANGE_DAYS = 30
_PAGE_SIZE_MAX = 100
_PAGE_SIZE_DEFAULT = 20


def _parse_iso_datetime(value: str, field: str) -> datetime:
    """解析 ISO 日期/时间参数（"2026-08-01" 或 "2026-08-01T00:00:00[±tz]"）。

    统一转 UTC naive（库内存储格式）；解析失败抛 422（与 Pydantic 校验风格一致）。
    """
    raw = value.strip()
    if not raw:
        raise HTTPException(status_code=422, detail=f"{field} 不能为空")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"{field} 格式非法（期望 ISO 日期或 datetime）: {value}",
        )
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _resolve_range(
    start: Optional[str], end: Optional[str]
) -> Tuple[datetime, datetime]:
    """解析时间范围；缺省取最近 30 天（end=now）。"""
    end_dt = _parse_iso_datetime(end, "end") if end else datetime.now(timezone.utc).replace(tzinfo=None)
    if start:
        start_dt = _parse_iso_datetime(start, "start")
    else:
        start_dt = end_dt - timedelta(days=_DEFAULT_RANGE_DAYS)
    if start_dt > end_dt:
        raise HTTPException(status_code=422, detail="start 不能晚于 end")
    return start_dt, end_dt


def _range_filters(
    start_dt: datetime, end_dt: datetime
) -> List[Any]:
    return [LLMUsage.created_at >= start_dt, LLMUsage.created_at < end_dt]


async def _count(db: AsyncSession, stmt: Select) -> int:
    return (
        await db.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()


async def get_overview(
    db: AsyncSession,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    granularity: str = "day",
) -> Dict[str, Any]:
    """用量总览：趋势序列 + 按模型 + 按链路聚合 + 合计（单次请求一次查询）。

    Returns:
        {"start", "end", "granularity", "totals", "series", "by_model", "by_chain"}
    """
    if granularity not in _BUCKET_EXPR:
        raise HTTPException(
            status_code=422, detail="granularity 仅支持 day/week/month"
        )
    start_dt, end_dt = _resolve_range(start, end)
    base_filters = _range_filters(start_dt, end_dt)
    bucket = _BUCKET_EXPR[granularity]

    # 一次查询取全部分桶明细，内存聚合出 totals/series/by_model/by_chain
    # （llm_usage 行数可控，量级远小于报表口径的聚合复杂度收益）
    rows = (
        await db.execute(
            select(
                bucket.label("bucket"),
                LLMUsage.model,
                LLMUsage.chain,
                LLMUsage.input_tokens,
                LLMUsage.output_tokens,
                LLMUsage.duration_ms,
                LLMUsage.cost,
            )
            .where(*base_filters)
            .order_by(bucket)
        )
    ).all()

    series: Dict[str, Dict[str, Any]] = {}
    models: Dict[str, Dict[str, Any]] = {}
    chains: Dict[str, Dict[str, Any]] = {}
    totals: Dict[str, Any] = {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "total_tokens": 0, "duration_sum": 0, "cost": None,
    }

    def _add(group: Dict[str, Dict[str, Any]], key: str, tokens: Dict[str, int], duration_ms: int, cost: Optional[float]) -> None:
        g = group.setdefault(
            key,
            {"calls": 0, "input_tokens": 0, "output_tokens": 0,
             "total_tokens": 0, "duration_sum": 0, "cost_sum": 0.0, "has_cost": False},
        )
        g["calls"] += 1
        g["input_tokens"] += tokens["input"]
        g["output_tokens"] += tokens["output"]
        g["total_tokens"] += tokens["input"] + tokens["output"]
        g["duration_sum"] += duration_ms
        if cost is not None:
            g["cost_sum"] += cost
            g["has_cost"] = True

    for row in rows:
        tokens = {"input": row.input_tokens or 0, "output": row.output_tokens or 0}
        duration = row.duration_ms or 0
        cost = row.cost
        bucket_key = (
            _iso_week_label(row.bucket) if granularity == "week" else row.bucket
        )
        _add(series, bucket_key, tokens, duration, cost)
        _add(models, row.model, tokens, duration, cost)
        _add(chains, row.chain, tokens, duration, cost)
        totals["calls"] += 1
        totals["input_tokens"] += tokens["input"]
        totals["output_tokens"] += tokens["output"]
        totals["total_tokens"] += tokens["input"] + tokens["output"]
        totals["duration_sum"] += duration
        if cost is not None:
            totals["cost"] = (totals["cost"] or 0.0) + cost

    def _to_trend(key: str, g: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "bucket": key,
            "calls": g["calls"],
            "input_tokens": g["input_tokens"],
            "output_tokens": g["output_tokens"],
            "total_tokens": g["total_tokens"],
            "avg_duration_ms": g["duration_sum"] // g["calls"] if g["calls"] else 0,
            "cost": round(g["cost_sum"], 6) if g["has_cost"] else None,
        }

    def _to_group(name: str, g: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": name,
            "calls": g["calls"],
            "input_tokens": g["input_tokens"],
            "output_tokens": g["output_tokens"],
            "total_tokens": g["total_tokens"],
            "avg_duration_ms": g["duration_sum"] // g["calls"] if g["calls"] else 0,
            "cost": round(g["cost_sum"], 6) if g["has_cost"] else None,
        }

    return {
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "granularity": granularity,
        "totals": {
            "calls": totals["calls"],
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "total_tokens": totals["total_tokens"],
            "avg_duration_ms": totals["duration_sum"] // totals["calls"] if totals["calls"] else 0,
            "cost": round(totals["cost"], 6) if totals["cost"] is not None else None,
        },
        "series": [_to_trend(k, series[k]) for k in sorted(series)],
        "by_model": sorted(
            (_to_group(k, g) for k, g in models.items()),
            key=lambda x: x["total_tokens"],
            reverse=True,
        ),
        "by_chain": sorted(
            (_to_group(k, g) for k, g in chains.items()),
            key=lambda x: x["total_tokens"],
            reverse=True,
        ),
    }


async def _details_stmt(
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    model: Optional[str] = None,
    chain: Optional[str] = None,
    user: Optional[str] = None,
) -> Select:
    stmt = select(LLMUsage)
    if start or end:
        start_dt, end_dt = _resolve_range(start, end)
        stmt = stmt.where(*_range_filters(start_dt, end_dt))
    if model:
        stmt = stmt.where(LLMUsage.model == model.strip())
    if chain:
        stmt = stmt.where(LLMUsage.chain == chain.strip())
    if user:
        stmt = stmt.where(LLMUsage.user == user.strip())
    return stmt


async def get_details(
    db: AsyncSession,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    model: Optional[str] = None,
    chain: Optional[str] = None,
    user: Optional[str] = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
) -> Dict[str, Any]:
    """明细查询（分页，created_at 倒序）。"""
    page = max(page, 1)
    page_size = max(1, min(page_size, _PAGE_SIZE_MAX))
    stmt = await _details_stmt(start=start, end=end, model=model, chain=chain, user=user)
    total = await _count(db, stmt)
    rows = (
        await db.execute(
            stmt.order_by(LLMUsage.created_at.desc(), LLMUsage.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": list(rows),
    }


async def export_csv(
    db: AsyncSession,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
    model: Optional[str] = None,
    chain: Optional[str] = None,
    user: Optional[str] = None,
) -> Tuple[str, str]:
    """CSV 导出（筛选条件下的全量行，不分页）。

    Returns:
        (csv 文本, 建议文件名)
    """
    stmt = await _details_stmt(start=start, end=end, model=model, chain=chain, user=user)
    rows = (
        await db.execute(stmt.order_by(LLMUsage.created_at.desc(), LLMUsage.id.desc()))
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "created_at", "model", "chain", "user", "input_tokens",
         "output_tokens", "total_tokens", "duration_ms", "cost"]
    )
    for r in rows:
        writer.writerow(
            [r.id, r.created_at.isoformat(), r.model, r.chain, r.user or "",
             r.input_tokens, r.output_tokens, r.total_tokens, r.duration_ms,
             "" if r.cost is None else r.cost]
        )
    filename = f"llm_usage_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.csv"
    return buf.getvalue(), filename
