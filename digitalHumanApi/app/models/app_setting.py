"""通用应用配置（key-value）模型 + 数字人形象常量。

app_settings 为通用 key-value 配置表（2026-08 数字人形象管理引入）：
单行一条配置，key 为字符串主键（如 digital_human_character），value 存
配置值，updated_at 记录最近更新时刻。

数字人形象常量与 ADH（live2d-frontend/web/public/characters/）目录一致，
默认 Hiyori；无配置记录时读取回退默认（不写库）。
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# 数字人形象配置 key（app_settings 表）
DIGITAL_HUMAN_CHARACTER_KEY = "digital_human_character"

# 可选形象列表（与 ADH public/characters 目录一致，13 个）
DIGITAL_HUMAN_CHARACTERS = [
    "Hiyori",
    "Chitose",
    "Epsilon",
    "Haru-1",
    "Haru-2",
    "Hibiki",
    "Izumi",
    "Kei",
    "Mao",
    "Miara",
    "Rice",
    "Shizuku",
    "Tsumiki",
]

# 默认形象（无配置记录时回退，不写库）
DIGITAL_HUMAN_CHARACTER_DEFAULT = "Hiyori"


class AppSetting(Base):
    """通用 key-value 应用配置表。

    单行一条配置：key 为字符串主键（如 digital_human_character），
    value 存配置值，updated_at 记录最近更新时刻（Python 侧默认值/onupdate）。
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(512))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
