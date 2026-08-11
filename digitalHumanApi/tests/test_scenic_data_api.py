"""景区结构化数据管理 API 测试（/api/admin/scenic，批次 e3 灵山化任务）。

覆盖：
- 鉴权：无 token 401 / 错 token 401
- 列表/详情：灵山数据（10 景点 / 8 路线）、404
- 新增：id 自动生成（spot_011）、名称唯一 422、难度非法 422
- 更新：部分更新、404
- 删除：admin 放行（404 目标不存在）、operator 403（RBAC 删除类敏感操作）
- 路线：spot_ids 引用校验（未知景点 id 422）
- **与 agent 工具数据一致**：页面写入后 search_spots 等 agent 工具实时读到新数据
  （单一来源 = app/data JSON，agent 工具零改动）
- **装配层工具缓存联动失效**：景区数据变更后静态数据工具缓存立即失效
  （防 agent 最长 1 小时读到旧数据）

数据目录重定向：写操作经 scenic_data_service 落盘 app/data/*.json，测试
monkeypatch `_DATA_DIR` 到 tmp_path 并从仓库拷贝真实数据文件（验证真实
schema 与 CRUD 行为，不污染工作区数据）。
"""
import json
import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient

import app.services.scenic_data_service as scenic_service
from app.core.config import settings

ADMIN_TOKEN = "scenic-admin-token"
OPERATOR_TOKEN = "scenic-operator-token"


@pytest.fixture(autouse=True)
def _setup_tokens(monkeypatch):
    """配置双角色：ADMIN_API_TOKEN（兼容）+ ADMIN_TOKENS 映射 operator。"""
    monkeypatch.setattr(settings, "ADMIN_API_TOKEN", ADMIN_TOKEN)
    monkeypatch.setattr(
        settings,
        "ADMIN_TOKENS",
        {ADMIN_TOKEN: "admin", OPERATOR_TOKEN: "operator"},
    )


@pytest.fixture(autouse=True)
def _redirect_data_dir(monkeypatch, tmp_path):
    """重定向景区数据目录到临时目录，并拷贝仓库真实数据文件作为初始数据。

    agent 工具（recommend_agent）与首页（home_service）各自持有独立的
    `_DATA_DIR` 常量（指向同一真实目录），一并重定向保证「页面写入 →
    agent 工具读到同一份数据」的链路在测试内闭环。
    """
    src = Path(__file__).resolve().parents[1] / "app" / "data"
    dst = tmp_path / "data"
    dst.mkdir(parents=True, exist_ok=True)
    for filename in ("spots.json", "routes.json"):
        shutil.copy(src / filename, dst / filename)
    monkeypatch.setattr(scenic_service, "_DATA_DIR", dst)
    # agent 结构化工具 / 首页活数据同源重定向（见 `_load_json` 的 `_DATA_DIR`）
    from app.services import home_service, recommend_agent

    monkeypatch.setattr(recommend_agent, "_DATA_DIR", dst)
    monkeypatch.setattr(home_service, "_DATA_DIR", dst)
    return dst


def _auth(token: str) -> dict:
    return {"X-Admin-Token": token}


def _payload(filename: str, index: int) -> dict:
    """读取仓库真实数据文件中的第 index 条（校验真实 schema）。"""
    src = Path(__file__).resolve().parents[1] / "app" / "data"
    items = json.loads((src / filename).read_text(encoding="utf-8"))
    return items[index]


# ── 鉴权 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_401(client: AsyncClient):
    """无 token 访问景区数据管理端点 401。"""
    for path in ("/api/admin/scenic/spots", "/api/admin/scenic/routes"):
        resp = await client.get(path)
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_token_401(client: AsyncClient):
    """错误 token 401。"""
    resp = await client.get(
        "/api/admin/scenic/spots", headers=_auth("wrong-token")
    )
    assert resp.status_code == 401


