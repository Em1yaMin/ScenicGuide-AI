"""高德 Web 服务封装（批次8 B3：GPS/高德）。

船长决策：演示主推「描述位置 → 地理编码 → 路径规划 → 地图展示」
（浏览器定位为辅，留给前端任务）；离线/异常直接报对应错误码（不做离线
兜底）；坐标全部来自高德 API（结构化零幻觉，符合项目原则）。key 读取
`AMAP_WEB_KEY`（config.py），不入库。

对外接口（全部返回**统一错误信封**，不抛裸异常）：

- `geocode(address, city=None)` — 地理编码：地址描述 → 经纬度候选列表
  （最多 5 条，含省/市/区县与 formatted_address；同名地点跨城市时返回
  多候选供模型结合景区上下文消歧，city 参数（城市名/adcode）可缩小范围）
- `route(origin, destination, mode)` — 路径规划：mode ∈ walking/driving/
  transit；返回 distance（米）/duration（秒）/steps（含步行指令文本）；
  transit 模式自动经逆地理编码确定起终点城市（调用方无需传城市参数）
- `regeo(location)` — 逆地理编码（transit 城市推导的内部辅助，不对
  agent 暴露）

**错误码契约（权威文档 doc/amap-gps-api.md）**：

成功：`{"ok": true, "data": {...}}`
失败：`{"ok": false, "error": "<错误码>", "message": "<人话说明>"}`

| 错误码 | 含义 |
|--------|------|
| `amap_not_configured` | AMAP_WEB_KEY 未配置（部署缺 key，不阻断对话） |
| `amap_unavailable` | 高德服务不可用（网络/超时/HTTP 错误/API 业务错误/响应解析失败） |
| `amap_no_result` | 地理编码请求成功但无候选结果 |
| `amap_invalid_param` | 参数非法（地址为空/坐标格式错误/mode 不在 walking、driving、transit） |
| `amap_route_failed` | 路径规划失败（业务错误/无可行路径/transit 城市推导失败） |

工具层（deepagents/amap_tools.py）原样透出该信封，模型如实转达错误码，
不编造坐标/距离/路线。
"""
from app.services.amap.client import (
    ERR_INVALID_PARAM,
    ERR_NOT_CONFIGURED,
    ERR_NO_RESULT,
    ERR_ROUTE_FAILED,
    ERR_UNAVAILABLE,
    MAX_CANDIDATES,
    VALID_MODES,
    AmapClient,
    geocode,
    regeo,
    reset_client,
    route,
)

__all__ = [
    "AmapClient",
    "ERR_INVALID_PARAM",
    "ERR_NOT_CONFIGURED",
    "ERR_NO_RESULT",
    "ERR_ROUTE_FAILED",
    "ERR_UNAVAILABLE",
    "MAX_CANDIDATES",
    "VALID_MODES",
    "geocode",
    "regeo",
    "reset_client",
    "route",
]
