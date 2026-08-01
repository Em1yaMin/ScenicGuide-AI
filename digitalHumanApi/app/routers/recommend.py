"""个性化推荐路由：根据游客偏好推荐游览路线。"""
from fastapi import APIRouter

from app.schemas.recommend import RecommendRequest, RecommendResponse
from app.services.recommend_agent import recommend

router = APIRouter(prefix="/api/v1/recommend", tags=["个性化推荐"])


@router.post("/", response_model=RecommendResponse)
async def recommend_routes(request: RecommendRequest) -> RecommendResponse:
    """根据游客偏好推荐游览路线。

    接收游客的自然语言偏好描述，通过 LangChain Agent + Tool Calling
    模式匹配合适的游览路线，返回排序后的推荐列表及推荐理由。

    Example:
        POST /api/v1/recommend
        {"preference": "喜欢自然风光，带老人和孩子"}
    """
    return await recommend(request.preference)
