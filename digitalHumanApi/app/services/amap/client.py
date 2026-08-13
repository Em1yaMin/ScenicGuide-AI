"""高德 Web 服务客户端（批次8 B3：GPS/高德）。

- 仅依赖 httpx（项目已有依赖），无额外 SDK
- `AmapClient`：geocode（地理编码）/ regeo（逆地理编码，transit 城市推导
  内部辅助）/ route（路径规划 walking|driving|transit）
- 统一错误信封（`{"ok": false, "error": <错误码>, "message": ...}`）：
  任何异常（网络/超时/HTTP 错误/API 业务错误/响应解析失败/无 key/无结果）
  都转为结构化错误码返回，**不抛裸异常**；错误码契约见 doc/amap-gps-api.md
- key 读取优先级：构造参数 api_key > settings.AMAP_WEB_KEY（构造时未传
  key 则每次调用实时读取 settings，测试可 monkeypatch settings 模拟无 key）
- 测试注入：`_transport` 参数传 httpx.MockTransport（与 embedding.py 同模式）

高德 Web 服务端点（实测可用，learnings 2026-08-08）：
- 地理编码 `/v3/geocode/geo`：多候选返回（同名景点跨城市需消歧）
- 逆地理编码 `/v3/geocode/regeo`：坐标 → 城市/adcode
- 路径规划 `/v3/direction/{walking|driving|transit/integrated}`：
  步行/驾车返回 route.paths[0].steps[].instruction；公交（integrated）返回
  route.transits[0].segments[]（walking.steps + bus.buslines），需 city/cityd
  参数——本客户端自动经 regeo 推导（调用方无需传城市）
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── 错误码常量（权威契约 doc/amap-gps-api.md） ───────────────────────────

# AMAP_WEB_KEY 未配置
ERR_NOT_CONFIGURED = "amap_not_configured"
# 高德服务不可用：网络异常/超时/HTTP 错误/API 业务错误（status != "1"）/响应解析失败
ERR_UNAVAILABLE = "amap_unavailable"
# 地理编码请求成功但无候选结果
ERR_NO_RESULT = "amap_no_result"
# 参数非法（地址为空/坐标格式错误/mode 非法）
ERR_INVALID_PARAM = "amap_invalid_param"
# 路径规划失败（业务错误/无可行路径/transit 城市推导失败）
ERR_ROUTE_FAILED = "amap_route_failed"

# 高德 Web 服务端点
_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
_REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
_DIRECTION_URL = "https://restapi.amap.com/v3/direction/{mode}"

# 支持出行方式（transit 走 integrated 公交 API）
VALID_MODES = ("walking", "driving", "transit")

# 地理编码候选返回条数上限（多候选消歧：返回前 N 条含省/市/区县供模型判断）
MAX_CANDIDATES = 5

# 请求超时（秒）：高德 Web 服务延迟低，10s 足够（网络差时快速报错不拖对话）
DEFAULT_TIMEOUT = 10.0


def _error(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    """构造统一错误信封（ok=False + 错误码 + 人话说明）。"""
    return {"ok": False, "error": code, "message": message, **extra}


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    """构造统一成功信封（ok=True + data）。"""
    return {"ok": True, "data": data}


def _is_valid_location(value: Any) -> bool:
    """校验坐标字符串：'经度,纬度'（如 114.306967,30.545389），数值在合法范围。"""
    if not isinstance(value, str):
        return False
    parts = value.split(",")
    if len(parts) != 2:
        return False
    try:
        lng, lat = float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return False
    return -180.0 <= lng <= 180.0 and -90.0 <= lat <= 90.0


class AmapClient:
    """高德 Web 服务客户端（httpx，统一错误信封，不抛裸异常）。

    Args:
        api_key: 高德 Web 服务 key（AMAP_WEB_KEY）；None 时每次调用实时读
            settings.AMAP_WEB_KEY（测试可 monkeypatch settings 模拟无 key）
        timeout: 请求超时秒数
        _transport: 仅供测试注入 httpx 传输层（如 MockTransport）
    """

    backend_name = "amap"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        _transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._transport = _transport

    # ── 内部工具 ──────────────────────────────────────────────────────────

    def _resolve_key(self) -> str:
        """解析 key：构造参数 > settings.AMAP_WEB_KEY（实时读取）。"""
        if self._api_key is None:
            return (settings.AMAP_WEB_KEY or "").strip()
        return (self._api_key or "").strip()

    async def _get(
        self, url: str, params: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """GET 请求高德 API 并解析 JSON；任何异常返回 None（调用方转错误码）。

        网络异常（连接失败/超时/HTTP 错误码）与 JSON 解析失败统一在此
        收敛为 None，保证调用方只有「成功 dict / None」两条路径，不抛
        裸异常。httpx.HTTPError 为网络层异常超类（覆盖 RequestError 系
        与 HTTPStatusError——httpx 0.28 中二者平级，须用超类捕获）。
        """
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
            logger.warning("高德 Web 服务请求异常: url=%s err=%s", url, e)
            return None

    @staticmethod
    def _api_ok(data: Dict[str, Any]) -> bool:
        """高德 API 业务状态判定（status == "1" 表示成功）。"""
        return str(data.get("status", "")) == "1"

    # ── 地理编码（地址 → 经纬度候选） ────────────────────────────────────

    async def geocode(
        self, address: str, city: Optional[str] = None
    ) -> Dict[str, Any]:
        """地理编码：地址描述 → 经纬度候选列表（最多 MAX_CANDIDATES 条）。

        多候选消歧策略（实测"黄鹤楼"返回多城市同名）：返回候选列表
        （含省/市/区县与 formatted_address）让模型结合景区上下文选择，
        同时支持 `city` 参数（城市名/adcode）请求时缩小范围——两种方式
        组合为「更稳的方案」（船长决策，见 doc/amap-gps-api.md）。

        成功 data: {"address", "count", "candidates": [{"formatted_address",
        "location"（"经度,纬度"）, "province", "city", "district", "adcode"}]}
        """
        key = self._resolve_key()
        if not key:
            return _error(ERR_NOT_CONFIGURED, "高德地图服务未配置（AMAP_WEB_KEY 缺失）")
        address = (address or "").strip()
        if not address:
            return _error(ERR_INVALID_PARAM, "地址不能为空")
        params: Dict[str, str] = {"key": key, "address": address, "output": "JSON"}
        if city and str(city).strip():
            params["city"] = str(city).strip()
        data = await self._get(_GEOCODE_URL, params)
        if data is None:
            return _error(ERR_UNAVAILABLE, "高德地理编码服务暂时不可用")
        if not self._api_ok(data):
            info = str(data.get("info", "") or "")
            logger.warning("高德地理编码业务错误: info=%s", info)
            return _error(ERR_UNAVAILABLE, f"高德地理编码失败（{info}）")
        geocodes: List[Dict[str, Any]] = data.get("geocodes") or []
        if not geocodes:
            return _error(ERR_NO_RESULT, f"未找到「{address}」的位置信息")
        candidates: List[Dict[str, str]] = []
        for item in geocodes[:MAX_CANDIDATES]:
            candidates.append(
                {
                    "formatted_address": str(item.get("formatted_address") or ""),
                    "location": str(item.get("location") or ""),
                    "province": str(item.get("province") or ""),
                    "city": str(item.get("city") or ""),
                    "district": str(item.get("district") or ""),
                    "adcode": str(item.get("adcode") or ""),
                }
            )
        return _ok(
            {"address": address, "count": len(candidates), "candidates": candidates}
        )

    # ── 逆地理编码（坐标 → 城市，transit 城市推导内部辅助） ──────────────

    async def regeo(self, location: str) -> Dict[str, Any]:
        """逆地理编码：坐标 → 城市/adcode（供 transit 推导 city/cityd 参数）。

        成功 data: {"location", "formatted_address", "city", "city_adcode",
        "adcode"}；city_adcode 由区县 adcode 前 4 位 + "00" 推导
        （如 420102 → 420100 武汉市，兼容直辖市如 110101 → 110100）。
        """
        key = self._resolve_key()
        if not key:
            return _error(ERR_NOT_CONFIGURED, "高德地图服务未配置（AMAP_WEB_KEY 缺失）")
        if not _is_valid_location(location):
            return _error(ERR_INVALID_PARAM, "坐标格式应为 经度,纬度（如 114.306967,30.545389）")
        data = await self._get(
            _REGEO_URL, {"key": key, "location": location, "output": "JSON"}
        )
        if data is None:
            return _error(ERR_UNAVAILABLE, "高德逆地理编码服务暂时不可用")
        if not self._api_ok(data):
            info = str(data.get("info", "") or "")
            logger.warning("高德逆地理编码业务错误: info=%s", info)
            return _error(ERR_UNAVAILABLE, f"高德逆地理编码失败（{info}）")
        regeocode: Dict[str, Any] = data.get("regeocode") or {}
        component: Dict[str, Any] = regeocode.get("addressComponent") or {}
        adcode = str(component.get("adcode") or "")
        city = component.get("city") or ""
        # 直辖市 city 可能为空数组/空串：退化为 province 名（如"北京市"）
        if isinstance(city, list):
            city = "".join(str(c) for c in city)
        if not str(city).strip():
            city = str(component.get("province") or "")
        city_adcode = f"{adcode[:4]}00" if len(adcode) >= 4 else ""
        return _ok(
            {
                "location": location,
                "formatted_address": str(regeocode.get("formatted_address") or ""),
                "city": str(city or ""),
                "city_adcode": city_adcode,
                "adcode": adcode,
            }
        )

    # ── 路径规划（坐标对 → 路径） ────────────────────────────────────────

    async def route(
        self, origin: str, destination: str, mode: str = "walking"
    ) -> Dict[str, Any]:
        """路径规划：起点/终点坐标 + 出行方式 → distance/duration/steps。

        mode ∈ walking | driving | transit：
        - walking/driving：`/v3/direction/{mode}`，返回 route.paths[0]
          steps 含步行指令文本
        - transit：`/v3/direction/transit/integrated`（需 city/cityd 参数，
          自动经 regeo 推导起终点城市，调用方无需传）；返回
          route.transits[0]，归一化为统一 steps（步行指令 + 公交乘坐指令）

        成功 data: {"mode", "origin", "destination", "distance"（米）,
        "duration"（秒）, "steps": [{"instruction", "distance", "duration",
        "road", "polyline"}], "strategy"}

        每步 steps 增加 `polyline` 字段（高德原样透出坐标串
        "lng,lat;lng,lat;..."，供前端拼接完整路径画 AMap.Polyline
        实际折线，不再画起终点直线）；transit 归一化时步行段保留
        walking.steps 的 polyline、公交段可能为空串（AMAP busline 无
        polyline 时留空，前端无 polyline 时降级直线/文字路线）。
        """
        key = self._resolve_key()
        if not key:
            return _error(ERR_NOT_CONFIGURED, "高德地图服务未配置（AMAP_WEB_KEY 缺失）")
        if mode not in VALID_MODES:
            return _error(
                ERR_INVALID_PARAM,
                f"不支持的出行方式: {mode}（可选 {'/'.join(VALID_MODES)}）",
            )
        if not _is_valid_location(origin) or not _is_valid_location(destination):
            return _error(
                ERR_INVALID_PARAM,
                "起点/终点坐标格式应为 经度,纬度（如 114.306967,30.545389）",
            )
        params: Dict[str, str] = {
            "key": key,
            "origin": origin,
            "destination": destination,
            "output": "JSON",
            # extensions=all：三种模式统一携带——公交 integrated 默认 base 模式
            # 会把步行段 instruction 替换为十六进制标记（如「步行23米0x02」），
            # 带 extensions=all 才返回完整中文动作（实机测试问题2）；walking/
            # driving 同步带上保证分支一致
            "extensions": "all",
        }
        url = _DIRECTION_URL.format(mode=mode)
        if mode == "transit":
            url = _DIRECTION_URL.format(mode="transit/integrated")
            origin_city = await self._derive_city(origin)
            destination_city = await self._derive_city(destination)
            if not origin_city or not destination_city:
                return _error(
                    ERR_ROUTE_FAILED, "公交路线规划失败：无法确定起/终点城市"
                )
            params["city"] = origin_city
            params["cityd"] = destination_city
        data = await self._get(url, params)
        if data is None:
            return _error(ERR_UNAVAILABLE, "高德路径规划服务暂时不可用")
        if not self._api_ok(data):
            info = str(data.get("info", "") or "")
            logger.warning("高德路径规划业务错误: mode=%s info=%s", mode, info)
            return _error(ERR_ROUTE_FAILED, f"路径规划失败（{info}）")
        route_data: Dict[str, Any] = data.get("route") or {}
        if mode == "transit":
            normalized = self._normalize_transit(route_data)
            if normalized is None:
                return _error(ERR_ROUTE_FAILED, "未找到可行的公交路线")
            return _ok(
                {
                    "mode": mode,
                    "origin": origin,
                    "destination": destination,
                    **normalized,
                }
            )
        paths: List[Dict[str, Any]] = route_data.get("paths") or []
        if not paths:
            return _error(ERR_ROUTE_FAILED, "未找到可行路线")
        path: Dict[str, Any] = paths[0]
        steps = [
            {
                "instruction": str(s.get("instruction") or ""),
                "distance": str(s.get("distance") or ""),
                "duration": str(s.get("duration") or ""),
                "road": str(s.get("road") or ""),
                # 高德坐标串（"lng,lat;lng,lat;..."），原样透出供前端画实际路径
                "polyline": str(s.get("polyline") or ""),
            }
            for s in (path.get("steps") or [])
        ]
        return _ok(
            {
                "mode": mode,
                "origin": origin,
                "destination": destination,
                "distance": str(path.get("distance") or ""),
                "duration": str(path.get("duration") or ""),
                "steps": steps,
                "strategy": str(path.get("strategy") or ""),
            }
        )

    async def _derive_city(self, location: str) -> str:
        """推导坐标所在城市（transit 的 city/cityd 参数）：adcode 优先，城市名兜底。

        内部辅助：regeo 失败/异常时返回空串（调用方转 amap_route_failed），
        不抛异常。
        """
        try:
            result = await self.regeo(location)
        except Exception as e:
            logger.warning("transit 城市推导异常（降级）: location=%s err=%s", location, e)
            return ""
        if not result.get("ok"):
            return ""
        data = result.get("data") or {}
        return str(data.get("city_adcode") or data.get("city") or "").strip()

    @staticmethod
    def _normalize_transit(
        route_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """公交 integrated 响应归一化为统一 steps（与 walking/driving 同构）。

        AMAP transit 响应结构为 route.transits[0].segments[]，每段含
        walking（steps 指令）与 bus（buslines 数组）；归一化规则：
        - 步行段：透出 walking.steps[].instruction 与 polyline
        - 公交段：每段取首条 busline，合成「乘坐{线路名}（上车站 → 下车站）」
          指令（road 记为线路名）；polyline 尽量保留（busline 有
          polyline 则透出，无则留空——公交段可空，前端据此降级）
        无 transits 返回 None（调用方转 amap_route_failed）。
        """
        transits: List[Dict[str, Any]] = route_data.get("transits") or []
        if not transits:
            return None
        transit: Dict[str, Any] = transits[0]
        steps: List[Dict[str, str]] = []
        for segment in transit.get("segments") or []:
            walking: Dict[str, Any] = segment.get("walking") or {}
            for s in walking.get("steps") or []:
                steps.append(
                    {
                        "instruction": str(s.get("instruction") or "步行"),
                        "distance": str(s.get("distance") or ""),
                        "duration": str(s.get("duration") or ""),
                        "road": "",
                        # 步行段高德坐标串（"lng,lat;lng,lat;..."）原样透出
                        "polyline": str(s.get("polyline") or ""),
                    }
                )
            bus: Dict[str, Any] = segment.get("bus") or {}
            buslines: List[Dict[str, Any]] = bus.get("buslines") or []
            for bl in buslines[:1]:
                name = str(bl.get("name") or "公交")
                dep = bl.get("departure_stop") or {}
                arr = bl.get("arrival_stop") or {}
                steps.append(
                    {
                        "instruction": f"乘坐{name}"
                        f"（{str(dep.get('name') or '')} → {str(arr.get('name') or '')}）",
                        "distance": str(bl.get("distance") or ""),
                        "duration": str(bl.get("duration") or ""),
                        "road": name,
                        # 公交段 polyline 尽量保留（busline 无该字段时可空）
                        "polyline": str(bl.get("polyline") or ""),
                    }
                )
        return {
            "distance": str(transit.get("distance") or ""),
            "duration": str(transit.get("duration") or ""),
            "steps": steps,
            "strategy": str(route_data.get("strategy") or ""),
        }


# ── 模块级便捷函数（对话工具调用面；测试可 monkeypatch） ─────────────────


_client: Optional[AmapClient] = None


def _get_client() -> AmapClient:
    """懒加载单例客户端（key 实时读 settings，monkeypatch settings 即生效）。"""
    global _client
    if _client is None:
        _client = AmapClient()
    return _client


def reset_client() -> None:
    """重置单例客户端（测试用：换 transport/key 后重建）。"""
    global _client
    _client = None


async def geocode(address: str, city: Optional[str] = None) -> Dict[str, Any]:
    """模块级地理编码（默认客户端，统一错误信封）。"""
    return await _get_client().geocode(address, city)


async def regeo(location: str) -> Dict[str, Any]:
    """模块级逆地理编码（默认客户端，统一错误信封）。"""
    return await _get_client().regeo(location)


async def route(
    origin: str, destination: str, mode: str = "walking"
) -> Dict[str, Any]:
    """模块级路径规划（默认客户端，统一错误信封）。"""
    return await _get_client().route(origin, destination, mode)
