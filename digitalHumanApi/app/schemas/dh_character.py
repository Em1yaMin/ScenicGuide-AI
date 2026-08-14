"""数字人形象模块的 Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field, field_validator

from app.models.app_setting import DIGITAL_HUMAN_CHARACTERS


class DhCharacterResponse(BaseModel):
    """数字人形象响应（访客端/管理端共用）：当前形象 + 可选列表。"""

    character: str
    available: list[str]


class DhCharacterUpdateRequest(BaseModel):
    """设置数字人形象请求：形象名必须在可选列表内（非法 422）。"""

    character: str = Field(..., min_length=1, max_length=64, description="形象名")

    @field_validator("character")
    @classmethod
    def _validate_character(cls, value: str) -> str:
        name = value.strip()
        if name not in DIGITAL_HUMAN_CHARACTERS:
            raise ValueError(
                f"形象 {name!r} 不在可选列表内（可选：{'/'.join(DIGITAL_HUMAN_CHARACTERS)}）"
            )
        return name
