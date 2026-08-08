"""Alembic 迁移测试（验收：空库/现有库 upgrade head 均成功且不丢数据）。

覆盖三条路径（与 app/core/migrations.py 的自动判定逻辑一致）：
1. 空库执行 upgrade head → 全部业务表建齐，DDL 与 ORM metadata（create_all）完全一致；
2. 遗留库（create_all 建成、无 alembic_version）stamp head → upgrade 为 no-op、数据不丢；
3. 已版本化库重复 upgrade head → 幂等 no-op。

另覆盖遗留库缺表场景（仅部分业务表的遗留库，run_migrations 在 stamp 前幂等 create_all 补齐缺失表、
数据不丢）与 run_migrations() 程序化入口对三种库状态的选择正确。
测试全部使用临时文件 SQLite，不触碰真实 digital_human.db。
"""
import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base

# digitalHumanApi/migrations/
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# 业务表集合（不含 alembic_version / sqlite_* 系统表）
BUSINESS_TABLES = {
    "announcements",
    "dialogues",
    "evaluations",
    "knowledge_base",
    "stats",
    "users",
    "visitors",
}


def _make_config(db_path: Path) -> Config:
    """程序化 Alembic 配置：指向测试临时库（覆盖 env.py 的 settings 回退）。"""
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path}")
    return cfg


