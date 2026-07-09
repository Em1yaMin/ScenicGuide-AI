from typing import Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/analysis", tags=["量化分析"])


@router.post("/batch")
async def batch_analysis() -> Dict[str, str]:
    return {"message": "batch analysis placeholder"}
