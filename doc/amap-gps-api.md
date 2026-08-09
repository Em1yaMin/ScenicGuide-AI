# 高德 GPS 接口契约（批次8 B3）

演示主推「**描述位置 → 地理编码 → 路径规划 → 地图展示**」（浏览器定位为辅，
前端任务）；坐标全部来自高德 API（结构化零幻觉，符合项目原则）；离线/异常
直接报对应错误码，**不做离线兜底**（船长决策 2026-08-08）。

## 凭据

- `AMAP_WEB_KEY`（后端，Web 服务 key）：`digitalHumanApi/.env` / 部署环境 /
  docker-compose 透传；**不入库**（仓库内 `.env.example` 仅占位符）。
- 与前端地图渲染的 JS API key（`NEXT_PUBLIC_AMAP_JS_KEY` +
  `NEXT_PUBLIC_AMAP_SECURITY_CODE`，ADH `live2d-frontend/web/.env`，gitignored
  **不入库**；任务约定名为 `VITE_AMAP_JS_KEY`/`VITE_AMAP_SECURITY_CODE`，
  Next.js 客户端仅注入 `NEXT_PUBLIC_` 前缀变量故改名，代码 `getAmapKeys`
  对两套命名均兜底读取）是两套独立凭据，互不通用。
- 未配置 key：工具返回 `amap_not_configured` 错误码，**不阻断对话**。

## 服务层（`app/services/amap/`）

对外接口（全部异步 httpx，返回**统一错误信封**，不抛裸异常）：

| 接口 | 说明 |
|------|------|
| `geocode(address, city=None)` | 地理编码：地址描述 → 经纬度候选（≤5 条，含省/市/区县） |
| `route(origin, destination, mode)` | 路径规划：`walking\|driving\|transit` → distance/duration/steps |
| `regeo(location)` | 逆地理编码（transit 城市推导的内部辅助，不对 agent 暴露） |

### 统一信封

```json
// 成功
{"ok": true, "data": { ... }}
// 失败
{"ok": false, "error": "amap_unavailable", "message": "高德地理编码服务暂时不可用"}
```

### 错误码契约

| 错误码 | 触发场景 | 语义 |
|--------|----------|------|
| `amap_not_configured` | `AMAP_WEB_KEY` 未配置 | 部署缺 key（演示环境不阻断对话） |
| `amap_unavailable` | 网络/超时/HTTP 错误/API 业务错误（status≠1）/响应解析失败 | 高德服务不可用 |
| `amap_no_result` | geocode 成功但 0 候选 | 地址无匹配结果 |
| `amap_invalid_param` | 地址为空/坐标格式非法/mode 不在 walking\|driving\|transit | 参数非法 |
| `amap_route_failed` | route 业务错误/无可行路径/transit 城市推导失败 | 路径规划失败 |

### geocode 多候选消歧（船长决策：组合方案，更稳）

实测"黄鹤楼"返回多城市同名（成都新津/湖北咸宁/武汉等），两种方式组合：

1. **返回候选列表**（最多 5 条，含 `formatted_address`/`province`/`city`/
   `district`/`adcode`）——模型结合景区上下文（如景区在武汉 → 选"武汉市"
   候选）自行选择最匹配项；
2. **`city` 参数**（城市名或城市 adcode，如 `city="武汉"`）——请求时即
   缩小范围，语义明确的场景直接精确命中。

`geocode` 成功 data 结构：

```json
{
  "address": "黄鹤楼",
  "count": 2,
  "candidates": [
    {"formatted_address": "湖北省武汉市武昌区黄鹤楼", "location": "114.306967,30.545389",
     "province": "湖北省", "city": "武汉市", "district": "武昌区", "adcode": "420106"},
    {"formatted_address": "四川省成都市新津区黄鹤楼", "location": "103.818,30.410",
     "province": "四川省", "city": "成都市", "district": "新津区", "adcode": "510132"}
  ]
}
```

`location` 为 `"经度,纬度"` 字符串（高德方向 API 的 origin/destination 格式）。

### route

