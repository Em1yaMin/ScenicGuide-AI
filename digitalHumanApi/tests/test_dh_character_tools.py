"""数字人形象管理 agent 工具（list_character_models / set_character_model）单测。

覆盖：
1. admin 上下文校验：非管理员上下文拒绝执行（PermissionError）
2. list_character_models：只读返回当前形象 + 可选列表（默认回退 Hiyori）
3. set_character_model：设置成功（写库 + 审计日志）、覆盖更新、设置后读取生效
4. set_character_model：非法形象名/空白失败（error 信封，不写库）
"""

import json
import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.app_setting import (
    DIGITAL_HUMAN_CHARACTERS,
    DIGITAL_HUMAN_CHARACTER_DEFAULT,
    DIGITAL_HUMAN_CHARACTER_KEY,
    AppSetting,
)
from app.services.admin_agent import tools

# 默认以管理员上下文调用（is_admin=True 注入）；越权用例显式传 False
ADMIN = {"is_admin": True}


@pytest.fixture
async def tool_db(db_engine, monkeypatch):
    """工具私有会话工厂绑定测试内存库（monkeypatch tools.async_session_factory）。"""
    factory = async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(tools, "async_session_factory", factory)
    return factory


def _parse(data: str) -> dict:
    return json.loads(data)


# ── admin 上下文校验 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_character_tools_reject_non_admin_context():
    """list_character_models / set_character_model：is_admin=False 拒绝执行。"""
    with pytest.raises(PermissionError):
        await tools.list_character_models.ainvoke({})
    with pytest.raises(PermissionError):
        await tools.set_character_model.ainvoke({"character": "Mao"})


# ── list_character_models（只读） ────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_character_models_default(tool_db):
    """无配置记录：返回默认 Hiyori + 完整可选列表 + 一句话描述。"""
    result = _parse(await tools.list_character_models.ainvoke({**ADMIN}))
    assert result["status"] == "ok"
    assert result["current"] == DIGITAL_HUMAN_CHARACTER_DEFAULT
    assert result["available"] == DIGITAL_HUMAN_CHARACTERS
    assert f"当前形象：{DIGITAL_HUMAN_CHARACTER_DEFAULT}" in result["message"]


@pytest.mark.asyncio
async def test_list_character_models_after_set(tool_db):
    """设置后读取：返回新形象（与库内一致）。"""
    await tools.set_character_model.ainvoke({"character": "Izumi", **ADMIN})
    result = _parse(await tools.list_character_models.ainvoke({**ADMIN}))
    assert result["status"] == "ok"
    assert result["current"] == "Izumi"


# ── set_character_model（写操作 + 审计） ─────────────────────────────────


@pytest.mark.asyncio
async def test_set_character_model_success_and_audit(tool_db, caplog):
    """设置成功：写库 + 审计日志（operator=admin-agent）。"""
    caplog.set_level(logging.INFO, logger="app.services.admin_agent.tools")
    result = _parse(
        await tools.set_character_model.ainvoke({"character": "Mao", **ADMIN})
    )
    assert result["status"] == "ok"
    assert result["character"] == "Mao"
    assert result["available"] == DIGITAL_HUMAN_CHARACTERS
    # 落库
    async with tool_db() as db:
        row = await db.get(AppSetting, DIGITAL_HUMAN_CHARACTER_KEY)
    assert row is not None
    assert row.value == "Mao"
    # 审计
    assert any(
        "operator=admin-agent tool=set_character_model" in r.getMessage()
        and "action=update" in r.getMessage()
        and "character=Mao" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_set_character_model_overwrite_upsert(tool_db):
    """覆盖更新：仍单行 upsert，值被替换。"""
    await tools.set_character_model.ainvoke({"character": "Kei", **ADMIN})
    await tools.set_character_model.ainvoke({"character": "Rice", **ADMIN})
    async with tool_db() as db:
        rows = (
            await db.execute(
                select(AppSetting).where(AppSetting.key == DIGITAL_HUMAN_CHARACTER_KEY)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == "Rice"


@pytest.mark.asyncio
async def test_set_character_model_invalid_name(tool_db):
    """非法形象名：error 信封，不写库。"""
    result = _parse(
        await tools.set_character_model.ainvoke({"character": "Not-A-Character", **ADMIN})
    )
    assert result["status"] == "error"
    assert "不在可选列表内" in result["message"]
    async with tool_db() as db:
        row = await db.get(AppSetting, DIGITAL_HUMAN_CHARACTER_KEY)
    assert row is None


@pytest.mark.asyncio
async def test_set_character_model_blank(tool_db):
    """空白形象名：error 信封（strip 后不在列表），不写库。"""
    result = _parse(await tools.set_character_model.ainvoke({"character": "   ", **ADMIN}))
    assert result["status"] == "error"
    assert "不在可选列表内" in result["message"]
