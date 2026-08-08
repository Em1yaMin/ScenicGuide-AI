"""首页活数据接口的 Pydantic 响应模型。

数据源为 app/data/*.json（景点/路线），接口化后前端首页
不再写死卡片，后续管理端可替换数据源实现"改数据 → 首页实时变化"。
"""
from pydantic import BaseModel


class HomeSpot(BaseModel):
    """热门景点卡片数据（字段与 app/data/spots.json 对齐）。"""

    id: str
    name: str
    description: str
    tags: list[str]
    duration_minutes: int
    difficulty: str
    location: str
    highlights: list[str]

    model_config = {"extra": "allow"}


class HomeRoute(BaseModel):
    """推荐路线卡片数据（字段与 app/data/routes.json 对齐）。"""

    id: str
    name: str
    description: str
    spot_ids: list[str]
    tags: list[str]
    suitable_for: list[str]
    total_duration_minutes: int
    difficulty: str
    recommend_reason: str

    model_config = {"extra": "allow"}


class HomeHighlightsResponse(BaseModel):
    """首页活数据：热门景点 + 推荐路线。"""

    spots: list[HomeSpot]
    routes: list[HomeRoute]

    model_config = {"extra": "allow"}
