# 账号管理 API（批次 2026-08-13 船长需求）

管理后台账号全生命周期管理 + 管理员操作审计日志。后端：`digitalHumanApi/app/routers/user_admin.py`、`app/services/user_admin_service.py`、`app/services/admin_audit_service.py`；前端：`digitalHumanVue/src/views/admin/UserAdminView.vue`、`src/api/userAdmin.ts`、`src/types/userAdmin.ts`。

## 鉴权与权限

- 全部端点走管理端双路径鉴权（`deps.verify_admin_token`：X-Admin-Token / 管理员 JWT）
- **RBAC 分级**：列表/详情/审计查询 = operator 可读；创建 = 常规写（operator）；**禁用/启用/重置密码/删除 = 敏感操作，要求 admin 角色**（operator 403）
- **审计**：全部敏感操作（创建/禁用启用/重置密码/删除）强制落库 `admin_audit_logs` 表（operator=管理员账号名或 X-Admin-Token 值；agent 操作为 `admin-agent`），与业务同一事务；**删除类操作在二次确认执行后才写审计**；执行失败也记录（结果如实）
- **保护性约束**：系统管理员账号（`settings.ADMIN_USERNAME`，超管）同名 users 行不可禁用/删除（400）；删除为**单目标**接口（无批量路径）

## 端点

前缀 `/api/admin/users`（`routers/user_admin.py`；注意 `/audit-logs` 必须声明在 `/{user_id}` 之前——Starlette 按注册顺序匹配路径）。

| 方法 | 路径 | 说明 | 角色 |
|------|------|------|------|
| GET | `/api/admin/users?keyword=&page=&page_size=` | 账号分页列表（含 `dialogue_count`/`memory_count`/`is_disabled`） | operator |
| GET | `/api/admin/users/{user_id}` | 账号详情（同列表字段） | operator |
| POST | `/api/admin/users` | 创建账号（username 3-32 位字母/数字/下划线，password ≥6 位；重名 409/弱密码 422） | operator |
| PUT | `/api/admin/users/{user_id}/disabled` | `{"disabled": bool}` 禁用/启用（幂等：状态已一致不重复写审计） | admin |
| POST | `/api/admin/users/{user_id}/reset-password` | `{"new_password": "可选"}`；留空生成 12 位临时密码（**仅响应返回一次**，响应含 `generated`） | admin |
| DELETE | `/api/admin/users/{user_id}` | 删除账号（**单目标**；级联清理 dialogues/evaluations/visitors/长期记忆，响应含 `deleted_*` 计数） | admin |
| GET | `/api/admin/users/audit-logs?keyword=&action=&page=&page_size=` | 审计日志（action ∈ create/disable/enable/reset_password/delete；时间倒序） | operator |

## 软禁用语义

- `users.is_disabled`（默认 0）+ `users.disabled_at`；Alembic 迁移 `b4c3d2e1f0a9`（SQLite 原生 ADD COLUMN，列序与 ORM 一致；防御性列存在性检查）
- **登录**：`/api/v1/auth/login` 对禁用账号 403「账号已被禁用，请联系管理员」（不计入限流失败次数）
- **身份恢复**：`/api/v1/auth/me` 对禁用账号 403（前端 `stores/auth.ts` restoreSession 对 401/403 均清除本地登录态）
- **对话**：`/api/v1/dialogue/chat` 在用户解析后门禁（`_ensure_user_active`）——注册用户 id 形态（全数字）的用户在 users 表不存在（已删除）或被禁用 → 403；游客/其他标识不校验

## 删除级联（影响展示）

删除前调用方应展示影响（列表/详情的 `dialogue_count`/`memory_count`）；删除执行顺序：evaluations（外键先删）→ dialogues → visitors 记录 → user_memory 长期记忆（`memory_service.delete_all_user_memories`）→ users 行。响应 `deleted_dialogues/deleted_evaluations/deleted_memories` 如实汇报。

## 管理员 agent 工具（admin_agent/tools.py）

- 只读：`list_users` / `get_user_detail`
- 写：`create_user`（直接）、`enable_user`（直接，恢复操作）
- **两阶段二次确认**（confirm.py + `confirm_operation`，TTL 120s）：`disable_user` / `reset_user_password` / `delete_user`——首次调用只登记待确认（返回 operation_id + 摘要；删除含影响概览），确认后才执行
- 约束：全部**单目标**（参数为单个 user_id），严禁批量/无目标模糊操作；超管账号拒绝；system prompt（`admin_agent/agent.py`）已同步账号管理纪律；审计 operator=admin-agent

## 审计表结构（admin_audit_logs）

`id / operator(64) / action(32) / target_user_id(int?) / target_username(64，快照) / detail(255?) / created_at`，索引：operator/action/target_user_id/created_at。迁移 `a9f0e1d2c3b4`（防御性建表）。

## 迁移说明（重要）

2026-08-13 起全部 Alembic 迁移为**防御性（幂等）写法**（建表前 `has_table`、加列前列存在性、建索引前索引存在性），原因：`run_migrations` 遗留库路径改为 `create_all + stamp 基线 + upgrade head`（重放全部迁移）。新增迁移必须遵循该约定，否则遗留库路径会重复 DDL 报错。测试：`tests/test_migrations.py`（DDL 等价性按空白归一化比较——SQLite ALTER ADD COLUMN 会把追加列内联进原 CREATE TABLE 文本）。
