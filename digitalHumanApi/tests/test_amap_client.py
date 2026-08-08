"""高德 Web 服务封装（批次8 B3：GPS/高德）——客户端单测。

覆盖（httpx.MockTransport 模拟，不发真实请求）：
1. geocode 成功：多候选解析（字段齐全/顺序保留/count）、候选条数上限
   （超过 MAX_CANDIDATES 截断）、city 参数透传（请求 URL 含 city）
2. geocode 失败路径：无 key（amap_not_configured，不发请求）、地址为空
   （amap_invalid_param）、无结果（amap_no_result）、API 业务错误
   （amap_unavailable 含 info）、网络异常（amap_unavailable）、HTTP 错误、
   响应解析失败
3. regeo 成功（city/city_adcode 推导，直辖市退化）与失败（无 key/业务错误）
4. route 成功：walking/driving（paths[0].steps 指令解析）、transit
   （自动 regeo 推导 city/cityd + transits 归一化：步行指令 + 公交合成指令）
5. route 失败路径：无 key、mode 非法、坐标非法（amap_invalid_param）、
   业务错误/无路径（amap_route_failed）、网络异常（amap_unavailable）、
   transit 城市推导失败（amap_route_failed）
6. 模块级函数：settings.AMAP_WEB_KEY 联动（无 key → amap_not_configured）、
   单例重置
"""

import httpx

from app.core.config import settings
from app.services import amap
from app.services.amap.client import (
    ERR_INVALID_PARAM,
    ERR_NOT_CONFIGURED,
    ERR_NO_RESULT,
    ERR_ROUTE_FAILED,
    ERR_UNAVAILABLE,
    MAX_CANDIDATES,
    AmapClient,
)

GEO_OK = {
    "status": "1",
    "info": "OK",
    "geocodes": [
        {
            "formatted_address": "湖北省武汉市武昌区黄鹤楼",
            "location": "114.306967,30.545389",
            "province": "湖北省",
            "city": "武汉市",
            "district": "武昌区",
            "adcode": "420106",
        },
        {
            "formatted_address": "四川省成都市新津区黄鹤楼",
            "location": "103.818,30.410",
            "province": "四川省",
            "city": "成都市",
            "district": "新津区",
            "adcode": "510132",
        },
    ],
}

WALKING_ROUTE_OK = {
    "status": "1",
    "info": "OK",
    "route": {
        "origin": "114.306967,30.545389",
        "destination": "114.320,30.560",
        "paths": [
            {
                "distance": "1262",
                "duration": "908",
                "strategy": "最短距离",
                "steps": [
                    {
                        "instruction": "步行向南出发，进入黄鹤楼东路",
                        "road": "黄鹤楼东路",
                        "distance": "10",
                        "duration": "8",
                    },
                    {
                        "instruction": "左转进入武昌路",
                        "road": "武昌路",
                        "distance": "1252",
                        "duration": "900",
                    },
                ],
            }
        ],
    },
}

TRANSIT_ROUTE_OK = {
    "status": "1",
    "info": "OK",
    "route": {
        "origin": "114.306967,30.545389",
        "destination": "114.320,30.560",
        "transits": [
            {
                "distance": "5400",
                "duration": "2400",
                "segments": [
                    {
                        "walking": {
                            "steps": [
                                {"instruction": "步行至黄鹤楼站", "distance": "300", "duration": "240"}
                            ]
                        },
                        "bus": {
                            "buslines": [
                                {
                                    "name": "804路",
                                    "departure_stop": {"name": "黄鹤楼站"},
                                    "arrival_stop": {"name": "武昌火车站"},
                                    "distance": "5100",
                                    "duration": "2160",
                                }
                            ]
                        },
                    }
                ],
            }
        ],
        "strategy": "推荐路线",
    },
}

REGEO_OK = {
    "status": "1",
    "info": "OK",
    "regeocode": {
        "formatted_address": "湖北省武汉市武昌区黄鹤楼",
        "addressComponent": {
            "province": "湖北省",
            "city": "武汉市",
            "district": "武昌区",
            "adcode": "420106",
        },
    },
}


def _json_handler(payload):
    """MockTransport handler：返回固定 JSON 响应。"""

    def handler(request):
        return httpx.Response(200, json=payload)

    return handler


def _url_params(request: httpx.Request) -> dict:
    """提取请求 query 参数（供断言 city 等透传）。"""
    return dict(request.url.params)


