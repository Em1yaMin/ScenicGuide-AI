"""推荐接口的 Pydantic 请求/响应模型。"""
from typing import List, Optional

from pydantic import BaseModel


class RecommendRequest(BaseModel):
    """个性化推荐请求。

    preference: 游客偏好描述，如 "喜欢自然风光，带老人"
    """

    preference: str


class SpotInfo(BaseModel):
    """景点简要信息，嵌套在路线推荐中返回。"""

    id: str
    name: str
    description: str
    tags: List[str]
    duration_minutes: int
    difficulty: str


class RouteRecommendation(BaseModel):
    """单条路线推荐。"""

    route_id: str
    route_name: str
    description: str
    spots: List[SpotInfo]
    tags: List[str]
    suitable_for: List[str]
    total_duration_minutes: int
    difficulty: str
    recommend_reason: str
    match_score: int  # 0-100 匹配度评分


class RecommendResponse(BaseModel):
    """个性化推荐响应。"""

    preference: str
    recommendations: List[RouteRecommendation]
    summary: str  # 综合推荐说明
    total: int
