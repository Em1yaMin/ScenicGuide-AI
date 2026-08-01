from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.analysis import AIOverviewResponse, TrendsResponse
from app.services.analytics_service import get_ai_overview, get_trends
from app.tasks.analysis import batch_evaluate

router = APIRouter(prefix="/api/v1/analysis", tags=["AI运营分析"])


@router.post("/run-now")
async def run_now() -> Dict[str, int]:
    """Manually trigger one evaluation cycle.

    Returns count of dialogues evaluated in this batch.
    """
    count = await batch_evaluate()
    return {"evaluated_count": count}


@router.get("/overview", response_model=AIOverviewResponse)
async def analysis_overview(
    days: int = Query(default=30, ge=1, le=365, description="统计天数范围"),
    db: AsyncSession = Depends(get_db),
) -> AIOverviewResponse:
    """获取 AI 运营分析概览。

    使用 LangChain 聚合分析全量对话数据，返回：
    - 热门问题 TOP10
    - 满意度趋势
    - 未回答好的问题
    - 高峰时段分析
    - 运营优化建议
    """
    return await get_ai_overview(db, days=days)


@router.get("/trends", response_model=TrendsResponse)
async def analysis_trends(
    days: int = Query(default=30, ge=1, le=365, description="统计天数范围"),
    db: AsyncSession = Depends(get_db),
) -> TrendsResponse:
    """获取趋势数据。

    返回每日对话量和每日满意度均分的时间序列数据。
    """
    return await get_trends(db, days=days)
