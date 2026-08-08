"""Alembic 迁移环境（SQLAlchemy 2.0 async 模板）。

项目数据库是 async engine（create_async_engine + aiosqlite，见 app/core/database.py），
因此在线迁移经 run_async_migrations 包装（asyncio.run）执行。

数据库 URL 解析优先级（_resolve_url）：
  1. `alembic -x db_url="sqlite+aiosqlite:///..."` 命令行参数（生成/测试指定临时库）
  2. alembic.ini 的 `sqlalchemy.url`（测试程序化 Config 覆盖入口）
  3. 应用配置 settings.DATABASE_URL（.env / 环境变量，与 app/core/database.py 同源）

启动时程序化入口见 app/core/migrations.py（run_migrations），
开发/部署命令见 doc/alembic-migrations.md。
"""
import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 确保 digitalHumanApi/ 在 sys.path（alembic CLI 直接运行时当前目录不一定在路径上）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

# 导入全量 ORM 模型，确保 Base.metadata 完整（autogenerate 依赖，勿删）
from app.models import Base  # noqa: E402,F401

config = context.config

# 有 ini 文件时应用其日志配置（程序化 Config 无 ini，跳过）
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def process_revision_directives(context, revision, directives):
    """autogenerate 未检测到任何变更时不生成空迁移文件（官方 recipe）。"""
    migration_script = directives[0]
    if getattr(getattr(context.config, "cmd_opts", None), "autogenerate", False):
        script = migration_script.upgrade_ops
        if script is None or script.is_empty():
            directives[:] = []
            print("No changes detected in models, skip empty migration...")


def _resolve_url() -> str:
    """按优先级解析数据库 URL（见模块 docstring）。"""
    try:
        x_args = context.get_x_argument(as_dictionary=True)
    except Exception:
        x_args = {}
    url = x_args.get("db_url")
    if not url:
        url = config.get_main_option("sqlalchemy.url")
    if not url:
        url = settings.DATABASE_URL
    return url


def run_migrations_offline() -> None:
    """离线模式（--sql）：仅生成 SQL 脚本，不连接数据库。"""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # render_as_batch：SQLite 无原生 ALTER TABLE，未来涉及改列的迁移
    # 需用 batch 模式渲染（表重建），autogenerate 会产出 batch_alter_table 操作
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        process_revision_directives=process_revision_directives,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """用异步 engine 连接数据库并执行迁移。"""
    section = config.get_section(config.config_ini_section, {}) or {}
    connectable = async_engine_from_config(
        {**section, "sqlalchemy.url": _resolve_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """在线模式：asyncio.run 包装（env.py 为同步入口，Alembic 命令内部调用）。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
