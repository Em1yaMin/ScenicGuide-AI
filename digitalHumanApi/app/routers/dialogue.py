from typing import Dict

from fastapi import APIRouter

from app.tasks.polling import poll_once

router = APIRouter(prefix="/api/v1/dialogue", tags=["对话抓取"])


@router.post("/sync-now")
async def sync_now() -> Dict[str, int]:
    """Manually trigger one polling cycle.

    Returns count of new messages synced from Dify.
    """
    count = await poll_once()
    return {"synced_count": count}