# ── geocode 成功路径 ──────────────────────────────────────────────────────


async def test_geocode_success_multi_candidates():
    """多候选返回：字段齐全/顺序保留/count 正确。"""
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(GEO_OK)))
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is True
    data = result["data"]
    assert data["address"] == "黄鹤楼"
    assert data["count"] == 2
    assert [c["formatted_address"] for c in data["candidates"]] == [
        "湖北省武汉市武昌区黄鹤楼",
        "四川省成都市新津区黄鹤楼",
    ]
    first = data["candidates"][0]
    assert first["location"] == "114.306967,30.545389"
    assert first["province"] == "湖北省"
    assert first["city"] == "武汉市"
    assert first["district"] == "武昌区"
    assert first["adcode"] == "420106"


async def test_geocode_candidates_limited_to_max():
    """候选条数上限：超过 MAX_CANDIDATES 只返回前 5 条。"""
    geocodes = []
    for i in range(8):
        geocodes.append(
            {"formatted_address": f"城市{i}黄鹤楼", "location": f"11{i},30.5",
             "province": "省", "city": f"市{i}", "district": "区", "adcode": f"42{i:04d}"}
        )
    payload = {"status": "1", "info": "OK", "geocodes": geocodes}
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(payload)))
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is True
    assert len(result["data"]["candidates"]) == MAX_CANDIDATES == 5
    assert result["data"]["count"] == 5


async def test_geocode_city_param_passed():
    """city 参数透传：请求 URL 含 city（城市名/adcode 消歧）。"""
    seen = {}

    def handler(request):
        seen["params"] = _url_params(request)
        return httpx.Response(200, json=GEO_OK)

    client = AmapClient(api_key="k", _transport=httpx.MockTransport(handler))
    await client.geocode("黄鹤楼", city="武汉")
    assert seen["params"]["address"] == "黄鹤楼"
    assert seen["params"]["city"] == "武汉"
    assert seen["params"]["key"] == "k"


# ── geocode 失败路径（错误码契约） ───────────────────────────────────────


async def test_geocode_no_key_not_configured():
    """无 key：amap_not_configured，且不发任何请求。"""

    def handler(request):
        raise AssertionError("无 key 不应发起请求")

    client = AmapClient(api_key="", _transport=httpx.MockTransport(handler))
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is False
    assert result["error"] == ERR_NOT_CONFIGURED
    assert "AMAP_WEB_KEY" in result["message"]


async def test_geocode_empty_address_invalid_param():
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(GEO_OK)))
    result = await client.geocode("   ")
    assert result["ok"] is False
    assert result["error"] == ERR_INVALID_PARAM


async def test_geocode_no_result():
    payload = {"status": "1", "info": "OK", "geocodes": []}
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(payload)))
    result = await client.geocode("不存在的地址xyz")
    assert result["ok"] is False
    assert result["error"] == ERR_NO_RESULT


async def test_geocode_business_error():
    """高德 API 业务错误（status=0）：amap_unavailable 且 message 含 info。"""
    payload = {"status": "0", "info": "INVALID_USER_KEY"}
    client = AmapClient(api_key="bad", _transport=httpx.MockTransport(_json_handler(payload)))
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is False
    assert result["error"] == ERR_UNAVAILABLE
    assert "INVALID_USER_KEY" in result["message"]


async def test_geocode_network_error():
    """网络异常（连接失败）：amap_unavailable，不抛裸异常。"""

    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = AmapClient(api_key="k", _transport=httpx.MockTransport(handler))
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is False
    assert result["error"] == ERR_UNAVAILABLE


async def test_geocode_http_error():
    """HTTP 5xx：amap_unavailable。"""
    client = AmapClient(
        api_key="k", _transport=httpx.MockTransport(lambda req: httpx.Response(502))
    )
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is False
    assert result["error"] == ERR_UNAVAILABLE


async def test_geocode_invalid_json():
    """响应非 JSON：amap_unavailable。"""
    client = AmapClient(
        api_key="k", _transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html>"))
    )
    result = await client.geocode("黄鹤楼")
    assert result["ok"] is False
    assert result["error"] == ERR_UNAVAILABLE


# ── regeo（transit 城市推导内部辅助） ────────────────────────────────────


