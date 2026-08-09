"""备份脚本冒烟测试（批次8 C7 数据库备份策略）。

覆盖：创建临时库 → 备份脚本执行 → 恢复验证（临时库打开 + 表计数/数据比对）
+ 保留份数清理 + 错误路径。脚本用 sqlite3 CLI 的 .backup 在线安全备份，
未安装 sqlite3 CLI 时跳过（CI ubuntu runner / 安装了 sqlite3 的机器可跑）。

脚本实现与 Docker volume 路径说明见 doc/database-backup.md。
"""

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

BACKUP_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "backup_db.sh"
)

needs_sqlite3 = pytest.mark.skipif(
    shutil.which("sqlite3") is None,
    reason="需要 sqlite3 CLI（Debian/Ubuntu: sudo apt-get install sqlite3）",
)


def _create_source_db(db_path: Path) -> None:
    """构造一个含多表 + 数据的源库（模拟 digital_human.db 的业务形态）。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE spots (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO spots (name) VALUES ('灵山'), ('大佛')")
    conn.execute("CREATE TABLE routes (id INTEGER PRIMARY KEY, name TEXT)")
    conn.commit()
    conn.close()


def _run_script(db_path: Path, backup_dir: Path, keep: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(BACKUP_SCRIPT), str(db_path), str(backup_dir), keep],
        capture_output=True,
        text=True,
    )


def _table_count(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table';"
        ).fetchone()[0]
    finally:
        conn.close()


@needs_sqlite3
def test_backup_create_and_restore(tmp_path):
    """创建临时库 → 备份 → 恢复验证（临时库打开 + 表计数/数据比对）。"""
    db = tmp_path / "digital_human.db"
    backup_dir = tmp_path / "backups"
    _create_source_db(db)
    assert _table_count(db) == 2

    result = _run_script(db, backup_dir, "7")
    assert result.returncode == 0, result.stderr
    assert "备份完成" in result.stdout

    # 备份文件生成（时间戳命名：<库名>_backup_YYYYmmdd_HHMMSS.db）
    backups = list(backup_dir.glob("digital_human_backup_*.db"))
    assert len(backups) == 1
    assert re.fullmatch(
        r"digital_human_backup_\d{8}_\d{6}\.db", backups[0].name
    ), backups[0].name

    # 恢复验证：把备份拷贝为恢复库独立打开，表数量一致 + 数据完整
    restore_path = tmp_path / "restore.db"
    shutil.copy2(backups[0], restore_path)
    conn = sqlite3.connect(str(restore_path))
    try:
        assert conn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        assert _table_count(restore_path) == 2
        rows = conn.execute("SELECT name FROM spots ORDER BY id;").fetchall()
        assert rows == [("灵山",), ("大佛",)]
    finally:
        conn.close()


@needs_sqlite3
def test_backup_keep_pruning(tmp_path):
    """保留份数：跑 3 次保留 2 份，目录只剩最新 2 份。"""
    db = tmp_path / "app.db"
    backup_dir = tmp_path / "backups"
    _create_source_db(db)
    for _ in range(3):
        result = _run_script(db, backup_dir, "2")
        assert result.returncode == 0, result.stderr
    backups = sorted(backup_dir.glob("app_backup_*.db"))
    assert len(backups) == 2


@needs_sqlite3
def test_backup_missing_db_fails(tmp_path):
    """源库不存在：非零退出 + 明确错误信息，不创建备份。"""
    result = _run_script(tmp_path / "nope.db", tmp_path / "backups", "7")
    assert result.returncode != 0
    assert "数据库文件不存在" in result.stderr
    assert not (tmp_path / "backups").exists()


@needs_sqlite3
def test_backup_invalid_keep_fails(tmp_path):
    """保留份数非法（0/非数字）：非零退出。"""
    db = tmp_path / "app.db"
    _create_source_db(db)
    result = _run_script(db, tmp_path / "backups", "0")
    assert result.returncode != 0
    assert "保留份数必须为正整数" in result.stderr


@needs_sqlite3
def test_backup_no_temp_leftovers(tmp_path):
    """备份成功后不留 .tmp 临时文件（原子改名路径）。"""
    db = tmp_path / "app.db"
    backup_dir = tmp_path / "backups"
    _create_source_db(db)
    result = _run_script(db, backup_dir, "7")
    assert result.returncode == 0, result.stderr
    assert list(backup_dir.glob("*.tmp")) == []
