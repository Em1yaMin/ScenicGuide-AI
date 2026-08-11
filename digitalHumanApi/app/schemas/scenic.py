"""景区结构化数据（spots/routes）管理 API 的 Pydantic 模型。

数据源为 `app/data/spots.json` 与 `routes.json`——导览 agent 结构化工具
（search_spots / search_routes / get_spot_detail / get_route_detail /
filter_by_tags / recommend_routes）、首页活数据（home_service）与推荐问句
降级（recommend_service）的**单一数据来源**。管理端经本 schema 读写该
文件，保证「管理后台页面数据 = agent 工具数据」。

字段与既有 JSON 结构对齐（破坏 schema 会连带破坏 agent 工具/首页卡片/
既有测试），新增字段需同步本文件与 `app/data/` 两个文件。
"""
from typing import Optional

from pydantic import BaseModel, Field

# 难度可选值（与既有数据/工具提示词约定一致）
DIFFICULTY_VALUES = ("轻松", "中等", "挑战")


class ScenicSpot(BaseModel):
    """景点数据结构（字段与 app/data/spots.json 对齐，另见 schemas/home.HomeSpot）。"""

    id: str
    name: str
    description: str
    tags: list[str]
    duration_minutes: int
    difficulty: str
    location: str
    highlights: list[str]

    model_config = {"extra": "allow"}


class ScenicSpotCreate(BaseModel):
    """新建景点请求（id 由服务端自动生成，不开放指定）。"""

    name: str = Field(min_length=1, max_length=50, description="景点名称")
    description: str = Field(min_length=1, description="景点介绍")
    tags: list[str] = Field(default_factory=list, description="标签")
    duration_minutes: int = Field(default=60, ge=1, le=600, description="建议游览时长（分钟）")
    difficulty: str = Field(default="轻松", description="难度：轻松/中等/挑战")
    location: str = Field(default="", description="具体位置")
    highlights: list[str] = Field(default_factory=list, description="游玩亮点")

    def validate_difficulty(self) -> None:
        if self.difficulty not in DIFFICULTY_VALUES:
            raise ValueError(f"difficulty 仅支持 {'/'.join(DIFFICULTY_VALUES)}")


class ScenicSpotUpdate(BaseModel):
    """部分更新景点请求（仅更新传入的非 None 字段）。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    description: Optional[str] = Field(default=None, min_length=1)
    tags: Optional[list[str]] = None
    duration_minutes: Optional[int] = Field(default=None, ge=1, le=600)
    difficulty: Optional[str] = None
    location: Optional[str] = None
    highlights: Optional[list[str]] = None


class ScenicRoute(BaseModel):
    """路线数据结构（字段与 app/data/routes.json 对齐，另见 schemas/home.HomeRoute）。"""

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


class ScenicRouteCreate(BaseModel):
    """新建路线请求（id 由服务端自动生成；spot_ids 须引用已存在景点 id）。"""

    name: str = Field(min_length=1, max_length=50, description="路线名称")
    description: str = Field(min_length=1, description="路线介绍")
    spot_ids: list[str] = Field(default_factory=list, description="途经景点 id 列表")
    tags: list[str] = Field(default_factory=list, description="标签")
    suitable_for: list[str] = Field(default_factory=list, description="适合人群")
    total_duration_minutes: int = Field(default=180, ge=1, le=1440, description="总时长（分钟）")
    difficulty: str = Field(default="轻松", description="难度：轻松/中等/挑战")
    recommend_reason: str = Field(default="", description="推荐理由")

    def validate_difficulty(self) -> None:
        if self.difficulty not in DIFFICULTY_VALUES:
            raise ValueError(f"difficulty 仅支持 {'/'.join(DIFFICULTY_VALUES)}")


class ScenicRouteUpdate(BaseModel):
    """部分更新路线请求（仅更新传入的非 None 字段）。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    description: Optional[str] = Field(default=None, min_length=1)
    spot_ids: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    suitable_for: Optional[list[str]] = None
    total_duration_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    difficulty: Optional[str] = None
    recommend_reason: Optional[str] = None
