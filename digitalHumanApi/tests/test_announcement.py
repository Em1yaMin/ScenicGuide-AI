"""公告模块测试：管理端 CRUD/鉴权/状态切换 + 访客端已发布过滤与排序。"""
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.models.announcement import STATUS_DRAFT, STATUS_PUBLISHED

ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)


async def _create(client: AsyncClient, payload: dict, token: str = ADMIN_TOKEN):
    return await client.post(
        "/api/admin/announcements",
        json=payload,
        headers={"X-Admin-Token": token},
    )


async def _create_published(client: AsyncClient, title: str, pinned: bool = False) -> int:
    resp = await _create(
        client,
        {"title": title, "content": "正文", "status": STATUS_PUBLISHED, "pinned": pinned},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


# ── 管理端 CRUD ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_announcement_default_draft(client: AsyncClient):
    """创建公告：默认草稿、不置顶。"""
    resp = await _create(client, {"title": "景区开放时间调整", "content": "8 月 10 日起 08:00 开放。"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "景区开放时间调整"
    assert data["status"] == STATUS_DRAFT
    assert data["pinned"] is False
    assert "id" in data and "created_at" in data


@pytest.mark.asyncio
async def test_create_announcement_published_pinned(client: AsyncClient):
    """创建公告：可直接指定发布 + 置顶。"""
    resp = await _create(
        client,
        {"title": "紧急通知", "content": "索道检修暂停运营。", "status": STATUS_PUBLISHED, "pinned": True},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == STATUS_PUBLISHED
    assert data["pinned"] is True


@pytest.mark.asyncio
async def test_list_announcements_with_filters(client: AsyncClient):
    """管理端列表：状态筛选 + 关键词搜索 + 分页。"""
    await _create(client, {"title": "草稿公告", "content": "未发布内容"})
    await _create_published(client, "发布公告")
    await _create(client, {"title": "第二篇草稿", "content": "草稿内容"})

    # 状态筛选
    resp = await client.get(
        "/api/admin/announcements",
        params={"status": STATUS_DRAFT},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert all(i["status"] == STATUS_DRAFT for i in data["items"])

    # 关键词搜索（标题命中；正文含“发布”的内容不参与标题搜索歧义）
    resp = await client.get(
        "/api/admin/announcements",
        params={"keyword": "第二篇"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "第二篇草稿"

    # 分页
    resp = await client.get(
        "/api/admin/announcements",
        params={"page": 1, "page_size": 2},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    data = resp.json()
    assert data["total"] == 3
    assert len(data["items"]) == 2


@pytest.mark.asyncio
async def test_update_announcement_partial(client: AsyncClient):
    """部分更新：仅更新传入字段，未传字段保持不变。"""
    ann_id = await _create_published(client, "原标题")

    resp = await client.put(
        f"/api/admin/announcements/{ann_id}",
        json={"title": "新标题", "pinned": True},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "新标题"
    assert data["pinned"] is True
    assert data["content"] == "正文"  # 未传字段保持不变
    assert data["status"] == STATUS_PUBLISHED


@pytest.mark.asyncio
async def test_update_announcement_not_found(client: AsyncClient):
    resp = await client.put(
        "/api/admin/announcements/9999",
        json={"title": "新标题"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_status_publish_and_draft(client: AsyncClient):
    """发布状态切换：草稿→发布（访客可见）→草稿（访客不可见）。"""
    resp = await _create(client, {"title": "待发布", "content": "内容"})
    ann_id = resp.json()["id"]
    assert resp.json()["status"] == STATUS_DRAFT

    # 草稿状态访客端不可见
    public = await client.get("/api/v1/announcements")
    assert public.json()["total"] == 0

    # 切换为发布
    resp = await client.patch(
        f"/api/admin/announcements/{ann_id}/status",
        json={"status": STATUS_PUBLISHED},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == STATUS_PUBLISHED

    public = await client.get("/api/v1/announcements")
    assert public.json()["total"] == 1

    # 切回草稿
    resp = await client.patch(
        f"/api/admin/announcements/{ann_id}/status",
        json={"status": STATUS_DRAFT},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.json()["status"] == STATUS_DRAFT
    public = await client.get("/api/v1/announcements")
    assert public.json()["total"] == 0


@pytest.mark.asyncio
async def test_set_status_invalid_value(client: AsyncClient):
    """状态切换：非法取值返回 422。"""
    ann_id = await _create_published(client, "公告")
    resp = await client.patch(
        f"/api/admin/announcements/{ann_id}/status",
        json={"status": "invalid"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_announcement(client: AsyncClient):
    ann_id = await _create_published(client, "待删除")

    resp = await client.delete(
        f"/api/admin/announcements/{ann_id}",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 删除后访客端不可见
    public = await client.get("/api/v1/announcements")
    assert public.json()["total"] == 0

    # 重复删除 404
    resp = await client.delete(
        f"/api/admin/announcements/{ann_id}",
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert resp.status_code == 404


# ── 鉴权（X-Admin-Token） ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_requires_token(client: AsyncClient):
    """无 Token / 错误 Token → 401（列表、创建、更新、删除、状态切换全覆盖）。"""
    for method, url, kwargs in [
        ("get", "/api/admin/announcements", {}),
        ("post", "/api/admin/announcements", {"json": {"title": "t", "content": "c"}}),
        ("put", "/api/admin/announcements/1", {"json": {"title": "t"}}),
        ("patch", "/api/admin/announcements/1/status", {"json": {"status": STATUS_PUBLISHED}}),
        ("delete", "/api/admin/announcements/1", {}),
    ]:
        resp = await client.request(method, url, **kwargs)
        assert resp.status_code == 401, f"{method} {url} 应 401"

        resp = await client.request(
            method, url, headers={"X-Admin-Token": "wrong-token"}, **kwargs
        )
        assert resp.status_code == 401, f"{method} {url} 错误 token 应 401"


# ── 访客端（公开） ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_public_only_published(client: AsyncClient):
    """访客端仅返回已发布公告；草稿不可见。"""
    await _create(client, {"title": "草稿一", "content": "c"})
    await _create_published(client, "已发布一")
    await _create(client, {"title": "草稿二", "content": "c"})
    await _create_published(client, "已发布二")

    resp = await client.get("/api/v1/announcements")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert {i["title"] for i in data["items"]} == {"已发布一", "已发布二"}
    # 访客端条目不含 status 字段
    assert "status" not in data["items"][0]


@pytest.mark.asyncio
async def test_public_ordering_pinned_then_newest(client: AsyncClient):
    """访客端排序：置顶优先，其次发布时间倒序。"""
    await _create_published(client, "普通旧")
    await _create_published(client, "普通新")
    pinned_id = await _create_published(client, "置顶", pinned=True)

    resp = await client.get("/api/v1/announcements")
    items = resp.json()["items"]
    assert items[0]["id"] == pinned_id
    assert [i["title"] for i in items[1:]] == ["普通新", "普通旧"]


@pytest.mark.asyncio
async def test_public_limit(client: AsyncClient):
    """访客端 limit 参数生效。"""
    for i in range(5):
        await _create_published(client, f"公告{i}")
    resp = await client.get("/api/v1/announcements", params={"limit": 2})
    data = resp.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
