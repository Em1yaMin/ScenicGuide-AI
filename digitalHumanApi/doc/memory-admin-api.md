# 记忆管理接口契约（批次5 B5）

> 本文档是**前后端对接权威契约**：路径、请求/响应 schema、鉴权与行为约定。
> 前端任务（管理后台记忆管理页：用户选择 → 记忆列表 → 新增/修改/删除）按此对接，
> 任何变更须同步本文档与 `app/schemas/memory_admin.py`。

## 背景

长期记忆（用户画像）由系统在对话后自动提取写入（`user_memory` 向量命名空间，
见 `doc/long-term-memory.md`）。用户侧已由 deepagents 记忆四工具实现
recall/save/list/delete（全部 user_id 隔离、游客跳过）；本模块补**管理端**能力：
管理员（`X-Admin-Token`）可查看/新增/修改/删除**任意注册用户**的记忆——这是
船长确认的管理员例外（用户侧隔离不适用于管理端）。

游客/匿名用户（`visitor` / `guest-*`）无长期记忆：**查询返回空列表**、
**新增返回 400**。

## 数据模型（记忆条目）

| 字段 | 类型 | 说明 |
|------|------|------|
| memory_id | str | 记忆条目对外标识：新条目为 uuid；legacy 条目（升级前写入、无 memory_id）为派生 id `legacy:{归一化 content}`（去空白/标点/小写），唯一且稳定 |
| user | str | 归属用户标识（注册用户为 `str(id)`，如 `"1"`） |
| fact_type | str | `preference`（偏好）/ `fact`（事实）/ `habit`（习惯） |
| content | str | 自然语言记忆句，**1-200 字符** |
| source_conversation_id | str | 来源会话 ID（溯源用，可为空串） |
| timestamp | float | 提取/更新时间（**Unix 秒**，前端自行格式化为可读时间） |

同用户同「归一化 content」的记忆按去重语义覆盖更新（不膨胀），管理端新增/修改
同样遵循。

## 鉴权方式

全部接口为管理端：请求头 `X-Admin-Token: <ADMIN_API_TOKEN>`；缺失/错误返回
`401 {"detail": "未授权访问"}`（或「管理员 Token 未配置」）。

## 一、用户列表（供管理页面选择用户）

### 列表（分页）

```
GET /api/admin/memories/users?keyword=alice&page=1&page_size=20
```

| Query 参数 | 类型 | 默认 | 说明 |
|------------|------|------|------|
| keyword | str | 无 | 用户名/昵称关键词模糊搜索（可选） |
| page | int | 1 | 页码（≥1） |
| page_size | int | 20 | 每页条数（1-100） |

排序：id 升序。响应 `200`：

