# 景区数据管理 API（/api/admin/scenic）

管理导览 agent 的结构化景区数据（景点 spots / 路线 routes）。**数据源为
`app/data/spots.json` 与 `routes.json`**——与导览 agent 结构化工具
（search_spots / search_routes / get_spot_detail / get_route_detail /
filter_by_tags / recommend_routes）、首页活数据（`/api/v1/home/highlights`）、
推荐问句降级（recommend_service）为**同一份数据**：管理端写文件后 agent
工具/首页零改动实时生效（单一来源 = JSON 文件）。

批次 e3（2026-08-11 灵山化）：spots.json（10 景点）/ routes.json（8 路线）
已从翠微峰等演示数据替换为灵山胜境数据（schema 未变）。

## 鉴权与 RBAC

- 全部端点要求管理端鉴权：`X-Admin-Token` 请求头（`ADMIN_API_TOKEN` /
  `ADMIN_TOKENS` 映射）或 `Authorization: Bearer` 管理员 JWT（账号密码登录，
  见 deps.py `verify_admin_token` 双路径）
- **删除类敏感操作要求 admin 角色**（`require_role("admin")`），operator
  访问返回 403（与知识库删除/记忆删除一致）
- 未认证 401（detail=未授权访问）

## 数据结构（与 app/data/*.json 字段对齐）

### 景点 Spot

```json
{
  "id": "spot_001",
  "name": "灵山大佛",
  "image": "/images/spots/spot_001.jpg",
  "description": "世界最高露天青铜释迦牟尼立像，通高88米…",
  "tags": ["历史人文", "宗教", "地标", "拍照打卡", "祈福"],
  "duration_minutes": 120,
  "difficulty": "中等",
  "location": "秦履峰南麓，景区最高处",
  "highlights": ["抱佛脚祈福", "216级登云道", "俯瞰太湖全景"]
}
```

- `id`：服务端自动生成（`spot_011`，编号 = 现有最大值 + 1，删除后不复用）
- `image`：**可选**，实景图 URL（前端 `public/images/spots/` 静态资源；首页热门景点卡片有图渲染实景图、缺省回退 emoji 意象占位，前端契约见 `digitalHumanVue/src/types/home.ts` HomeSpot.image；管理端景点表单未暴露该字段，经 schema `extra=allow` 透传保持原值）
- `difficulty`：仅支持 `轻松` / `中等` / `挑战`

### 路线 Route

```json
{
  "id": "route_001",
  "name": "历史文化深度游",
  "description": "从华夏第一壁到千年古刹再到佛教艺术巅峰…",
  "spot_ids": ["spot_001", "spot_006", "spot_007", "spot_008", "spot_009"],
  "tags": ["历史人文", "文化体验", "深度游", "一日游"],
  "suitable_for": ["文化爱好者", "老年人", "学生", "独自旅行"],
  "total_duration_minutes": 360,
  "difficulty": "中等",
  "recommend_reason": "这条路线串联千年佛教文化脉络…"
}
```

- `id`：服务端自动生成（`route_009`）
- `spot_ids`：必须引用已存在景点 id（后端校验，未知 id 返回 422）；删除被
  路线引用的景点会被拒绝（422，detail 列出引用路线 id 与名称，需先在路线
  编辑中取消勾选）

## 端点

### 景点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/scenic/spots` | 景点列表（完整返回，无分页） |
| GET | `/api/admin/scenic/spots/{spot_id}` | 按 id 查询（不存在 404） |
| POST | `/api/admin/scenic/spots` | 新建（201；名称重复 422） |
| PUT | `/api/admin/scenic/spots/{spot_id}` | 部分更新（仅更新传入非 None 字段；不存在 404） |
| DELETE | `/api/admin/scenic/spots/{spot_id}` | 删除（**要求 admin**；不存在 404；**被路线引用 422**；成功 `{"success": true}`） |

### 路线

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/scenic/routes` | 路线列表（完整返回） |
| GET | `/api/admin/scenic/routes/{route_id}` | 按 id 查询（不存在 404） |
| POST | `/api/admin/scenic/routes` | 新建（201；名称重复/未知景点 id 422） |
| PUT | `/api/admin/scenic/routes/{route_id}` | 部分更新（不存在 404） |
| DELETE | `/api/admin/scenic/routes/{route_id}` | 删除（**要求 admin**；不存在 404） |

### 请求体示例

POST /api/admin/scenic/spots（id 不必传，服务端生成）：

```json
{
  "name": "三圣殿",
  "description": "供奉华严三圣的殿堂",
  "tags": ["历史人文", "宗教"],
  "duration_minutes": 40,
  "difficulty": "轻松",
  "location": "灵山梵宫东侧",
  "highlights": ["华严三圣像"]
}
```

POST /api/admin/scenic/routes（spot_ids 须引用已存在景点）：

```json
{
  "name": "禅意漫步线",
  "description": "中轴线轻松漫步",
  "spot_ids": ["spot_001", "spot_002"],
  "tags": ["休闲", "轻松"],
  "suitable_for": ["老年人"],
  "total_duration_minutes": 90,
  "difficulty": "轻松",
  "recommend_reason": "节奏舒缓"
}
```

PUT 仅需携带要更新的字段（未传字段保持原值）。

## 错误码

| 状态码 | 场景 | detail |
|--------|------|--------|
| 401 | 未认证/错 token | 未授权访问 |
| 403 | operator 调删除类接口 | 需要 admin 角色 |
| 404 | 目标 id 不存在 | 景点/路线不存在: {id} |
| 422 | 业务校验失败 | 名称已存在 / 引用了不存在的景点 id / 删除被路线引用的景点（detail 列出引用路线） / 字段纯空白 / difficulty 仅支持… |
| 200 | 全局异常兜底 | code=500 服务器内部错误（FastAPI catch-all，与全站一致） |

## 数据一致性保证

- **agent 工具实时生效**：结构化工具每次调用实时读 `app/data/*.json`，
  管理端写入后无需重启/重建，agent 下次调用即读到新数据
- **工具缓存联动失效**：deepagents 工具缓存把静态数据工具（search_spots
  等）按 TTL 1h 缓存（A4 调优）；写入后 `tool_cache.invalidate_scenic()`
  立即清除这些条目，避免 agent 最长 1 小时读到旧数据
- **原子写入**：临时文件 + os.replace + fsync（与 rag.py 向量落盘同模式），
  任何时刻文件均为完整合法 JSON（读方无中间态）

## 前端

- `digitalHumanVue/src/api/scenic.ts`（fetchScenicSpots / createScenicSpot /
  updateScenicSpot / deleteScenicSpot + routes 同构）
- `digitalHumanVue/src/types/scenic.ts`（ScenicSpot / ScenicRoute /
  SCENIC_DIFFICULTIES）
- `digitalHumanVue/src/views/admin/KnowledgeView.vue` 三页签：
  知识文档（knowledge_base 表 CRUD）/ 景点数据 / 路线数据

## 测试

`digitalHumanApi/tests/test_scenic_data_api.py`（25 用例）：鉴权 401 /
RBAC（operator 删除 403）/ 灵山数据列表 / CRUD / 名称唯一 / spot_ids 引用 /
删除被路线引用景点 422（detail 列出引用路线）/ 解除引用后可删除 / 纯空白
字段 422 / **agent 工具实时读到页面写入数据** / 数据文件原子性 / 工具缓存
失效联动。
测试通过 monkeypatch 重定向 `_DATA_DIR`（service + recommend_agent +
home_service 三处同源）到临时目录，不污染工作区数据文件。
