from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.visitor import (
    RecommendedQuestionsResponse,
    VisitorRegisterRequest,
    VisitorRegisterResponse,
)
from app.tasks.recommend_questions import get_cached_questions, refresh_cycle
from app.services.visitor_service import register_visitor

router = APIRouter(prefix="/api/v1/visitor", tags=["游客端API"])


@router.post("/refresh-recommend")
async def refresh_recommended() -> dict:
    """临时端点：手动触发推荐问句刷新（调试用，用完即删）。"""
    count = await refresh_cycle()
    return {"cached_count": count, "questions": get_cached_questions()}


@router.get("/recommended-questions", response_model=RecommendedQuestionsResponse)
async def recommended_questions() -> RecommendedQuestionsResponse:
    """返回动态推荐问句（从后台任务缓存读取）。

    无需 DB 会话 — 纯内存读取。
    首次启动时缓存为空，返回 {"questions": [], "total": 0}。
    """
    questions = get_cached_questions()
    return RecommendedQuestionsResponse(questions=questions)


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
