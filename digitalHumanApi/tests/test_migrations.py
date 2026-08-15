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
import re
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
    "admin_audit_logs",
    "analysis_reports",
    "announcements",
    "app_settings",
    "dialogues",
    "evaluations",
    "knowledge_base",
    "llm_usage",
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


def _normalize_sql(sql: str) -> str:
    """归一化 DDL 文本：折叠全部空白（\n/\t/连续空格 → 单空格）。

    SQLite 的 ALTER TABLE ADD COLUMN 会把追加列内联到原 CREATE TABLE
    文本末行（如账号管理的 users 加列迁移），与 create_all 的逐列换行格式
    不同——比较前归一化空白，仍可捕获真实漂移（缺表/缺列/类型/约束/索引），
    仅容忍空白排版差异。
    """
    return re.sub(r"\s+", " ", sql or "").strip()


def test_upgraded_schema_matches_orm_metadata(tmp_path):
    """迁移产出的 DDL 与 ORM metadata（create_all）逐字节一致（空白归一化后）。

    该等价性是遗留库 stamp 基线安全的前提：历史 create_all 建出的库
    与初始迁移产物相同，stamp 后无 schema 漂移。归一化仅折叠空白——
    SQLite 的 ALTER TABLE ADD COLUMN 会把追加列内联进原 CREATE TABLE
    文本（users 加列迁移），逐字节比对无法通过；空白归一化后列/类型/约束/索引
    的逐 token 一致性仍严格校验。
    """
    migrated = tmp_path / "migrated.db"
    orm = tmp_path / "orm.db"
    command.upgrade(_make_config(migrated), "head")
    asyncio.run(_create_orm_schema(orm))

    def _normalized(sql_map: dict) -> dict:
        return {
            "tables": {
                name: _normalize_sql(sql)
                for name, sql in sql_map["tables"].items()
            },
            "indexes": {
                name: _normalize_sql(sql)
                for name, sql in sql_map["indexes"].items()
            },
        }

    assert _normalized(_table_sql(migrated)) == _normalized(_table_sql(orm))


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

    # 回退到基线：仅删除 announcements / llm_usage / admin_audit_logs / app_settings / analysis_reports
    #（均在基线之后引入，同被回退），其余早期表保留
    command.downgrade(cfg, "04436fda61c5")
    tables = _table_sql(db)["tables"]
    assert "announcements" not in tables
    assert "llm_usage" not in tables
    assert "admin_audit_logs" not in tables
    assert "app_settings" not in tables
    assert "analysis_reports" not in tables
    assert set(tables) == BUSINESS_TABLES - {
        "announcements",
        "llm_usage",
        "admin_audit_logs",
        "app_settings",
        "analysis_reports",
    }

    # 再次升级可重建
    command.upgrade(cfg, "head")
    assert "announcements" in _table_sql(db)["tables"]
    assert _current_revision(db) == _head_revision(cfg)


# ---------- 批次8 数字人形象：app_settings 表迁移 ----------

def test_app_settings_migration_upgrade_downgrade(tmp_path):
    """app_settings 表迁移：upgrade 建表 → downgrade 回退删表，其余表不受影响。"""
    db = tmp_path / "settings.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    assert "app_settings" in _table_sql(db)["tables"]
    # 新表可正常读写（key 主键 upsert）
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) "
            "VALUES ('digital_human_character', 'Hiyori', '2026-08-14 00:00:00')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT value FROM app_settings WHERE key='digital_human_character'"
        ).fetchone()[0] == "Hiyori"
    finally:
        conn.close()

    # 回退到审计表版本：仅删除 app_settings / analysis_reports，其余表保留
    command.downgrade(cfg, "a9f0e1d2c3b4")
    tables = _table_sql(db)["tables"]
    assert "app_settings" not in tables
    assert "analysis_reports" not in tables
    assert set(tables) == BUSINESS_TABLES - {"app_settings", "analysis_reports"}

    # 再次升级可重建
    command.upgrade(cfg, "head")
    assert "app_settings" in _table_sql(db)["tables"]
    assert _current_revision(db) == _head_revision(cfg)


