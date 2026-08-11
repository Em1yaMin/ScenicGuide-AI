"""景区结构化数据（spots/routes）管理服务。

数据源：`app/data/spots.json` 与 `routes.json`——导览 agent 结构化工具
（search_spots / search_routes / get_spot_detail / get_route_detail /
filter_by_tags / recommend_routes）与首页活数据（home_service）、推荐问句
降级（recommend_service）的**单一数据来源**。

设计决策（知识库页与导览 agent 景区数据同步，方案②：页面管理直接读写
app/data JSON 并同步 agent 工具）：

- **单一来源 = JSON 文件**：agent 工具每次调用实时读文件（_load_json），
  管理端经本服务写文件后 agent 工具零改动即可读到最新数据（无第二份数据，
  不破坏 schema、不重构对话链路）
- **原子写入**：临时文件 + os.replace + fsync（与 rag.py 向量落盘
  `_atomic_write_json` 同模式），避免写一半的损坏文件被并发读取；
  模块级 threading.Lock 串行化写操作（防并发写互相覆盖）
- **装配层工具缓存联动失效**：deepagents 工具缓存把静态数据工具
  （search_spots 等）按 TTL 1h 缓存（A4 调优），景区数据变更后调用
  `invalidate_scenic()` 立即失效——否则管理端改完数据，agent 最长 1 小时
  读到旧结果，破坏「页面数据 = agent 数据」一致性承诺
- **业务校验**：景点名称唯一（防 agent 工具按名检索歧义）；路线 spot_ids
  必须引用已存在景点 id（防 agent 工具返回「未知景点」）；难度字段取值
  轻松/中等/挑战（与既有数据/工具提示词约定一致）
"""
import json
import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.schemas.scenic import (
    ScenicRoute,
    ScenicRouteCreate,
    ScenicRouteUpdate,
    ScenicSpot,
    ScenicSpotCreate,
    ScenicSpotUpdate,
)

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SPOTS_FILE = "spots.json"
_ROUTES_FILE = "routes.json"

# 写操作串行化（读不锁——agent 工具/首页每次读文件，原子替换保证读到
# 完整旧版本或完整新版本，无中间态）
_write_lock = threading.Lock()

# 景点/路线 id 前缀与编号位数（与既有数据格式 spot_001 / route_008 一致）
_ID_PREFIX = {"spot": "spot_", "route": "route_"}
_ID_WIDTH = 3

# 装配层静态数据工具集（deepagents/agent.py _CACHEABLE_TOOLS 同源）——
# 景区数据变更后整体失效（这些工具以 app/data/ 为数据源）
_STATIC_DATA_TOOLS = frozenset(
    {
        "search_spots",
        "search_routes",
        "get_spot_detail",
        "get_route_detail",
        "filter_by_tags",
        "recommend_routes",
    }
)


# ── 文件读写 ──────────────────────────────────────────────────────────────


def _load_json(filename: str) -> List[Dict[str, Any]]:
    """从 app/data/ 加载 JSON 数据文件（异常时返回空列表，不抛错）。"""
    path = _DATA_DIR / filename
    if not path.exists():
        logger.warning("景区数据文件不存在: %s", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("景区数据文件格式异常（期望数组）: %s", path)
            return []
        return data
    except json.JSONDecodeError:
        logger.exception("景区数据文件 JSON 解析失败: %s", path)
        return []


def _atomic_write_json(filename: str, items: List[Dict[str, Any]]) -> None:
    """原子写入 JSON 数据文件（临时文件 + os.replace + fsync）。"""
    path = _DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{filename}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _invalidate_agent_tool_cache() -> None:
    """景区数据变更后失效装配层静态数据工具缓存（防 agent 读旧数据）。"""
    try:
        from app.services.deepagents.tool_cache import get_cache

        get_cache().invalidate_scenic()
    except Exception as e:
        logger.warning("景区数据工具缓存失效失败（短 TTL 兜底）: %s", e)


# ── 公共校验辅助 ──────────────────────────────────────────────────────────


def _normalize_string_list(values: Optional[List[str]]) -> List[str]:
    """字符串列表归一化：去空白、去空串（标签/亮点/适合人群等）。"""
    if not values:
        return []
    cleaned: List[str] = []
    for v in values:
        if isinstance(v, str) and v.strip():
            cleaned.append(v.strip())
    return cleaned


def _next_id(items: List[Dict[str, Any]], prefix: str) -> str:
    """生成下一个自增 id（spot_011 / route_009 等，编号 = 现有最大值 + 1）。

    删除过条目后编号不会复用（取 max+1 而非 min 空洞），避免既有
    缓存/引用指向被删条目时误命中新条目。
    """
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)$")
    max_num = 0
    for item in items:
        m = pattern.match(str(item.get("id", "")))
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"{prefix}{max_num + 1:0{_ID_WIDTH}d}"