```json
{
  "items": [
    {
      "id": 1,
      "username": "alice",
      "nickname": "小艾",
      "memory_count": 3,
      "created_at": "2026-08-08T10:00:00",
      "last_login_at": null
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

`memory_count` 为该用户长期记忆条数（实时统计）。

## 二、按用户查看记忆列表

```
GET /api/admin/memories?user=1
```

| Query 参数 | 类型 | 必填 | 说明 |
|------------|------|------|------|
| user | str | 是 | 目标用户标识（注册用户 id / 游客标识；首尾空白自动去除） |

响应 `200`（游客/匿名用户返回空列表；条目按写入顺序）：

```json
{
  "user": "1",
  "total": 2,
  "items": [
    {
      "memory_id": "3a14e6fdf7b6402baf5a54385a0acbd2",
      "user": "1",
      "fact_type": "preference",
      "content": "喜欢拍照",
      "source_conversation_id": "conv-xxx",
      "timestamp": 1780000000.0
    },
    {
      "memory_id": "legacy:喜欢自然风光",
      "user": "1",
      "fact_type": "preference",
      "content": "喜欢自然风光",
      "source_conversation_id": "",
      "timestamp": 1770000000.0
    }
  ]
}
```

## 三、新增记忆

```
POST /api/admin/memories
Content-Type: application/json
```

请求体：

```json
{
  "user": "1",
  "fact_type": "preference",
  "content": "喜欢拍照"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| user | str | 是 | 目标注册用户 id（字符串形式；首尾空白自动去除） |
| fact_type | str | 否 | 默认 `fact`；非法值 422 |
| content | str | 是 | 记忆句，**1-200 字符**（超长/空/**纯空白** 422；首尾空白自动去除） |

错误：游客/匿名用户 `400 {"detail": "游客/匿名用户不支持记忆管理"}`；
目标用户未注册（含非数字标识）`404 {"detail": "用户不存在"}`。

响应 `201`（`memory_id` 为落库后的稳定 id，可直接用于修改/删除）：

```json
{
  "memory_id": "3a14e6fdf7b6402baf5a54385a0acbd2",
  "user": "1",
  "fact_type": "preference",
  "content": "喜欢拍照",
  "source_conversation_id": "",
  "timestamp": 1780000000.0
}
```

## 四、修改记忆（删除后重建）

```
PUT /api/admin/memories/{memory_id}
Content-Type: application/json
```

`{memory_id}` 支持 uuid 与 legacy 派生 id。

请求体（**部分更新**，content 与 fact_type 至少提供其一，否则 422）：

```json
{ "fact_type": "habit", "content": "喜欢摄影" }
```

| 字段 | 类型 | 说明 |
|------|------|------|
| fact_type | str | 可选；不传保留原值；非法值 422 |
| content | str | 可选；不传保留原值；1-200 字符（**纯空白 422**；首尾空白自动去除） |

行为约定：

- 实现为**删除原条目后重建**：保留 `user` 与 `source_conversation_id`；
  `timestamp` 刷新为当前时间；**`memory_id` 更新为新建 uuid**（响应返回新 id，
  旧 id 即失效）
- **校验先行**：content 在 schema 层先 strip 再校验（纯空白 422），任何删除
  发生前即失败，不存在「先删后建」在空白内容下先删后失败的数据丢失
- 新内容与同用户其他条目同「归一化 content」时按去重语义覆盖更新
  （该其他条目被替换）
- 目标不存在 `404 {"detail": "记忆不存在"}`

响应 `200`：与新增相同的条目结构（含新 `memory_id`）。

## 五、删除记忆（跨用户，管理员例外）

```
DELETE /api/admin/memories/{memory_id}
```

`{memory_id}` 支持 uuid 与 legacy 派生 id；管理员可按 id 删除**任意用户**的
记忆（不校验归属用户——管理员例外）。

响应 `200`：

```json
{ "success": true, "user": "1", "memory_id": "3a14e6fdf7b6402baf5a54385a0acbd2", "deleted": 1 }
```

目标不存在 `404 {"detail": "记忆不存在"}`。

## 六、审计

删除/修改/新增记忆均记录结构化日志（`app.routers.memory_admin` logger，
`operator=admin` 标识操作者——X-Admin-Token 认证通过即视为管理员），
含目标 `user` / `memory_id` / 内容摘要（截断 50 字符）：

```
管理员新增记忆: operator=admin user=1 memory_id=xxx content='喜欢拍照'
管理员修改记忆: operator=admin user=1 memory_id=xxx content='喜欢拍照' -> '喜欢摄影'
管理员删除记忆: operator=admin user=1 memory_id=xxx content='喜欢摄影'
```

**日志先行**：DB 审计表暂不引入（需 Alembic 迁移 + 写入面扩张，当前管理端
操作频次低、日志已可追溯；如后续需要留存查询再评估审计表）。

## 前端对接建议

1. 记忆管理页进入时拉 `GET /api/admin/memories/users` 渲染用户列表
2. 选中用户后拉 `GET /api/admin/memories?user={id}` 渲染记忆列表
   （`timestamp` 用 `new Date(timestamp * 1000)` 格式化）
3. 新增/修改用同一表单；修改后刷新列表（memory_id 已变化）
4. 删除需二次确认（不可恢复，审计已记录）
