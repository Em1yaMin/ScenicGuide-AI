"""数字人形象路由。

- 访客端 `GET /api/v1/dh-character`：公开（访客端/ADH 用），返回当前形象 + 可选列表
- 管理端 `GET/PUT /api/admin/dh-character`：X-Admin-Token 鉴权（复用
  verify_admin_token）；PUT 写库 + 审计日志（logger，operator=admin，
  与记忆管理 B5 审计风格一致）

形象常量与 ADH（live2d-frontend/web/public/characters/）目录一致，
默认 Hiyori；无配置记录读取时回退默认（不写库）。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.routers.deps import verify_admin_token
from app.schemas.dh_character import DhCharacterResponse, DhCharacterUpdateRequest
from app.services import dh_character_service

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/v1/dh-character", tags=["数字人形象"])
admin_router = APIRouter(prefix="/api/admin/dh-character", tags=["数字人形象管理"])


def _response(character: str) -> DhCharacterResponse:
    return DhCharacterResponse(
        character=character,
        available=dh_character_service.DIGITAL_HUMAN_CHARACTERS,
    )


# ── 访客端（公开，无需鉴权） ─────────────────────────────────────────────


@public_router.get("", response_model=DhCharacterResponse)
async def get_public_character(
    db: AsyncSession = Depends(get_db),
) -> DhCharacterResponse:
    """公开读取当前数字人形象 + 可选列表（访客端/ADH 嵌入用）。"""
    return _response(await dh_character_service.get_character(db))


# ── 管理端（X-Admin-Token 鉴权） ─────────────────────────────────────────


@admin_router.get("", response_model=DhCharacterResponse)
async def get_admin_character(
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> DhCharacterResponse:
    """管理端读取当前数字人形象 + 可选列表。"""
    return _response(await dh_character_service.get_character(db))


@admin_router.put("", response_model=DhCharacterResponse)
async def set_admin_character(
    request: DhCharacterUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: str = Depends(verify_admin_token),
) -> DhCharacterResponse:
    """设置当前数字人形象（写库 + 审计日志）。

    形象名非法（不在可选列表内）由 schema 校验返回 422。
    """
    name = request.character
    await dh_character_service.set_character(db, name)
    logger.info(
        "数字人形象管理审计: operator=admin action=set_digital_human_character "
        "target=digital_human_character value=%s",
        name,
    )
    return _response(name)
