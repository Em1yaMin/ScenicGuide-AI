from typing import Dict

from fastapi import APIRouter

from app.tasks.analysis import batch_evaluate

router = APIRouter(prefix="/api/v1/analysis", tags=["量化分析"])


@router.post("/run-now")
async def run_now() -> Dict[str, int]:
    """Manually trigger one evaluation cycle.

    Returns count of dialogues evaluated in this batch.
    """
    count = await batch_evaluate()
    return {"evaluated_count": count}
