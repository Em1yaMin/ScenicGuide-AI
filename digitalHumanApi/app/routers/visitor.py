from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.visitor import VisitorRegisterRequest, VisitorRegisterResponse
from app.services.visitor_service import register_visitor

router = APIRouter(prefix="/api/v1/visitor", tags=["游客端API"])


@router.post("/register", response_model=VisitorRegisterResponse)
async def register_visitor_endpoint(
    request: VisitorRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> VisitorRegisterResponse:
    visitor, is_new = await register_visitor(db, request)
    return VisitorRegisterResponse(
        visitor_id=visitor.id,
        is_new=is_new,
        first_visit=visitor.first_visit,
        last_visit=visitor.last_visit,
    )