def _validate_difficulty(value: str) -> None:
    """难度字段取值校验（与既有数据约定一致）。"""
    if value not in ("轻松", "中等", "挑战"):
        raise ValueError("difficulty 仅支持 轻松/中等/挑战")


def _validate_unique_name(
    items: List[Dict[str, Any]], name: str, exclude_id: Optional[str] = None
) -> None:
    """名称唯一校验（排除自身，防 agent 工具按名检索歧义）。"""
    for item in items:
        if item.get("id") == exclude_id:
            continue
        if (item.get("name") or "").strip() == name.strip():
            raise ValueError(f"名称「{name}」已存在，请勿重复")


# ── 景点（spots） ────────────────────────────────────────────────────────


def list_spots() -> List[ScenicSpot]:
    """景点列表（数据文件原样顺序）。"""
    return [ScenicSpot.model_validate(s) for s in _load_json(_SPOTS_FILE)]


def get_spot(spot_id: str) -> Optional[ScenicSpot]:
    """按 id 查询景点；不存在返回 None。"""
    for s in _load_json(_SPOTS_FILE):
        if s.get("id") == spot_id:
            return ScenicSpot.model_validate(s)
    return None


def create_spot(request: ScenicSpotCreate) -> ScenicSpot:
    """新建景点（id 服务端生成；名称唯一校验；原子写入 + 工具缓存失效）。"""
    _validate_difficulty(request.difficulty)
    with _write_lock:
        items = _load_json(_SPOTS_FILE)
        _validate_unique_name(items, request.name)
        new_id = _next_id(items, _ID_PREFIX["spot"])
        spot = {
            "id": new_id,
            "name": request.name.strip(),
            "description": request.description.strip(),
            "tags": _normalize_string_list(request.tags),
            "duration_minutes": request.duration_minutes,
            "difficulty": request.difficulty,
            "location": request.location.strip(),
            "highlights": _normalize_string_list(request.highlights),
        }
        items.append(spot)
        _atomic_write_json(_SPOTS_FILE, items)
    _invalidate_agent_tool_cache()
    logger.info("景区数据-景点新增: id=%s name=%s", new_id, spot["name"])
    return ScenicSpot.model_validate(spot)


def update_spot(spot_id: str, request: ScenicSpotUpdate) -> Optional[ScenicSpot]:
    """部分更新景点（仅更新非 None 字段）；不存在返回 None。"""
    if request.difficulty is not None:
        _validate_difficulty(request.difficulty)
    with _write_lock:
        items = _load_json(_SPOTS_FILE)
        target = next((s for s in items if s.get("id") == spot_id), None)
        if target is None:
            return None
        if request.name is not None:
            _validate_unique_name(items, request.name, exclude_id=spot_id)
        if request.name is not None:
            target["name"] = request.name.strip()
        if request.description is not None:
            target["description"] = request.description.strip()
        if request.tags is not None:
            target["tags"] = _normalize_string_list(request.tags)
        if request.duration_minutes is not None:
            target["duration_minutes"] = request.duration_minutes
        if request.difficulty is not None:
            target["difficulty"] = request.difficulty
        if request.location is not None:
            target["location"] = request.location.strip()
        if request.highlights is not None:
            target["highlights"] = _normalize_string_list(request.highlights)
        _atomic_write_json(_SPOTS_FILE, items)
    _invalidate_agent_tool_cache()
    logger.info("景区数据-景点更新: id=%s", spot_id)
    return ScenicSpot.model_validate(target)


def delete_spot(spot_id: str) -> bool:
    """删除景点（成功 True，不存在 False）。"""
    with _write_lock:
        items = _load_json(_SPOTS_FILE)
        remaining = [s for s in items if s.get("id") != spot_id]
        if len(remaining) == len(items):
            return False
        _atomic_write_json(_SPOTS_FILE, remaining)
    _invalidate_agent_tool_cache()
    logger.info("景区数据-景点删除: id=%s", spot_id)
    return True


