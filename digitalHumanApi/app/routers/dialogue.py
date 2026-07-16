from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from app.tasks.polling import poll_once

router = APIRouter(prefix="/api/v1/dialogue", tags=["对话抓取"])


@router.post("/sync-now")
async def sync_now(user: Optional[str] = Query(None)) -> Dict[str, Any]:
    """手动触发一次对话轮询。

    Args:
        user: 可选，指定同步的虚拟用户。不传则同步所有配置用户。
    """
    count = await poll_once(user_filter=user)
    return {"status": "ok", "synced": count}