# ── 列表 / 详情（灵山数据） ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_spots_is_lingshan_data(client: AsyncClient):
    """景点列表为灵山数据（10 条，含灵山大佛等核心景点）。"""
    resp = await client.get("/api/admin/scenic/spots", headers=_auth(ADMIN_TOKEN))
    assert resp.status_code == 200
    spots = resp.json()
    assert len(spots) == 10
    names = [s["name"] for s in spots]
    assert "灵山大佛" in names
    assert "九龙灌浴" in names
    assert "灵山梵宫" in names
    # schema 字段完整
    for s in spots:
        assert {"id", "name", "description", "tags", "duration_minutes",
                "difficulty", "location", "highlights"} <= set(s)


@pytest.mark.asyncio
async def test_list_routes_is_lingshan_data(client: AsyncClient):
    """路线列表为灵山数据（8 条，spot_ids 均引用已存在景点）。"""
    resp = await client.get("/api/admin/scenic/routes", headers=_auth(ADMIN_TOKEN))
    assert resp.status_code == 200
    routes = resp.json()
    assert len(routes) == 8
    spot_ids = {s["id"] for s in (await client.get(
        "/api/admin/scenic/spots", headers=_auth(ADMIN_TOKEN)
    )).json()}
    for r in routes:
        assert {"id", "name", "description", "spot_ids", "tags",
                "suitable_for", "total_duration_minutes", "difficulty",
                "recommend_reason"} <= set(r)
        assert set(r["spot_ids"]) <= spot_ids