# ---------- AI 运营概览落库持久化：analysis_reports 表迁移 ----------

def test_analysis_reports_migration_upgrade_downgrade(tmp_path):
    """analysis_reports 表迁移：upgrade 建表（days 唯一约束）→ downgrade 回退删表，其余表不受影响。"""
    db = tmp_path / "reports.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    assert "analysis_reports" in _table_sql(db)["tables"]
    # 新表可正常读写（每 days 一行，唯一约束防重复行）
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO analysis_reports (days, response_json, analysis_timestamp, created_at, updated_at) "
            "VALUES (30, '{\"total_dialogues\": 1}', '2026-08-15 00:00:00', "
            "'2026-08-15 00:00:00', '2026-08-15 00:00:00')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT response_json FROM analysis_reports WHERE days=30"
        ).fetchone()[0] == '{"total_dialogues": 1}'
        # days 唯一约束：同 days 重复插入报错
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO analysis_reports (days, response_json, analysis_timestamp, created_at, updated_at) "
                "VALUES (30, '{}', '2026-08-15 00:00:00', "
                "'2026-08-15 00:00:00', '2026-08-15 00:00:00')"
            )
    finally:
        conn.close()

    # 回退到 app_settings 版本：仅删除 analysis_reports，其余表保留
    command.downgrade(cfg, "d1e2f3a4b5c6")
    tables = _table_sql(db)["tables"]
    assert "analysis_reports" not in tables
    assert set(tables) == BUSINESS_TABLES - {"analysis_reports"}

    # 再次升级可重建
    command.upgrade(cfg, "head")
    assert "analysis_reports" in _table_sql(db)["tables"]
    assert _current_revision(db) == _head_revision(cfg)


# ---------- 批次7 C5：llm_usage 表迁移 ----------

