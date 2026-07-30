import json
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.knowledge import KnowledgeBase


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", "test-admin-token")


async def _create_item(client: AsyncClient, payload: dict):
    return await client.post(
        "/api/admin/knowledge",
        json=payload,
        headers={"X-Admin-Token": "test-admin-token"},
    )


@pytest.mark.asyncio
async def test_create_knowledge(client: AsyncClient):
    resp = await _create_item(
        client,
        {
            "title": "黄果树瀑布讲解词",
            "category": "讲解词",
            "content": "黄果树瀑布是中国最大的瀑布...",
            "tags": ["瀑布", "自然"],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "黄果树瀑布讲解词"
    assert data["category"] == "讲解词"
    assert data["tags"] == ["瀑布", "自然"]
    assert "id" in data


@pytest.mark.asyncio
async def test_list_knowledge_with_filter(client: AsyncClient):
    await _create_item(
        client,
        {
            "title": "景区开放时间 FAQ",
            "category": "FAQ",
            "content": "景区每天 08:00 开放。",
            "tags": ["时间"],
        },
    )
    await _create_item(
        client,
        {
            "title": "黄果树瀑布讲解词",
            "category": "讲解词",
            "content": "瀑布介绍。",
            "tags": ["瀑布"],
        },
    )

    resp = await client.get(
        "/api/admin/knowledge",
        params={"category": "FAQ"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["category"] == "FAQ"

    resp = await client.get(
        "/api/admin/knowledge",
        params={"keyword": "瀑布"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "黄果树瀑布讲解词"


@pytest.mark.asyncio
async def test_update_knowledge(client: AsyncClient):
    create_resp = await _create_item(
        client,
        {
            "title": "旧标题",
            "category": "景点资料",
            "content": "旧内容。",
        },
    )
    kb_id = create_resp.json()["id"]

    resp = await client.put(
        f"/api/admin/knowledge/{kb_id}",
        json={"title": "新标题", "content": "新内容。"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "新标题"
    assert data["content"] == "新内容。"
    assert data["category"] == "景点资料"


@pytest.mark.asyncio
async def test_delete_knowledge(client: AsyncClient):
    create_resp = await _create_item(
        client,
        {
            "title": "待删除",
            "category": "景点资料",
            "content": "内容。",
        },
    )
    kb_id = create_resp.json()["id"]

    resp = await client.delete(
        f"/api/admin/knowledge/{kb_id}",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    resp = await client.get(
        "/api/admin/knowledge",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_import_json(client: AsyncClient):
    payload = [
        {
            "title": "JSON 导入条目 1",
            "category": "FAQ",
            "content": "内容 1",
            "tags": ["导入"],
        },
        {
            "title": "JSON 导入条目 2",
            "category": "讲解词",
            "content": "内容 2",
        },
    ]
    resp = await client.post(
        "/api/admin/knowledge/import",
        params={"default_category": "景点资料"},
        files={"file": ("data.json", json.dumps(payload, ensure_ascii=False), "application/json")},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["imported_count"] == 2

    list_resp = await client.get(
        "/api/admin/knowledge",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert list_resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_import_markdown(client: AsyncClient):
    content = "# 标题一\n\n内容一。\n# 标题二\n\n内容二。\n"
    resp = await client.post(
        "/api/admin/knowledge/import",
        params={"default_category": "讲解词"},
        files={"file": ("data.md", content, "text/markdown")},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert resp.status_code == 200
    assert resp.json()["imported_count"] == 2

    list_resp = await client.get(
        "/api/admin/knowledge",
        params={"category": "讲解词"},
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert list_resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_unauthorized(client: AsyncClient):
    # 缺少 Token 时返回 401
    resp = await client.get("/api/admin/knowledge")
    assert resp.status_code == 401

    resp = await client.get(
        "/api/admin/knowledge",
        headers={"X-Admin-Token": "wrong-token"},
    )
    assert resp.status_code == 401