- walking/driving：`/v3/direction/{mode}`，取 `route.paths[0]`；
- transit：`/v3/direction/transit/integrated`（要求 city/cityd 参数——
  客户端自动经 `regeo` 推导起终点城市，调用方无需传城市），取
  `route.transits[0]` 并归一化为统一 steps（步行指令 + 公交「乘坐{线路名}
  （上车站 → 下车站）」合成指令）。

成功 data 结构（walking/driving/transit 同构）：

```json
{
  "mode": "walking",
  "origin": "114.306967,30.545389",
  "destination": "114.320,30.560",
  "distance": "1262",
  "duration": "908",
  "strategy": "",
  "steps": [
    {"instruction": "步行向南出发，进入……", "distance": "10", "duration": "8", "road": ""}
  ]
}
```

`distance` 单位米、`duration` 单位秒（字符串，高德原样透出）。

## 对话工具（`app/services/deepagents/amap_tools.py`）

装配进 **deepagents 用户侧工具集**（`agent.py::_select_tools` 始终装配，
不参与工具结果缓存）；**管理员 agent（`admin_agent`）不装配**。

| 工具 | 参数 | 说明 |
|------|------|------|
| `geocode_location` | `description`（位置描述）、`city`（可选，城市名/adcode） | 位置描述 → 经纬度候选 JSON |
| `plan_route` | `origin`/`destination`（`"经度,纬度"` 坐标）、`mode`（默认 walking） | 坐标对 → 路径 JSON |

- **用法引导在工具描述中**：先 `geocode_location` 取坐标（候选的 `location`
  字段），再 `plan_route` 规划——高德方向 API 只收坐标不收地址文本。
- **返回值为 JSON 字符串**（信封原样透出）；工具层不抛裸异常（服务层意外
  异常防御性包装为 `amap_unavailable`）。
- **SSE 步骤透出**：`AMAP_STEP_LABELS` = `geocode_location → 正在定位…`、
  `plan_route → 正在规划路线…`，装配层合并进 `_STEP_LABELS`，与既有
  search_spots 等步骤事件一致透出（ADH 前端零改动）。
- 错误码可见性：工具把 `{"ok": false, "error": ..., "message": ...}` 透出
  给模型，模型如实转达（如"定位服务暂时不可用/未找到该位置"），不编造。

## 对话链路透出（route_data，批次8 B3）

plan_route 工具结果除透出给模型外，还经对话接口透出给 ADH 前端渲染路线卡片：

- **非流式**：`POST /api/v1/dialogue/chat` 响应 `ChatResponse.route_data`
  （dialogue.py）；**流式**：SSE `done` 事件携带 `route_data` 字段，两处一致。
- 透出内容为 plan_route 工具返回的 JSON 信封**原样**（成功
  `{"ok": true, "data": {...}}` 或失败 `{"ok": false, "error": "amap_*",
  "message": ...}`）；本轮无 plan_route 调用或工具结果解析失败时为 `null`
  （ADH 侧仅识别 dict 透出，前端不渲染路线卡片，不阻断对话）。
- **ADH 链路**：`langchainAgent.py` 在 done 事件提取 route_data → 透出
  `RouteMessage` → `agentApi.py` 编码路线帧 `{"type": "route", "data": ...}`
  → `web/app/lib/comm.ts` `callbackRoute` → `chatbot.tsx` 渲染 `routeCard.tsx`
  （AMap 地图起点/终点 Marker + 路径 Polyline + 距离/时长摘要 + 步骤列表）。
- **前端降级**：高德 JS key 未配置/脚本加载失败（网络、域名白名单）/坐标非法
  时自动降级文字路线（步骤列表 + 距离时长照常展示），不阻断对话；失败信封
  （amap_* 错误码）如实展示错误提示。

## 冒烟

```bash
cd digitalHumanApi
AMAP_WEB_KEY=<key> python -m scripts.smoke_amap          # 默认：黄鹤楼 → 武昌火车站（步行）
AMAP_WEB_KEY=<key> python -m scripts.smoke_amap 灵山胜境 灵山景区东门 driving
```

无 key 时输出 `amap_not_configured` 信封（等价验证错误码契约）。
