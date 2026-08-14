"""数字人形象模块测试：公开/管理端 API + 默认值回退 + 鉴权拒绝。"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.models.app_setting import (
    DIGITAL_HUMAN_CHARACTERS,
    DIGITAL_HUMAN_CHARACTER_DEFAULT,
    DIGITAL_HUMAN_CHARACTER_KEY,
    AppSetting,
)

ADMIN_TOKEN = "test-admin-token"

# 可选列表应与 ADH public/characters 目录一致（13 个）
assert len(DIGITAL_HUMAN_CHARACTERS) == 13


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)


def _assert_ok_shape(data: dict, character: str) -> None:
    """响应契约：character + available（完整可选列表）。"""
    assert data["character"] == character
    assert data["available"] == DIGITAL_HUMAN_CHARACTERS


# ── 公开 GET（访客端/ADH 用，无需鉴权） ──────────────────────────────────


@pytest.mark.asyncio
async def test_public_get_default_character(client: AsyncClient):
    """公开读取：无配置记录时回退默认 Hiyori + 完整可选列表。"""
    resp = await client.get("/api/v1/dh-character")
    assert resp.status_code == 200
    _assert_ok_shape(resp.json(), DIGITAL_HUMAN_CHARACTER_DEFAULT)


@pytest.mark.asyncio
async def test_public_get_after_admin_set(client: AsyncClient, db_session):
    """公开读取：管理端写入后读到新形象（访客端/ADH 立即生效）。"""
    resp = await client.put(
        "/api/admin/dh-character",
        json={"character": "Mao"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/dh-character")
    assert resp.status_code == 200
    _assert_ok_shape(resp.json(), "Mao")


@pytest.mark.asyncio
async def test_public_get_falls_back_on_invalid_stored_value(
    client: AsyncClient, db_session
):
    """默认值回退：库内为非法形象名时读取回退默认（防御脏数据）。"""
    db_session.add(
        AppSetting(key=DIGITAL_HUMAN_CHARACTER_KEY, value="Not-A-Character")
    )
    await db_session.commit()

    resp = await client.get("/api/v1/dh-character")
    assert resp.status_code == 200
    _assert_ok_shape(resp.json(), DIGITAL_HUMAN_CHARACTER_DEFAULT)


# ── 管理端 GET（X-Admin-Token 鉴权） ──────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_get_requires_auth(client: AsyncClient):
    """管理端读取：未携带 token 拒绝 401。"""
    resp = await client.get("/api/admin/dh-character")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_get_with_token(client: AsyncClient):
    """管理端读取：携带 token 返回当前形象 + 可选列表。"""
    resp = await client.get(
        "/api/admin/dh-character", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert resp.status_code == 200
    _assert_ok_shape(resp.json(), DIGITAL_HUMAN_CHARACTER_DEFAULT)


# ── 管理端 PUT（写库 + 审计） ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_put_updates_and_persists(
    client: AsyncClient, db_session
):
    """管理端设置：写库持久化（app_settings 单行 upsert）。"""
    resp = await client.put(
        "/api/admin/dh-character",
        json={"character": "Shizuku"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    _assert_ok_shape(resp.json(), "Shizuku")

    row = await db_session.get(AppSetting, DIGITAL_HUMAN_CHARACTER_KEY)
    assert row is not None
    assert row.value == "Shizuku"
    assert row.updated_at is not None

    # 再次设置：覆盖更新（仍单行）
    resp = await client.put(
        "/api/admin/dh-character",
        json={"character": "Hiyori"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    _assert_ok_shape(resp.json(), "Hiyori")
    rows = (
        await db_session.execute(
            select(AppSetting).where(AppSetting.key == DIGITAL_HUMAN_CHARACTER_KEY)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].value == "Hiyori"


@pytest.mark.asyncio
async def test_admin_put_requires_auth(client: AsyncClient):
    """管理端设置：未携带 token 拒绝 401。"""
    resp = await client.put(
        "/api/admin/dh-character", json={"character": "Mao"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_put_invalid_character(client: AsyncClient):
    """管理端设置：非法形象名拒绝 422（不在可选列表内）。"""
    resp = await client.put(
        "/api/admin/dh-character",
        json={"character": "Not-A-Character"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_admin_put_blank_character(client: AsyncClient):
    """管理端设置：空白形象名拒绝 422（schema min_length 拦截）。"""
    resp = await client.put(
        "/api/admin/dh-character",
        json={"character": "   "},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 422
