# Alembic 数据库迁移（批次2 地基任务 C1）

> 本文件是数据库 schema 版本管理的**权威使用文档**。
> 涉及 schema 变更（建表/改表/加列）一律走 Alembic 迁移，禁止手写 SQL 改库。

## 一、背景与决策

- 项目此前无 schema 版本管理：`app/core/database.py` 直接由 ORM `Base.metadata.create_all` 建表，
  改表靠手写（容易漏改、无回滚、无历史）。Alembic 是 SQLAlchemy 官方的迁移工具。
- **启动时自动迁移**（`app/core/migrations.py` 的 `run_migrations()`，main.py lifespan Phase 1 调用）：
  按数据库状态三态分流，已部署库平滑过渡、新库自动建齐：
  | 库状态 | 动作 | 说明 |
  |--------|------|------|
  | 空库（无任何表） | `upgrade head` | 从零建齐全部表 |
  | 遗留库（有业务表、无 `alembic_version`） | 幂等 `create_all` → `stamp head` | 先补建历史增量引入的缺失业务表，再基线化当前版本，**不丢数据** |
  | 已版本化库（有 `alembic_version`） | `upgrade head` | 应用待执行迁移 |
- 遗留库补表原因：业务表历史上增量引入（knowledge_base 在 #3、users 在 #14 才加入），直接 stamp
  会把缺表状态永久钉死在当前版本（后续 upgrade 为 no-op），因此 stamp 前先执行幂等 `create_all`
  （IF NOT EXISTS 语义）补齐缺失业务表。
- 迁移失败自动回退 `create_all`（幂等 IF NOT EXISTS），不阻断启动（仅告警日志）。
- 独立 CLI（`app/tasks/backfill.py`）同样调用 `run_migrations()`，与 lifespan 行为一致。
- 初始迁移（`04436fda61c5`）由 autogenerate 生成并复核：**其 DDL 产物与 ORM create_all
  逐字节一致**（测试 `test_upgraded_schema_matches_orm_metadata` 守护），这是遗留库
  stamp 基线不产生 schema 漂移的前提。

## 二、命令入口（均在 `digitalHumanApi/` 目录下执行）

```bash
alembic upgrade head          # 升级到最新 schema（部署/日常）
alembic downgrade -1          # 回退一个版本（慎用，先备份）
alembic current               # 当前库所在版本
alembic history               # 迁移历史
```

### 开发流程（改模型 → 生成迁移 → 测试 → 合并）

1. 修改 `app/models/*.py`（或新增模型文件并在 `app/models/__init__.py` 注册）。
2. 生成迁移（**autogenerate 只是草稿，必须人工 review**）：

   ```bash
   alembic revision --autogenerate -m "添加公告表"
   ```

   - 默认对 `settings.DATABASE_URL`（.env 配置的 digital_human.db）做 diff；
   - 若不想动开发库，可指定临时空库：`alembic -x db_url="sqlite+aiosqlite:////tmp/x.db" revision --autogenerate -m "..."`；
   - review 生成的 `migrations/versions/xxx_*.py`：确认只含预期变更，补全 `downgrade()`。
3. 本地验证：`alembic upgrade head`（临时库验证：`-x db_url=...`），再 `alembic downgrade -1` 确认可回退。
4. 跑测试：`pytest -q`（含 `tests/test_migrations.py` 的 schema 漂移守护与三态路径）。
5. 合并提交，部署后启动自动 `upgrade head`（无需手动执行）。

### 变更规则

- **SQLite 改列**：环境已开 `render_as_batch=True`，autogenerate 会产出
  `op.batch_alter_table(...)`（表重建模式），直接沿用即可；不要手写裸 `ALTER TABLE`。
- 新增表/索引等常规变更无特殊要求。
- 禁止删除或改名生产表（约束：不删除现有数据表）；确需结构性变更先在文档与代码评审中说明。

## 三、架构说明（迁移机制）

- `alembic.ini` — 配置入口；`script_location = %(here)s/migrations`（`%(here)s` 为 ini 所在目录绝对路径，避免相对 CWD 解析）；`sqlalchemy.url` 留空，
  URL 解析优先级：`alembic -x db_url=...` 命令行参数 > ini 的 `sqlalchemy.url`（测试程序化
  `Config.set_main_option` 入口）> 应用配置 `settings.DATABASE_URL`（.env / 环境变量）。
- `migrations/env.py` — **async 模板**（项目是 SQLAlchemy 2.0 async engine：
  `create_async_engine` + aiosqlite）：在线迁移经 `asyncio.run(run_async_migrations())` 包装；
  导入 `app.models` 全量模型保证 `Base.metadata` 完整；已显式把 `digitalHumanApi/` 加入 sys.path
  （CLI 直跑不需要 `PYTHONPATH`）。
- `migrations/versions/` — 版本文件（`04436fda61c5_初始_schema_基线.py` 为初始基线，
  `down_revision = None`）。
- `app/core/migrations.py` — 程序化入口 `run_migrations()`（三态分流：空库 upgrade / 遗留库先幂等
  create_all 补缺表再 stamp / 版本化库 upgrade；线程池执行，因 env.py 内部 `asyncio.run` 不能在运行中事件循环里直接调用）。
- 测试 `tests/test_migrations.py` — 空库建齐 / DDL 与 ORM 等价 / 遗留库 stamp 后
  upgrade no-op 不丢数据 / **遗留库缺表场景（仅部分表的遗留库 stamp 前 create_all 补齐缺失表、数据不丢）** /
  版本化库幂等 / `run_migrations` 三态路径（全部用临时文件库，不触碰真实 digital_human.db）。

## 四、部署与升级（Docker）

- Dockerfile.api 将 `digitalHumanApi/` 整体拷入镜像（alembic.ini + migrations/ 随镜像分发），
  WORKDIR `/app` 与 `DATABASE_URL=sqlite+aiosqlite:///./digital_human.db` 对应
  `/app/digital_human.db`（volume 持久化），容器启动时 lifespan 自动迁移，无需额外命令。
- 升级步骤：拉新镜像 → 重启 api 服务 → 日志出现「数据库迁移完成（upgrade head）」
  即成功；迁移失败会回退 create_all 并打印 ERROR 日志（不阻断启动，但需人工排查）。
