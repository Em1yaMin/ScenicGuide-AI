"""首页活数据服务：从 app/data/*.json 读取热门景点/推荐路线。

每次请求实时读文件（无缓存），保证「管理端改数据 → 首页实时变化」
的活数据演示效果；文件缺失/解析失败时降级为空列表并告警日志。
"""
import json
import logging
from pathlib import Path

from app.schemas.home import HomeHighlightsResponse, HomeRoute, HomeSpot

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_json(filename: str) -> list[dict]:
    """从 app/data/ 加载 JSON 数据文件（异常时返回空列表）。"""
    path = _DATA_DIR / filename
    if not path.exists():
        logger.warning("数据文件不存在: %s", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.warning("数据文件格式异常（期望数组）: %s", path)
            return []
        return data
    except json.JSONDecodeError:
        logger.exception("数据文件 JSON 解析失败: %s", path)
        return []


async def get_home_highlights(
    spots_limit: int = 3,
    routes_limit: int = 3,
) -> HomeHighlightsResponse:
    """读取首页热门景点与推荐路线。

    Args:
        spots_limit: 热门景点条数上限（默认 3，与首页卡片数一致）
        routes_limit: 推荐路线条数上限（默认 3）
    """
    spots = _load_json("spots.json")[:spots_limit]
    routes = _load_json("routes.json")[:routes_limit]

    return HomeHighlightsResponse(
        spots=[HomeSpot.model_validate(s) for s in spots],
        routes=[HomeRoute.model_validate(r) for r in routes],
    )
