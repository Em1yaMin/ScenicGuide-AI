"""首页活数据路由（公开接口，无鉴权）。

提供热门景点/推荐路线数据，供首页/前端动态渲染
（替代写死静态卡片；数据仍来自 app/data/*.json）。
"""
from fastapi import APIRouter, Query

from app.schemas.home import HomeHighlightsResponse
from app.services.home_service import get_home_highlights

router = APIRouter(prefix="/api/v1/home", tags=["首页活数据"])


@router.get("/highlights", response_model=HomeHighlightsResponse)
async def home_highlights(
    spots_limit: int = Query(3, ge=1, le=20, description="热门景点条数上限"),
    routes_limit: int = Query(3, ge=1, le=20, description="推荐路线条数上限"),
) -> HomeHighlightsResponse:
    """返回首页热门景点/推荐路线（数据来自 app/data/ 本地 JSON）。"""
    return await get_home_highlights(
        spots_limit=spots_limit,
        routes_limit=routes_limit,
    )