# ── 路线（routes） ────────────────────────────────────────────────────────


def _validate_route_spot_ids(spot_ids: List[str]) -> None:
    """路线 spot_ids 必须引用已存在景点 id（防 agent 工具返回「未知景点」）。"""
    if not spot_ids:
        return
    spot_ids_set = {s.get("id") for s in _load_json(_SPOTS_FILE)}
    unknown = [sid for sid in spot_ids if sid not in spot_ids_set]
    if unknown:
        raise ValueError(f"路线引用了不存在的景点 id: {', '.join(unknown)}")


def list_routes() -> List[ScenicRoute]:
    """路线列表（数据文件原样顺序）。"""
    return [ScenicRoute.model_validate(r) for r in _load_json(_ROUTES_FILE)]


def get_route(route_id: str) -> Optional[ScenicRoute]:
    """按 id 查询路线；不存在返回 None。"""
    for r in _load_json(_ROUTES_FILE):
        if r.get("id") == route_id:
            return ScenicRoute.model_validate(r)
    return None


def create_route(request: ScenicRouteCreate) -> ScenicRoute:
    """新建路线（id 服务端生成；名称唯一 + spot_ids 引用校验；原子写入）。"""
    _validate_difficulty(request.difficulty)
    spot_ids = _normalize_string_list(request.spot_ids)
    with _write_lock:
        items = _load_json(_ROUTES_FILE)
        _validate_unique_name(items, request.name)
        _validate_route_spot_ids(spot_ids)
        new_id = _next_id(items, _ID_PREFIX["route"])
        route = {
            "id": new_id,
            "name": request.name.strip(),
            "description": request.description.strip(),
            "spot_ids": spot_ids,
            "tags": _normalize_string_list(request.tags),
            "suitable_for": _normalize_string_list(request.suitable_for),
            "total_duration_minutes": request.total_duration_minutes,
            "difficulty": request.difficulty,
            "recommend_reason": request.recommend_reason.strip(),
        }
        items.append(route)
        _atomic_write_json(_ROUTES_FILE, items)
    _invalidate_agent_tool_cache()
    logger.info("景区数据-路线新增: id=%s name=%s", new_id, route["name"])
    return ScenicRoute.model_validate(route)


def update_route(route_id: str, request: ScenicRouteUpdate) -> Optional[ScenicRoute]:
    """部分更新路线（仅更新非 None 字段）；不存在返回 None。"""
    if request.difficulty is not None:
        _validate_difficulty(request.difficulty)
    spot_ids: Optional[List[str]] = None
    if request.spot_ids is not None:
        spot_ids = _normalize_string_list(request.spot_ids)
    with _write_lock:
        items = _load_json(_ROUTES_FILE)
        target = next((r for r in items if r.get("id") == route_id), None)
        if target is None:
            return None
        if request.name is not None:
            _validate_unique_name(items, request.name, exclude_id=route_id)
        if request.name is not None:
            target["name"] = request.name.strip()
        if request.description is not None:
            target["description"] = request.description.strip()
        if spot_ids is not None:
            _validate_route_spot_ids(spot_ids)
            target["spot_ids"] = spot_ids
        if request.tags is not None:
            target["tags"] = _normalize_string_list(request.tags)
        if request.suitable_for is not None:
            target["suitable_for"] = _normalize_string_list(request.suitable_for)
        if request.total_duration_minutes is not None:
            target["total_duration_minutes"] = request.total_duration_minutes
        if request.difficulty is not None:
            target["difficulty"] = request.difficulty
        if request.recommend_reason is not None:
            target["recommend_reason"] = request.recommend_reason.strip()
        _atomic_write_json(_ROUTES_FILE, items)
    _invalidate_agent_tool_cache()
    logger.info("景区数据-路线更新: id=%s", route_id)
    return ScenicRoute.model_validate(target)


def delete_route(route_id: str) -> bool:
    """删除路线（成功 True，不存在 False）。"""
    with _write_lock:
        items = _load_json(_ROUTES_FILE)
        remaining = [r for r in items if r.get("id") != route_id]
        if len(remaining) == len(items):
            return False
        _atomic_write_json(_ROUTES_FILE, remaining)
    _invalidate_agent_tool_cache()
    logger.info("景区数据-路线删除: id=%s", route_id)
    return True
