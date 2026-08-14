"""数字人形象配置服务（app_settings 表，key=digital_human_character）。

- 读取：无配置记录时回退默认 Hiyori（不写库）；库内值为非法形象名时
  同样回退默认（防御脏数据）
- 写入：upsert 单行（key 主键），不覆盖 updated_at 语义由模型 onupdate 保证
- 校验：形象名必须 ∈ DIGITAL_HUMAN_CHARACTERS（与 ADH public/characters
  目录一致）；服务层防御性校验（schema/工具层已先校验）
"""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import (
    DIGITAL_HUMAN_CHARACTERS,
    DIGITAL_HUMAN_CHARACTER_DEFAULT,
    DIGITAL_HUMAN_CHARACTER_KEY,
    AppSetting,
)

logger = logging.getLogger(__name__)


async def get_character(db: AsyncSession) -> str:
    """读取当前数字人形象；无记录/非法值时回退默认（不写库）。"""
    row = await db.get(AppSetting, DIGITAL_HUMAN_CHARACTER_KEY)
    if row is None or not row.value:
        return DIGITAL_HUMAN_CHARACTER_DEFAULT
    if row.value not in DIGITAL_HUMAN_CHARACTERS:
        logger.warning(
            "数字人形象配置值为非法形象名 %r，回退默认 %s",
            row.value,
            DIGITAL_HUMAN_CHARACTER_DEFAULT,
        )
        return DIGITAL_HUMAN_CHARACTER_DEFAULT
    return row.value


async def set_character(db: AsyncSession, character: str) -> str:
    """设置数字人形象（upsert 单行配置）；非法形象名抛 ValueError。

    调用方负责提交事务（路由层 get_db / agent 工具 _db_session 提交）。
    """
    name = character.strip()
    if name not in DIGITAL_HUMAN_CHARACTERS:
        raise ValueError(
            f"形象 {name!r} 不在可选列表内（可选：{'/'.join(DIGITAL_HUMAN_CHARACTERS)}）"
        )
    row = await db.get(AppSetting, DIGITAL_HUMAN_CHARACTER_KEY)
    if row is None:
        row = AppSetting(key=DIGITAL_HUMAN_CHARACTER_KEY, value=name)
        db.add(row)
    else:
        row.value = name
    logger.info("数字人形象配置更新: key=%s value=%s", DIGITAL_HUMAN_CHARACTER_KEY, name)
    return name
