# 公告模块 + 首页活数据接口契约（批次4 B2）

> 本文档是**前后端对接权威契约**：路径、请求/响应 schema、鉴权与行为约定。
> 前端任务（公告管理页 / 首页活数据卡片）按此对接，任何变更须同步本文档。

## 一、公告模块

### 1.1 数据模型（announcements 表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | int | 自增主键 |
| title | str(255) | 公告标题 |
| content | text | 公告正文 |
| status | str(16) | `published`（发布）/ `draft`（草稿），默认 `draft` |
| pinned | bool | 置顶标记，默认 `false`；访客端列表置顶优先 |
| created_at | datetime | 创建时间（UTC） |
| updated_at | datetime | 更新时间（UTC，变更自动刷新） |

### 1.2 管理端接口（X-Admin-Token 鉴权）

**鉴权方式**：请求头 `X-Admin-Token: <ADMIN_API_TOKEN>`；缺失/错误返回 `401 {"detail": "未授权访问"}`。

#### 列表（含草稿，分页）

```
GET /api/admin/announcements?status=draft&keyword=开放&page=1&page_size=20
```

| Query 参数 | 类型 | 默认 | 说明 |
|------------|------|------|------|
| status | str | 无 | 状态筛选：`published` / `draft`，不传返回全部 |
| keyword | str | 无 | 标题/正文关键词模糊搜索 |
| page | int | 1 | 页码（≥1） |
| page_size | int | 20 | 每页条数（1-100） |

排序：置顶优先（pinned desc）→ 更新时间倒序（updated_at desc）→ id 倒序。

响应 `200`：

```json
{
  "items": [
    {
      "id": 1, "title": "景区开放时间调整", "content": "…",
      "status": "published", "pinned": true,
      "created_at": "2026-08-08T10:00:00", "updated_at": "2026-08-08T10:00:00"
    }
  ],
  "total": 1, "page": 1, "page_size": 20
}
```

#### 创建

```
POST /api/admin/announcements
Content-Type: application/json
```

请求体：

```json
{ "title": "景区开放时间调整", "content": "…", "status": "draft", "pinned": false }
```

- `title` 必填（1-255 字符）、`content` 必填；`status` 默认 `draft`；`pinned` 默认 `false`。
- 非法 status 值返回 `422`。

响应 `201`：完整条目（同列表 items[0] 结构）。

#### 更新（部分更新）

```
PUT /api/admin/announcements/{id}
```

请求体（仅更新显式传入字段，均可选）：`{ "title"?, "content"?, "status"?, "pinned"? }`

响应 `200`：完整条目；`id` 不存在返回 `404 {"detail": "公告不存在"}`。

#### 发布状态切换

```
PATCH /api/admin/announcements/{id}/status
```

请求体：`{ "status": "published" }`（`published` / `draft`）

响应 `200`：完整条目；`404` 同上。**发布（published）即对访客端可见**。

#### 删除

```
DELETE /api/admin/announcements/{id}
```

响应 `200`：`{"success": true}`；`404` 同上。

### 1.3 访客端接口（公开，无鉴权）

#### 公告列表（仅已发布）

```
GET /api/v1/announcements?limit=20
```

| Query 参数 | 类型 | 默认 | 说明 |
|------------|------|------|------|
| limit | int | 20 | 返回条数上限（1-100） |

行为：仅返回 `status=published` 的公告；**草稿/已删除公告绝不出现在访客端**。
排序：置顶优先（pinned desc）→ 创建时间倒序（created_at desc）→ id 倒序。

响应 `200`：

```json
{
  "items": [
    {
      "id": 1, "title": "景区开放时间调整", "content": "…", "pinned": true,
      "created_at": "2026-08-08T10:00:00", "updated_at": "2026-08-08T10:00:00"
    }
  ],
  "total": 1
}
```

注意：访客端条目**不含 status 字段**（与 `total` 为本次返回条数而非全部已发布数——需要总数时用管理端列表）。

## 二、首页活数据接口（公开，无鉴权）

背景：首页「热门景点 / 推荐路线」卡片原先写死在前端，现改为数据文件驱动。
数据源为后端 `digitalHumanApi/app/data/spots.json` 与 `routes.json`（**每次请求实时读取**），
与导览 agent 结构化工具同源；管理端在线编辑走 `/api/admin/scenic`（管理后台知识库页「景点数据/路线数据」页签，契约见 `doc/scenic-data-api.md`），
改数据后首页刷新即变化。

```
GET /api/v1/home/highlights?spots_limit=3&routes_limit=3
```

| Query 参数 | 类型 | 默认 | 说明 |
|------------|------|------|------|
| spots_limit | int | 3 | 热门景点条数上限（1-20） |
| routes_limit | int | 3 | 推荐路线条数上限（1-20） |

响应 `200`：

```json
{
  "spots": [
    {
      "id": "spot_001", "name": "灵山大照壁",
      "description": "进入灵山胜境的第一道视觉屏障，长39.8米、高7米的青石照壁…",
      "tags": ["历史人文", "地标", "拍照打卡"], "duration_minutes": 30,
      "difficulty": "轻松", "location": "景区入口处，面朝太湖",
      "highlights": ["华夏第一壁", "赵朴初题字"]
    }
  ],
  "routes": [
    {
      "id": "route_001", "name": "历史文化深度游",
      "description": "从华夏第一壁到千年古刹再到佛教艺术巅峰…",
      "spot_ids": ["spot_001", "spot_006", "spot_007", "spot_008", "spot_009"],
      "tags": ["历史人文", "文化体验", "深度游"], "suitable_for": ["文化爱好者", "老年人"],
      "total_duration_minutes": 360, "difficulty": "中等",
      "recommend_reason": "这条路线串联千年佛教文化脉络…"
    }
  ]
}
```

字段即 JSON 数据源原样透出（额外字段兼容保留）；数据文件缺失/损坏时返回空数组 `{"spots": [], "routes": []}`，不报错。

## 三、前端对接备忘（api/index.ts 建议常量）

```ts
export const API_ADMIN_ANNOUNCEMENTS = '/api/admin/announcements'          // 管理端 CRUD（含 /{id}、/{id}/status）
export const API_PUBLIC_ANNOUNCEMENTS = '/api/v1/announcements'            // 访客端公告列表
export const API_HOME_HIGHLIGHTS = '/api/v1/home/highlights'               // 首页热门景点/推荐路线
```

管理端公告操作均需携带 `X-Admin-Token` 请求头（token 存 `localStorage` 的 `admin_api_token`，
与知识库管理页一致）。

## 四、测试覆盖

- `tests/test_announcement.py` — CRUD / 分页筛选 / 状态切换 / 访客过滤与排序 / 鉴权 401（12 用例）
- `tests/test_home.py` — 首页活数据默认/limit/空数据/公开访问/加载容错（6 用例）
- `tests/test_migrations.py` — announcements 表升级建齐、downgrade 回退删表、DDL 与 ORM 逐字节一致（既有守护自动覆盖）
