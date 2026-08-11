"""首页活数据接口测试：数据来自 app/data/*.json，接口公开可访问。"""
import json
from pathlib import Path
from unittest.mock import mock_open

import pytest
from httpx import AsyncClient

import app.services.home_service as home_service

# 与真实数据源结构一致的样本（测试不依赖真实文件内容）
SAMPLE_SPOTS = [
    {
        "id": "spot_001",
        "name": "灵山大照壁",
        "description": "华夏第一壁，景区入口标志性景观",
        "tags": ["历史人文"],
        "duration_minutes": 30,
        "difficulty": "轻松",
        "location": "景区入口处，面朝太湖",
        "highlights": ["赵朴初题字"],
    },
    {
        "id": "spot_002",
        "name": "九龙灌浴",
        "description": "花开见佛的动态群雕景观",
        "tags": ["历史人文", "演艺"],
        "duration_minutes": 60,
        "difficulty": "轻松",
        "location": "菩提大道北端",
        "highlights": ["圣水祈福"],
    },
]

SAMPLE_ROUTES = [
    {
        "id": "route_001",
        "name": "历史文化深度游",
        "description": "灵山文化精华路线",
        "spot_ids": ["spot_001", "spot_002"],
        "tags": ["历史人文", "经典"],
        "suitable_for": ["情侣"],
        "total_duration_minutes": 360,
        "difficulty": "中等",
        "recommend_reason": "核心景观全覆盖",
    }
]


def _patch_data(monkeypatch, spots, routes):
    """monkeypatch 数据源读取，返回 (spots_path, routes_path)。"""
    calls = {}

    def fake_load(filename: str) -> list:
        calls[filename] = True
        if filename == "spots.json":
            return spots
        if filename == "routes.json":
            return routes
        return []

    monkeypatch.setattr(home_service, "_load_json", fake_load)
    return calls


@pytest.mark.asyncio
async def test_highlights_returns_spots_and_routes(client: AsyncClient, monkeypatch):
    """首页活数据：返回热门景点 + 推荐路线（默认各 3 条上限）。"""
    calls = _patch_data(monkeypatch, SAMPLE_SPOTS, SAMPLE_ROUTES)

    resp = await client.get("/api/v1/home/highlights")
    assert resp.status_code == 200
    data = resp.json()
    assert [s["name"] for s in data["spots"]] == ["灵山大照壁", "九龙灌浴"]
    assert data["routes"][0]["name"] == "历史文化深度游"
    assert data["routes"][0]["spot_ids"] == ["spot_001", "spot_002"]
    # 每次请求实时读取数据文件
    assert calls.get("spots.json") is True
    assert calls.get("routes.json") is True


@pytest.mark.asyncio
async def test_highlights_limit_params(client: AsyncClient, monkeypatch):
    """limit 参数生效：spots_limit/routes_limit 截断返回条数。"""
    _patch_data(monkeypatch, SAMPLE_SPOTS, SAMPLE_ROUTES)

    resp = await client.get(
        "/api/v1/home/highlights",
        params={"spots_limit": 1, "routes_limit": 1},
    )
    data = resp.json()
    assert len(data["spots"]) == 1
    assert data["spots"][0]["name"] == "灵山大照壁"
    assert len(data["routes"]) == 1


@pytest.mark.asyncio
async def test_highlights_empty_data(client: AsyncClient, monkeypatch):
    """数据文件缺失/为空时返回空列表，不报错。"""
    _patch_data(monkeypatch, [], [])

    resp = await client.get("/api/v1/home/highlights")
    assert resp.status_code == 200
    data = resp.json()
    assert data["spots"] == []
    assert data["routes"] == []


@pytest.mark.asyncio
async def test_highlights_no_auth_required(client: AsyncClient, monkeypatch):
    """首页活数据为公开接口，无需鉴权。"""
    _patch_data(monkeypatch, SAMPLE_SPOTS, SAMPLE_ROUTES)
    resp = await client.get("/api/v1/home/highlights")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_load_json_missing_file(tmp_path, monkeypatch):
    """数据文件不存在时 _load_json 返回空列表并告警（不抛异常）。"""
    monkeypatch.setattr(home_service, "_DATA_DIR", tmp_path / "nonexistent")
    assert home_service._load_json("spots.json") == []


@pytest.mark.asyncio
async def test_load_json_invalid_json(monkeypatch):
    """JSON 解析失败时返回空列表。"""
    m = mock_open(read_data="{invalid json")
    monkeypatch.setattr(home_service, "_DATA_DIR", Path("/some/dir"))
    monkeypatch.setattr("builtins.open", m)
    assert home_service._load_json("spots.json") == []