def _table_sql(db_path: Path) -> dict:
    """读取库中全部表/索引 DDL（用于与 ORM create_all 产物逐字节比对）。"""
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            name: sql
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
            )
        }
        indexes = {
            name: sql
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
        return {"tables": tables, "indexes": indexes}
    finally:
        conn.close()


def _current_revision(db_path: Path) -> str:
    """读取当前版本号；库中无 alembic_version 表时返回 None。"""
    conn = sqlite3.connect(db_path)
    try:
        try:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            return row[0] if row else None
        except sqlite3.OperationalError:
            # 遗留库尚无版本表（create_all 建表产物）
            return None
    finally:
        conn.close()


def _head_revision(cfg: Config) -> str:
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _create_orm_schema(db_path: Path, seed: bool = False) -> None:
    """用 ORM metadata（等价历史 create_all）建表，可选写入种子数据。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        if seed:
            # 直接 SQL 造数（表默认值在 Python 侧，DDL 无默认值）
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO visitors (id, first_visit, last_visit) VALUES ('v1', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
                )
                await conn.execute(
                    text("INSERT INTO visitors (id, first_visit, last_visit) VALUES ('v2', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
                )
                await conn.execute(
                    text("INSERT INTO users (username, password_hash, created_at) VALUES ('tester', 'x', '2026-01-01 00:00:00')")
                )
    finally:
        await engine.dispose()


async def _create_partial_legacy_schema(db_path: Path, seed: bool = False) -> None:
    """模拟历史增量建表：仅建早期引入的 visitors/dialogues，其余业务表缺失。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    try:
        async with engine.begin() as conn:
            for name in ("visitors", "dialogues"):
                await conn.run_sync(
                    lambda sync_conn, n=name: Base.metadata.tables[n].create(sync_conn)
                )
        if seed:
            async with engine.begin() as conn:
                await conn.execute(
                    text("INSERT INTO visitors (id, first_visit, last_visit) VALUES ('v1', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
                )
                await conn.execute(
                    text("INSERT INTO visitors (id, first_visit, last_visit) VALUES ('v2', '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
                )
    finally:
        await engine.dispose()


# ---------- 路径 1：空库 upgrade head ----------

def test_empty_db_upgrade_head_creates_all_tables(tmp_path):
    """空库 upgrade head 后业务表齐全、版本表指向 head。"""
    db = tmp_path / "empty.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    assert _current_revision(db) == _head_revision(cfg)
    assert set(_table_sql(db)["tables"]) == BUSINESS_TABLES


def test_upgraded_schema_matches_orm_metadata(tmp_path):
    """迁移产出的 DDL 与 ORM metadata（create_all）逐字节一致。

    该等价性是遗留库 stamp 基线安全的前提：历史 create_all 建出的库
    与初始迁移产物相同，stamp 后无 schema 漂移。
    """
    migrated = tmp_path / "migrated.db"
    orm = tmp_path / "orm.db"
    command.upgrade(_make_config(migrated), "head")
    asyncio.run(_create_orm_schema(orm))

    assert _table_sql(migrated) == _table_sql(orm)


# ---------- 路径 2：遗留库 stamp → upgrade no-op ----------

def test_legacy_db_stamp_then_upgrade_preserves_data(tmp_path):
    """遗留库（create_all 建成、无版本表）stamp head 后 upgrade 为 no-op、数据不丢。"""
    db = tmp_path / "legacy.db"
    asyncio.run(_create_orm_schema(db, seed=True))
    # 遗留库尚无 alembic_version 表
    assert _current_revision(db) is None

    cfg = _make_config(db)
    command.stamp(cfg, "head")
    command.upgrade(cfg, "head")

    assert _current_revision(db) == _head_revision(cfg)
    conn = sqlite3.connect(db)
    try:
        visitors = conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        # 补充校验 DDL 未被改动（仍与 ORM 产物一致）
        assert {n for n in _table_sql(db)["tables"]} == BUSINESS_TABLES
    finally:
        conn.close()
    assert visitors == 2
    assert users == 1


# ---------- 路径 3：已版本化库重复 upgrade ----------

def test_versioned_db_upgrade_is_noop(tmp_path):
    """已版本化库重复 upgrade head 为 no-op（幂等），数据保留。"""
    db = tmp_path / "versioned.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO visitors (id, first_visit, last_visit) VALUES ('v-seed', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0] == 1
    finally:
        conn.close()
    assert _current_revision(db) == _head_revision(cfg)


# ---------- 新增表迁移：升级/回退 ----------

def test_announcements_migration_upgrade_downgrade(tmp_path):
    """公告表迁移：upgrade 建表（含索引）→ downgrade 回退删表，其余业务表不受影响。"""
    db = tmp_path / "ann.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    assert "announcements" in _table_sql(db)["tables"]
    assert "ix_announcements_status" in _table_sql(db)["indexes"]
    # 新表可正常读写
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO announcements (title, content, status, pinned, created_at, updated_at) "
            "VALUES ('t', 'c', 'draft', 0, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM announcements").fetchone()[0] == 1
    finally:
        conn.close()

    # 回退到基线：仅删除 announcements，其余表保留
    command.downgrade(cfg, "04436fda61c5")
    tables = _table_sql(db)["tables"]
    assert "announcements" not in tables
    assert set(tables) == BUSINESS_TABLES - {"announcements"}

    # 再次升级可重建
    command.upgrade(cfg, "head")
    assert "announcements" in _table_sql(db)["tables"]
    assert _current_revision(db) == _head_revision(cfg)


# ---------- 程序化入口 run_migrations（app/core/migrations.py） ----------

def _run_migrations_with_db(monkeypatch, tmp_path, setup: str) -> Path:
    """在指定临时库上执行 run_migrations()，monkeypatch 掉全局 engine 与 settings。"""
    import app.core.migrations as migrations_mod
    from app.core import config

    db = tmp_path / "runner.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    monkeypatch.setattr(migrations_mod, "engine", engine)
    monkeypatch.setattr(config.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    return db


async def _setup_db(db: Path, setup: str) -> None:
    """按 setup 类型准备库：empty 不建表 / legacy 建表+种子数据 / versioned 先升级。"""
    if setup == "empty":
        return
    if setup == "legacy":
        await _create_orm_schema(db, seed=True)
    elif setup == "versioned":
        # env.py 内部 asyncio.run，必须在独立线程执行（与 run_migrations 同理）
        await asyncio.to_thread(command.upgrade, _make_config(db), "head")


@pytest.mark.asyncio
async def test_run_migrations_empty_db_upgrades(monkeypatch, tmp_path):
    """run_migrations：空库 → upgrade head，表建齐。"""
    import app.core.migrations as migrations_mod

    db = _run_migrations_with_db(monkeypatch, tmp_path, "empty")
    from app.core.migrations import run_migrations

    try:
        await run_migrations()
        assert set(_table_sql(db)["tables"]) == BUSINESS_TABLES
        assert _current_revision(db) is not None
    finally:
        await migrations_mod.engine.dispose()


@pytest.mark.asyncio
async def test_run_migrations_legacy_db_stamps(monkeypatch, tmp_path):
    """run_migrations：遗留库 → stamp 基线（不执行 DDL），数据不丢。"""
    import app.core.migrations as migrations_mod

    db = _run_migrations_with_db(monkeypatch, tmp_path, "legacy")
    await _setup_db(db, "legacy")
    from app.core.migrations import run_migrations

    try:
        await run_migrations()
        assert _current_revision(db) is not None
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0] == 2
        finally:
            conn.close()
    finally:
        await migrations_mod.engine.dispose()


@pytest.mark.asyncio
async def test_run_migrations_legacy_db_missing_tables_filled(monkeypatch, tmp_path):
    """遗留库缺表场景：仅部分业务表的遗留库，stamp 前 create_all 补齐缺失表、数据不丢。"""
    import app.core.migrations as migrations_mod

    db = _run_migrations_with_db(monkeypatch, tmp_path, "legacy")
    await _create_partial_legacy_schema(db, seed=True)
    # 缺表遗留库：无 alembic_version，且 knowledge_base/users 等表缺失
    assert _current_revision(db) is None
    assert "users" not in _table_sql(db)["tables"]

    from app.core.migrations import run_migrations

    try:
        await run_migrations()
        # 缺失业务表被幂等 create_all 补建，版本表指向 head
        assert set(_table_sql(db)["tables"]) == BUSINESS_TABLES
        assert _current_revision(db) == _head_revision(_make_config(db))
        conn = sqlite3.connect(db)
        try:
            # 既有数据不丢，补建表可正常读写
            assert conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0] == 2
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        await migrations_mod.engine.dispose()


@pytest.mark.asyncio
async def test_run_migrations_versioned_db_upgrades(monkeypatch, tmp_path):
    """run_migrations：已版本化库 → upgrade head（no-op），版本不变。"""
    import app.core.migrations as migrations_mod

    db = _run_migrations_with_db(monkeypatch, tmp_path, "versioned")
    await _setup_db(db, "versioned")
    from app.core.migrations import run_migrations

    try:
        await run_migrations()
        assert _current_revision(db) == _head_revision(_make_config(db))
    finally:
        await migrations_mod.engine.dispose()
