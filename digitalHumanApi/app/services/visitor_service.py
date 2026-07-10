import logging
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.visitor import Visitor
from app.schemas.visitor import VisitorRegisterRequest

logger = logging.getLogger(__name__)

ReturnType = Tuple[Visitor, bool]  # (visitor, is_new)


async def register_visitor(
    db: AsyncSession, request: VisitorRegisterRequest
) -> ReturnType:
    """Upsert a visitor. Returns (visitor, is_new). flush() only — caller commits."""
    result = await db.execute(select(Visitor).where(Visitor.id == request.visitor_id))
    visitor = result.scalar_one_or_none()

    if visitor is None:
        visitor = Visitor(
            id=request.visitor_id,
            device_info=request.device_info,
        )
        db.add(visitor)
        await db.flush()
        logger.info("New visitor registered: %s", request.visitor_id)
        return visitor, True

    if request.device_info is not None:
        visitor.device_info = request.device_info

    await db.flush()
    logger.debug("Visitor updated: %s", request.visitor_id)
    return visitor, False