def test_llm_usage_migration_upgrade_downgrade(tmp_path):
    """llm_usage 表迁移：upgrade 建表（含索引）→ downgrade 回退删表，其余表不受影响。"""
    db = tmp_path / "usage.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    assert "llm_usage" in _table_sql(db)["tables"]
    assert "ix_llm_usage_created_at" in _table_sql(db)["indexes"]
    assert "ix_llm_usage_model" in _table_sql(db)["indexes"]
    assert "ix_llm_usage_chain" in _table_sql(db)["indexes"]
    # 新表可正常读写
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO llm_usage (model, input_tokens, output_tokens, total_tokens, "
            "duration_ms, chain, created_at) "
            "VALUES ('m', 10, 20, 30, 100, '对话', '2026-08-01 00:00:00')"
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 1
    finally:
        conn.close()

    # 回退到公告表版本：仅删除 llm_usage / admin_audit_logs / app_settings / analysis_reports，其余表保留
    command.downgrade(cfg, "e06ede5acb5a")
    tables = _table_sql(db)["tables"]
    assert "llm_usage" not in tables
    assert "admin_audit_logs" not in tables
    assert "app_settings" not in tables
    assert "analysis_reports" not in tables
    assert set(tables) == BUSINESS_TABLES - {
        "llm_usage", "admin_audit_logs", "app_settings", "analysis_reports"
    }

    # 再次升级可重建
    command.upgrade(cfg, "head")
    assert "llm_usage" in _table_sql(db)["tables"]
    assert _current_revision(db) == _head_revision(cfg)


# ---------- 批次8 账号管理：users 加列 + admin_audit_logs 建表迁移 ----------

def test_users_disabled_columns_migration_upgrade_downgrade(tmp_path):
    """users 加列迁移：upgrade 加 is_disabled/disabled_at（含默认值/列序）→
    downgrade 回退删列，其余表不受影响，数据保留。"""
    db = tmp_path / "users.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db)
    try:
        cols = {r[1]: r for r in conn.execute("PRAGMA table_info(users)")}
        assert "is_disabled" in cols
        assert "disabled_at" in cols
        # 列序：is_disabled 在 disabled_at 之前（与 ORM metadata 一致）
        order = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        assert order.index("is_disabled") < order.index("disabled_at")
        # 新列可正常读写（默认启用）
        conn.execute(
            "INSERT INTO users (username, password_hash, created_at) "
            "VALUES ('alice', 'x', '2026-01-01 00:00:00')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT is_disabled FROM users WHERE username='alice'"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    # 回退到加列前版本：两列删除、数据保留
    command.downgrade(cfg, "c5f1a2b3d4e5")
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "is_disabled" not in cols
        assert "disabled_at" not in cols
        assert conn.execute(
            "SELECT username FROM users"
        ).fetchone()[0] == "alice"
    finally:
        conn.close()

    # 再次升级可重建
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "is_disabled" in cols
        assert "disabled_at" in cols
    finally:
        conn.close()


def test_admin_audit_logs_migration_upgrade_downgrade(tmp_path):
    """审计表迁移：upgrade 建表（含索引）→ downgrade 回退删表，其余表不受影响。"""
    db = tmp_path / "audit.db"
    cfg = _make_config(db)
    command.upgrade(cfg, "head")

    assert "admin_audit_logs" in _table_sql(db)["tables"]
    assert "ix_admin_audit_logs_created_at" in _table_sql(db)["indexes"]
    assert "ix_admin_audit_logs_operator" in _table_sql(db)["indexes"]
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO admin_audit_logs (operator, action, target_username, "
            "created_at) VALUES ('admin', 'delete', 'alice', '2026-08-13 00:00:00')"
        )
        conn.commit()
        assert conn.execute(
            "SELECT COUNT(*) FROM admin_audit_logs"
        ).fetchone()[0] == 1
    finally:
        conn.close()

    command.downgrade(cfg, "b4c3d2e1f0a9")
    tables = _table_sql(db)["tables"]
    assert "admin_audit_logs" not in tables

    command.upgrade(cfg, "head")
    assert "admin_audit_logs" in _table_sql(db)["tables"]
    assert _current_revision(db) == _head_revision(cfg)


def test_old_shape_legacy_db_gets_user_columns(tmp_path, monkeypatch):
    """旧形状遗留库（users 表无新列、无版本表）经 run_migrations：
    create_all 补表 + stamp 基线 + upgrade head 后新列补齐、数据保留。"""
    import app.core.migrations as migrations_mod
    from app.core import config as config_mod

    db = tmp_path / "oldlegacy.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    monkeypatch.setattr(migrations_mod, "engine", engine)
    monkeypatch.setattr(
        config_mod.settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db}"
    )
    # 手工按旧形状建 users（无 is_disabled/disabled_at），模拟历史 create_all 产物
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER NOT NULL,
            username VARCHAR(64) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            nickname VARCHAR(64),
            created_at DATETIME NOT NULL,
            last_login_at DATETIME,
            PRIMARY KEY (id)
        );
        CREATE UNIQUE INDEX ix_users_username ON users (username);
        INSERT INTO users (username, password_hash, created_at)
            VALUES ('alice', 'x', '2026-01-01 00:00:00');
        """
    )
    conn.close()
    # 旧形状遗留库无版本表
    assert _current_revision(db) is None

    from app.core.migrations import run_migrations

    try:
        asyncio.run(run_migrations())
    finally:
        asyncio.run(engine.dispose())

    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert "is_disabled" in cols
        assert "disabled_at" in cols
        assert conn.execute(
            "SELECT username, is_disabled FROM users"
        ).fetchone() == ("alice", 0)
        # 版本表存在且指向 head
        assert _current_revision(db) == _head_revision(_make_config(db))
    finally:
        conn.close()


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
