from typing import Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/dialogue", tags=["对话抓取"])


@router.get("/poll")
async def poll_dialogues() -> Dict[str, str]:
    return {"message": "dialogue poll placeholder"}
