from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.admin import DashboardResponse, ReportResponse
from app.services.admin_service import get_dashboard_stats, get_report_data

router = APIRouter(prefix="/api/v1/admin", tags=["管理后台API"])


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    db: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    return await get_dashboard_stats(db)


@router.get("/report", response_model=ReportResponse)
async def report(
    db: AsyncSession = Depends(get_db),
) -> ReportResponse:
    return await get_report_data(db)


@router.get("/spot-analysis")
async def spot_analysis() -> dict:
    return {"message": "spot analysis placeholder"}
