"""deepagents 位置/路线工具（批次8 B3：GPS/高德，装配进用户侧工具集）。

船长决策：演示主推「描述位置 → 地理编码 → 路径规划 → 地图展示」，
浏览器定位为辅（前端任务）。坐标全部来自高德 API（结构化零幻觉，
符合项目原则）；离线/异常直接报错误码（不做离线兜底）。

两工具（**管理员 agent 不装配**，见 admin_agent/tools.py 的 ADMIN_TOOLS）：

- `geocode_location(description, city=None)` — 位置描述 → 经纬度候选
  （最多 5 条含省/市/区县；同名地点跨城市时返回多候选供模型结合景区
  上下文选择，city 参数可缩小范围）
- `plan_route(origin, destination, mode)` — 坐标对 → 路径
  （distance/duration/steps 含步行指令文本；mode=walking|driving|transit）

**工具描述引导用法**（模型侧 schema 即契约）：先 geocode_location 获得
坐标（取候选的 location 字段），再 plan_route 规划路线——避免模型直接
拿地址字符串调 plan_route（高德方向 API 只收坐标）。

**错误码透出**：内部调用 `app.services.amap`（统一错误信封，见
doc/amap-gps-api.md），工具原样透出 `{"ok": false, "error": <错误码>,
"message": ...}` 的 JSON 字符串（不抛异常）；仅当服务层自身出现未预期
异常（防御性兜底）时包装为 amap_unavailable。模型看到错误码后如实转达，
不编造坐标/距离/路线。

**SSE 步骤标签**：`AMAP_STEP_LABELS` 由装配层（agent.py）合并进
`_STEP_LABELS`（"正在定位…"/"正在规划路线…"），步骤事件与既有
search_spots 等一致透出。
"""
import json
import logging
from typing import Optional

from langchain_core.tools import tool

from app.services import amap as amap_service

logger = logging.getLogger(__name__)

# 步骤展示文案（SSE step 事件透出，装配层合并进 _STEP_LABELS）
AMAP_STEP_LABELS = {
    "geocode_location": "正在定位…",
    "plan_route": "正在规划路线…",
}


def _to_json(obj) -> str:
    """工具返回值统一 JSON 字符串（ensure_ascii=False 保中文可读）。"""
    return json.dumps(obj, ensure_ascii=False)


@tool
async def geocode_location(description: str, city: Optional[str] = None) -> str:
    """把位置描述转换为经纬度坐标（第一步：先定位，后规划路线）。

    当用户给出一个位置描述（如"黄鹤楼""灵山景区东门""最近的停车场"）而
    当前对话中还没有对应坐标时调用；返回最多 5 个候选（含省/市/区县与
    formatted_address）。同名地点跨城市时高德会返回多个候选——请结合
    景区上下文选择最匹配的一个（如景区在武汉就选城市为"武汉市"的候选），
    或在 city 参数传入城市名/adcode 缩小范围。

    返回 JSON 成功信封：{"ok": true, "data": {"candidates": [{"location":
    "经度,纬度", "formatted_address": ..., "province": ..., "city": ...,
    "district": ..., "adcode": ...}, ...]}}；失败为错误信封 {"ok": false,
    "error": "amap_*", "message": "..."}——如实转达，不要编造坐标。"""
    try:
        result = await amap_service.geocode(description, city)
    except Exception as e:
        # 防御性兜底：服务层应返回错误信封不抛异常；意外异常也转错误码
        logger.warning("geocode_location 工具异常（降级错误码）: %s", e)
        return _to_json(
            {
                "ok": False,
                "error": "amap_unavailable",
                "message": "位置解析服务暂时不可用",
            }
        )
    return _to_json(result)


@tool
async def plan_route(
    origin: str, destination: str, mode: str = "walking"
) -> str:
    """规划两个坐标之间的出行路线（第二步：先 geocode_location 获得坐标）。

    origin/destination 为"经度,纬度"坐标，必须来自 geocode_location 返回
    候选的 location 字段（高德方向 API 只接受坐标，不要传地址文本）；
    mode 为出行方式：walking（步行）/ driving（驾车）/ transit（公交，
    自动确定起终点城市，无需额外参数）。

    返回 JSON 成功信封：{"ok": true, "data": {"distance": 米, "duration":
    秒, "steps": [{"instruction": 步行/乘车指令文本, ...}], "origin": ...,
    "destination": ..., "mode": ...}}；失败为错误信封 {"ok": false,
    "error": "amap_*", "message": "..."}——如实转达，不要编造路线。"""
    try:
        result = await amap_service.route(origin, destination, mode)
    except Exception as e:
        # 防御性兜底：服务层应返回错误信封不抛异常；意外异常也转错误码
        logger.warning("plan_route 工具异常（降级错误码）: %s", e)
        return _to_json(
            {
                "ok": False,
                "error": "amap_unavailable",
                "message": "路径规划服务暂时不可用",
            }
        )
    return _to_json(result)
