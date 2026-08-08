"""LLM token 用量报表 API（批次7 C5，管理端 X-Admin-Token 鉴权）。

端点（契约见 doc/usage-tracking.md）：

- `GET /api/admin/usage/overview` — 总览：趋势序列（日/周/月粒度）+
  按模型 + 按链路聚合 + 合计（参考 GitHub 用量报表风格：趋势图 + 明细表）
- `GET /api/admin/usage/details` — 明细查询（分页 + 时间/模型/链路/用户筛选）
- `GET /api/admin/usage/export` — CSV 导出（同一筛选，全量行）

鉴权：全部端点要求 X-Admin-Token（verify_admin_token；查询类 operator
角色可访问，与知识库/记忆查询一致）。
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.core.database import get_db
from app.routers.deps import verify_admin_token
from app.schemas.usage import (
    UsageDetailResponse,
    UsageOverviewResponse,
)
from app.services.usage_report import (
    export_csv,
    get_details,
    get_overview,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/usage",
    tags=["LLM用量报表"],
    dependencies=[Depends(verify_admin_token)],
)


@router.get("/overview", response_model=UsageOverviewResponse)
async def usage_overview(
    start: Optional[str] = Query(default=None, description="起始时间（ISO 日期或 datetime，缺省最近 30 天）"),
    end: Optional[str] = Query(default=None, description="结束时间（ISO 日期或 datetime，缺省当前时刻）"),
    granularity: str = Query(default="day", description="趋势粒度：day/week/month"),
    db=Depends(get_db),
) -> UsageOverviewResponse:
    """用量总览（趋势 + 按模型 + 按链路 + 合计）。"""
    return await get_overview(db, start=start, end=end, granularity=granularity)


@router.get("/details", response_model=UsageDetailResponse)
async def usage_details(
    start: Optional[str] = Query(default=None, description="起始时间（ISO）"),
    end: Optional[str] = Query(default=None, description="结束时间（ISO）"),
    model: Optional[str] = Query(default=None, description="按模型筛选"),
    chain: Optional[str] = Query(default=None, description="按链路筛选（对话/推荐/评估/压缩/管理员agent/记忆提取/规划/其他）"),
    user: Optional[str] = Query(default=None, description="按用户筛选"),
    page: int = Query(default=1, ge=1, description="页码（从 1 开始）"),
    page_size: int = Query(default=20, ge=1, le=100, description="每页条数（≤100）"),
    db=Depends(get_db),
) -> UsageDetailResponse:
    """用量明细（分页，created_at 倒序）。"""
    return await get_details(
        db,
        start=start,
        end=end,
        model=model,
        chain=chain,
        user=user,
        page=page,
        page_size=page_size,
    )


@router.get("/export")
async def usage_export(
    start: Optional[str] = Query(default=None, description="起始时间（ISO）"),
    end: Optional[str] = Query(default=None, description="结束时间（ISO）"),
    model: Optional[str] = Query(default=None, description="按模型筛选"),
    chain: Optional[str] = Query(default=None, description="按链路筛选"),
    user: Optional[str] = Query(default=None, description="按用户筛选"),
    db=Depends(get_db),
) -> StreamingResponse:
    """CSV 导出（与明细同筛选，全量行；UTF-8 BOM 兼容 Excel）。"""
    csv_text, filename = await export_csv(
        db, start=start, end=end, model=model, chain=chain, user=user
    )
    # UTF-8 BOM：Excel 直接打开中文不乱码
    body = "\ufeff" + csv_text
    return StreamingResponse(
        iter([body.encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