@pytest.mark.asyncio
async def test_get_spot_by_id(client: AsyncClient):
    """按 id 查询景点；不存在 404。"""
    resp = await client.get(
        "/api/admin/scenic/spots/spot_001", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "灵山大照壁"

    resp = await client.get(
        "/api/admin/scenic/spots/spot_nonexist", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_route_by_id(client: AsyncClient):
    """按 id 查询路线；不存在 404。"""
    resp = await client.get(
        "/api/admin/scenic/routes/route_001", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "历史文化深度游"

    resp = await client.get(
        "/api/admin/scenic/routes/route_nonexist", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 404


# ── 新增 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_spot_generates_id(client: AsyncClient):
    """新增景点：201 + id 自动生成（spot_011，编号 = 现有最大值 + 1）。"""
    resp = await client.post(
        "/api/admin/scenic/spots",
        json={
            "name": "三圣殿",
            "description": "供奉华严三圣的殿堂，佛教历史文化展示区",
            "tags": ["历史人文", "宗教"],
            "duration_minutes": 40,
            "difficulty": "轻松",
            "location": "灵山梵宫东侧",
            "highlights": ["华严三圣像"],
        },
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "spot_011"
    assert data["name"] == "三圣殿"

    # 落盘生效：再次列表可见（页面数据 = 单一来源）
    resp = await client.get("/api/admin/scenic/spots", headers=_auth(ADMIN_TOKEN))
    assert any(s["name"] == "三圣殿" for s in resp.json())


@pytest.mark.asyncio
async def test_create_spot_duplicate_name_422(client: AsyncClient):
    """新增景点名称重复 422（防 agent 工具按名检索歧义）。"""
    resp = await client.post(
        "/api/admin/scenic/spots",
        json={"name": "灵山大佛", "description": "重复名称"},
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 422
    assert "已存在" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_spot_invalid_difficulty_422(client: AsyncClient):
    """新增景点难度非法 422（仅支持 轻松/中等/挑战）。"""
    resp = await client.post(
        "/api/admin/scenic/spots",
        json={
            "name": "测试景点",
            "description": "难度非法",
            "difficulty": "极限",
        },
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_route_unknown_spot_id_422(client: AsyncClient):
    """新增路线引用不存在的景点 id 422（防 agent 工具返回「未知景点」）。"""
    resp = await client.post(
        "/api/admin/scenic/routes",
        json={
            "name": "测试路线",
            "description": "引用无效景点",
            "spot_ids": ["spot_999"],
        },
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 422
    assert "spot_999" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_route_valid(client: AsyncClient):
    """新增路线：201 + id 自动生成（route_009）+ spot_ids 引用有效。"""
    resp = await client.post(
        "/api/admin/scenic/routes",
        json={
            "name": "禅意漫步线",
            "description": "中轴线轻松漫步",
            "spot_ids": ["spot_001", "spot_002"],
            "tags": ["休闲", "轻松"],
            "suitable_for": ["老年人"],
            "total_duration_minutes": 90,
            "difficulty": "轻松",
            "recommend_reason": "节奏舒缓",
        },
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] == "route_009"
    assert data["spot_ids"] == ["spot_001", "spot_002"]


# ── 更新 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_spot_partial(client: AsyncClient):
    """部分更新景点：仅更新传入字段，其余保持不变。"""
    resp = await client.put(
        "/api/admin/scenic/spots/spot_001",
        json={"description": "更新后的介绍"},
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "灵山大照壁"  # 未传字段不变
    assert data["description"] == "更新后的介绍"

    resp = await client.put(
        "/api/admin/scenic/spots/spot_nonexist",
        json={"description": "x"},
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_route_duplicate_name_422(client: AsyncClient):
    """更新路线名称与既有路线重复 422。"""
    resp = await client.put(
        "/api/admin/scenic/routes/route_002",
        json={"name": "历史文化深度游"},
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 422


# ── 删除 + RBAC ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_operator_forbidden_to_delete_scenic(client: AsyncClient):
    """operator 删除景点/路线 403（删除类敏感操作需 admin，与知识库一致）。"""
    resp = await client.delete(
        "/api/admin/scenic/spots/spot_001", headers=_auth(OPERATOR_TOKEN)
    )
    assert resp.status_code == 403
    resp = await client.delete(
        "/api/admin/scenic/routes/route_001", headers=_auth(OPERATOR_TOKEN)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_can_query_and_create(client: AsyncClient):
    """operator 可查询/新增景区数据（维持现状：仅删除类敏感操作需 admin）。"""
    resp = await client.get("/api/admin/scenic/spots", headers=_auth(OPERATOR_TOKEN))
    assert resp.status_code == 200
    resp = await client.post(
        "/api/admin/scenic/spots",
        json={"name": "operator 新增", "description": "允许"},
        headers=_auth(OPERATOR_TOKEN),
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_admin_delete_spot(client: AsyncClient):
    """admin 删除景点：成功 True；目标不存在 404（证明过了 RBAC）。"""
    resp = await client.delete(
        "/api/admin/scenic/spots/spot_010", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    # 删除生效
    resp = await client.get(
        "/api/admin/scenic/spots/spot_010", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 404
    # 已删除的 id 不复活
    resp = await client.delete(
        "/api/admin/scenic/spots/spot_010", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_delete_route(client: AsyncClient):
    """admin 删除路线：成功 True。"""
    resp = await client.delete(
        "/api/admin/scenic/routes/route_008", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 200
    resp = await client.get(
        "/api/admin/scenic/routes/route_008", headers=_auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 404


# ── 与 agent 工具数据一致（核心验收） ────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_tool_sees_page_written_data(client: AsyncClient, _redirect_data_dir):
    """页面写入 → agent 工具实时读到：新增景点后 search_spots 立即命中。"""
    from app.services.recommend_agent import search_spots

    # 页面新增景点
    resp = await client.post(
        "/api/admin/scenic/spots",
        json={
            "name": "灵山精舍",
            "description": "禅意酒店，含素斋与早课体验",
            "tags": ["休闲", "文化体验"],
            "duration_minutes": 60,
            "difficulty": "轻松",
            "location": "灵山胜境东侧",
            "highlights": ["素斋", "早课体验"],
        },
        headers=_auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 201

    # agent 结构化工具（读同一份 JSON 文件）立即命中——单一来源 = 文件
    result = search_spots.invoke({"query": "灵山精舍"})
    data = json.loads(result)
    assert isinstance(data, list)
    assert data[0]["name"] == "灵山精舍"
    assert data[0]["id"] == "spot_011"

    # 页面更新 → agent 工具读到新值
    await client.put(
        "/api/admin/scenic/spots/spot_011",
        json={"name": "灵山精舍·禅意酒店"},
        headers=_auth(ADMIN_TOKEN),
    )
    data = json.loads(search_spots.invoke({"query": "灵山精舍"}))
    assert data[0]["name"] == "灵山精舍·禅意酒店"

    # 页面删除 → agent 工具返回未找到
    await client.delete(
        "/api/admin/scenic/spots/spot_011", headers=_auth(ADMIN_TOKEN)
    )
    data = json.loads(search_spots.invoke({"query": "灵山精舍"}))
    assert isinstance(data, dict)
    assert "message" in data


@pytest.mark.asyncio
async def test_agent_route_tool_sees_updated_route(client: AsyncClient, _redirect_data_dir):
    """页面更新路线 → get_route_detail 工具实时读到新数据。"""
    from app.services.recommend_agent import get_route_detail

    await client.put(
        "/api/admin/scenic/routes/route_001",
        json={"name": "历史文化深度游（升级版）"},
        headers=_auth(ADMIN_TOKEN),
    )
    data = json.loads(get_route_detail.invoke({"route_id": "route_001"}))
    assert data["name"] == "历史文化深度游（升级版）"
    assert "spots_detail" in data


@pytest.mark.asyncio
async def test_data_files_stay_valid_json_after_crud(client: AsyncClient, _redirect_data_dir):
    """CRUD 全程数据文件保持合法 JSON（原子写入，无写一半的损坏文件）。"""
    for _ in range(3):
        resp = await client.post(
            "/api/admin/scenic/spots",
            json={"name": f"循环景点{_}", "description": "x"},
            headers=_auth(ADMIN_TOKEN),
        )
        assert resp.status_code == 201
    for spot_id in ("spot_011", "spot_012", "spot_013"):
        resp = await client.delete(
            f"/api/admin/scenic/spots/{spot_id}", headers=_auth(ADMIN_TOKEN)
        )
        assert resp.status_code == 200
    # 文件仍可被 agent 工具正常解析
    from app.services.recommend_agent import search_spots

    data = json.loads(search_spots.invoke({"query": ""}))
    assert isinstance(data, list)
    assert len(data) == 10


# ── 装配层工具缓存失效（数据一致性联动） ─────────────────────────────────


def test_invalidate_scenic_clears_static_tool_cache(monkeypatch):
    """invalidate_scenic：清除静态数据工具缓存条目（search_spots 等）。"""
    from app.services.deepagents.tool_cache import get_cache

    cache = get_cache()
    cache.invalidate_all()
    # 写入静态数据工具缓存（search_spots）与无关条目（记忆工具/知识检索）
    key_spots = cache.make_key("search_spots", ("灵山大佛",), {})
    key_kb = cache.make_key("search_knowledge", ("讲解词",), {})
    cache.set(key_spots, '{"name": "灵山大佛"}', ttl_seconds=3600)
    cache.set(key_kb, "kb", ttl_seconds=3600)
    assert cache.get(key_spots) is not None
    assert cache.get(key_kb) is not None

    # 景区数据变更 → 静态数据工具缓存整体失效
    scenic_service._invalidate_agent_tool_cache()
    assert cache.get(key_spots) is None  # 静态数据工具已失效
    assert cache.get(key_kb) is not None  # 知识检索缓存不受影响
    cache.invalidate_all()


@pytest.mark.asyncio
async def test_service_write_invalidates_tool_cache(client: AsyncClient, _redirect_data_dir):
    """页面写操作联动失效工具缓存：缓存命中旧数据后，写文件再查必读新值。"""
    from app.services.deepagents import tool_cache
    from app.services.recommend_agent import search_spots

    cache = tool_cache.get_cache()
    cache.invalidate_all()
    try:
        # 首次查询写入缓存（命中）——用装配层同款包装（裸工具不走缓存）
        wrapped = tool_cache.wrap_tool(search_spots, ttl_seconds=3600)
        wrapped.invoke({"query": "灵山大佛"})
        # 键与装配层调用形态一致：kwargs 形态（query=...）
        key = cache.make_key("search_spots", (), {"query": "灵山大佛"})
        assert cache.get(key) is not None

        # 页面更新该景点 → 服务层失效缓存
        await client.put(
            "/api/admin/scenic/spots/spot_007",
            json={"description": "世界最高露天青铜释迦牟尼立像（已更新）"},
            headers=_auth(ADMIN_TOKEN),
        )
        # 缓存已失效，再查必读文件新值
        assert cache.get(key) is None
    finally:
        cache.invalidate_all()