async def test_regeo_success_city_adcode_derived():
    """逆地理编码：city_adcode 由区县 adcode 前 4 位 + '00' 推导（420106 → 420100）。"""
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(REGEO_OK)))
    result = await client.regeo("114.306967,30.545389")
    assert result["ok"] is True
    data = result["data"]
    assert data["city"] == "武汉市"
    assert data["city_adcode"] == "420100"
    assert data["adcode"] == "420106"


async def test_regeo_municipality_city_fallback():
    """直辖市（city 为空数组）：退化为 province 名。"""
    payload = {
        "status": "1",
        "info": "OK",
        "regeocode": {
            "formatted_address": "北京市东城区",
            "addressComponent": {
                "province": "北京市",
                "city": [],
                "district": "东城区",
                "adcode": "110101",
            },
        },
    }
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(payload)))
    result = await client.regeo("116.418,39.917")
    assert result["ok"] is True
    assert result["data"]["city"] == "北京市"
    assert result["data"]["city_adcode"] == "110100"


async def test_regeo_no_key():
    client = AmapClient(api_key="", _transport=httpx.MockTransport(_json_handler(REGEO_OK)))
    result = await client.regeo("114.3,30.5")
    assert result["error"] == ERR_NOT_CONFIGURED


# ── route 成功路径 ────────────────────────────────────────────────────────


async def test_route_walking_success():
    """步行路径规划：paths[0].steps 指令文本解析，distance/duration 透出。"""
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(WALKING_ROUTE_OK)))
    result = await client.route("114.306967,30.545389", "114.320,30.560", "walking")
    assert result["ok"] is True
    data = result["data"]
    assert data["mode"] == "walking"
    assert data["distance"] == "1262"
    assert data["duration"] == "908"
    assert data["strategy"] == "最短距离"
    assert [s["instruction"] for s in data["steps"]] == [
        "步行向南出发，进入黄鹤楼东路",
        "左转进入武昌路",
    ]
    assert data["steps"][0]["road"] == "黄鹤楼东路"


async def test_route_driving_success():
    """驾车路径规划：同 walking 结构。"""
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(WALKING_ROUTE_OK)))
    result = await client.route("114.306967,30.545389", "114.320,30.560", "driving")
    assert result["ok"] is True
    assert result["data"]["mode"] == "driving"
    assert result["data"]["steps"]


async def test_route_transit_success():
    """公交路径规划：自动 regeo 推导 city/cityd + transits 归一化。

    - 请求先发两次 regeo（起/终点），再发 transit/integrated，URL 含
      city/cityd=420100（regeo adcode 推导）
    - 归一化 steps：步行指令 + 公交合成指令「乘坐804路（黄鹤楼站 → 武昌火车站）」
    """
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "regeo" in str(request.url):
            return httpx.Response(200, json=REGEO_OK)
        return httpx.Response(200, json=TRANSIT_ROUTE_OK)

    client = AmapClient(api_key="k", _transport=httpx.MockTransport(handler))
    result = await client.route("114.306967,30.545389", "114.320,30.560", "transit")
    assert result["ok"] is True, result
    assert len(calls) == 3
    transit_call = [u for u in calls if "transit/integrated" in u][0]
    params = dict(httpx.URL(transit_call).params)
    assert params["city"] == "420100"
    assert params["cityd"] == "420100"
    data = result["data"]
    assert data["mode"] == "transit"
    assert data["distance"] == "5400"
    assert data["duration"] == "2400"
    assert [s["instruction"] for s in data["steps"]] == [
        "步行至黄鹤楼站",
        "乘坐804路（黄鹤楼站 → 武昌火车站）",
    ]
    assert data["steps"][1]["road"] == "804路"


async def test_route_transit_no_transits_failed():
    """公交无可行路线（transits 空）：amap_route_failed。"""

    def handler(request):
        if "regeo" in str(request.url):
            return httpx.Response(200, json=REGEO_OK)
        return httpx.Response(200, json={"status": "1", "info": "OK", "route": {"transits": []}})

    client = AmapClient(api_key="k", _transport=httpx.MockTransport(handler))
    result = await client.route("114.3,30.5", "114.32,30.56", "transit")
    assert result["ok"] is False
    assert result["error"] == ERR_ROUTE_FAILED


# ── route 失败路径（错误码契约） ─────────────────────────────────────────


async def test_route_no_key():
    client = AmapClient(api_key="", _transport=httpx.MockTransport(_json_handler(WALKING_ROUTE_OK)))
    result = await client.route("114.3,30.5", "114.32,30.56")
    assert result["error"] == ERR_NOT_CONFIGURED


