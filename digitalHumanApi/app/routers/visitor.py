from typing import Dict

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/visitor", tags=["游客端API"])


@router.post("/register")
async def register_visitor() -> Dict[str, str]:
    return {"message": "visitor register placeholder"}
