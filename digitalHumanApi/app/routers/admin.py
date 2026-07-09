from typing import Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/admin", tags=["管理后台API"])


@router.get("/dashboard")
async def dashboard() -> Dict[str, str]:
    return {"message": "dashboard placeholder"}


@router.get("/report")
async def report() -> Dict[str, str]:
    return {"message": "report placeholder"}


@router.get("/spot-analysis")
async def spot_analysis() -> Dict[str, str]:
    return {"message": "spot analysis placeholder"}