async def test_route_invalid_mode():
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(WALKING_ROUTE_OK)))
    result = await client.route("114.3,30.5", "114.32,30.56", "flying")
    assert result["ok"] is False
    assert result["error"] == ERR_INVALID_PARAM
    assert "walking" in result["message"]


async def test_route_invalid_coordinate():
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(WALKING_ROUTE_OK)))
    # 地址文本/单值/越界坐标均非法
    for bad in ("黄鹤楼", "114.3", "200,30.5", "abc,def"):
        result = await client.route(bad, "114.32,30.56")
        assert result["error"] == ERR_INVALID_PARAM, bad
    result = await client.route("114.3,30.5", "")
    assert result["error"] == ERR_INVALID_PARAM


async def test_route_business_error():
    payload = {"status": "0", "info": "NO_ROUTE"}
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(payload)))
    result = await client.route("114.3,30.5", "114.32,30.56")
    assert result["ok"] is False
    assert result["error"] == ERR_ROUTE_FAILED
    assert "NO_ROUTE" in result["message"]


async def test_route_no_paths():
    payload = {"status": "1", "info": "OK", "route": {"paths": []}}
    client = AmapClient(api_key="k", _transport=httpx.MockTransport(_json_handler(payload)))
    result = await client.route("114.3,30.5", "114.32,30.56")
    assert result["error"] == ERR_ROUTE_FAILED


async def test_route_network_error():
    def handler(request):
        raise httpx.ConnectError("timeout")

    client = AmapClient(api_key="k", _transport=httpx.MockTransport(handler))
    result = await client.route("114.3,30.5", "114.32,30.56")
    assert result["ok"] is False
    assert result["error"] == ERR_UNAVAILABLE


async def test_route_transit_city_derivation_failed():
    """transit 城市推导失败（regeo 业务错误）：amap_route_failed 不抛异常。"""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"status": "0", "info": "INVALID_USER_KEY"})

    client = AmapClient(api_key="k", _transport=httpx.MockTransport(handler))
    result = await client.route("114.3,30.5", "114.32,30.56", "transit")
    assert result["ok"] is False
    assert result["error"] == ERR_ROUTE_FAILED
    assert "城市" in result["message"]
    # 仅 regeo 请求，未发 transit 请求
    assert calls["n"] == 2


# ── 模块级函数（对话工具调用面） ─────────────────────────────────────────


async def test_module_geocode_no_key_from_settings(monkeypatch):
    """settings.AMAP_WEB_KEY 为空：模块级 geocode 返回 amap_not_configured。"""
    monkeypatch.setattr(settings, "AMAP_WEB_KEY", "")
    amap.reset_client()
    result = await amap.geocode("黄鹤楼")
    assert result["ok"] is False
    assert result["error"] == ERR_NOT_CONFIGURED


async def test_module_geocode_uses_settings_key(monkeypatch):
    """settings.AMAP_WEB_KEY 配置后：模块级函数正常请求（单例实时读 key）。"""
    monkeypatch.setattr(settings, "AMAP_WEB_KEY", "settings-key")
    amap.reset_client()
    seen = {}

    class _FakeClient:
        async def geocode(self, address, city=None):
            seen["address"] = address
            return {"ok": True, "data": {"candidates": [{"location": "114.3,30.5"}]}}

    monkeypatch.setattr("app.services.amap.client._get_client", lambda: _FakeClient())
    result = await amap.geocode("黄鹤楼")
    assert result["ok"] is True
    assert seen["address"] == "黄鹤楼"


async def test_module_route_and_regeo_exposed(monkeypatch):
    """模块级 route/regeo 存在且走默认客户端（工具调用面完整）。"""
    monkeypatch.setattr(settings, "AMAP_WEB_KEY", "")
    amap.reset_client()
    assert (await amap.route("114.3,30.5", "114.32,30.56"))["error"] == ERR_NOT_CONFIGURED
    assert (await amap.regeo("114.3,30.5"))["error"] == ERR_NOT_CONFIGURED


async def test_reset_client_rebuilds():
    """reset_client 后 _get_client 返回新实例（旧实例不残留）。"""
    from app.services.amap import client as amap_client_mod

    amap.reset_client()
    c1 = amap_client_mod._get_client()
    amap.reset_client()
    c2 = amap_client_mod._get_client()
    assert c1 is not c2
