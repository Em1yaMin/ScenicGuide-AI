"""Alembic 迁移程序化入口（启动时自动迁移，见 doc/alembic-migrations.md）。

main.py lifespan 与独立 CLI（app/tasks/backfill.py）调用 run_migrations()，
对三种数据库状态做平滑处理（旧库不丢数据、新库自动建齐）：

- 空库（无任何表）          → alembic upgrade head：从零建齐全部表
- 遗留库（有业务表、无版本表） → 先幂等 create_all（IF NOT EXISTS）补建历史增量引入的
                            缺失业务表（knowledge_base/users 等），再 alembic stamp head
                            基线化当前版本（schema 与初始迁移等价，升级前先写死版本号）
- 已版本化库（有 alembic_version） → alembic upgrade head：应用待执行迁移

注意：Alembic 命令为同步 API，且 env.py 内部使用 asyncio.run（async 模板），
因此必须在独立线程（asyncio.to_thread）中执行，避免与运行中事件循环冲突。
"""
import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

import app.models  # noqa: F401（确保 Base.metadata 全量模型已注册，create_all 依赖）
from app.core.database import Base, engine

logger = logging.getLogger(__name__)

# alembic.ini 位于 digitalHumanApi/ 根目录（与 migrations/ 平级）
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _alembic_config() -> Config:
    """构造 Alembic 配置（url 留空，env.py 回退到 settings.DATABASE_URL）。

    标记 programmatic 属性：env.py 据此跳过 fileConfig——进程内迁移不得重配日志
    （fileConfig 默认禁用既有 logger，会把应用全部日志清空），CLI 直跑不受影响。
    """
    cfg = Config(str(_ALEMBIC_INI))
    cfg.attributes["programmatic"] = True
    return cfg


async def _list_tables() -> set[str]:
    """返回数据库中已存在的表名（含 alembic_version 等系统表）。"""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        return {row[0] for row in result}


def _run_alembic(cmd: str) -> None:
    """在线程中执行 Alembic 命令（upgrade/stamp 到 head）。"""
    cfg = _alembic_config()
    if cmd == "stamp":
        command.stamp(cfg, "head")
    else:
        command.upgrade(cfg, "head")


async def run_migrations() -> None:
    """程序化执行 Alembic 迁移（幂等，可重复调用）。

    按数据库当前状态自动选择 stamp / upgrade，保证：
    - 已部署的现有库平滑过渡（stamp 基线，零 DDL、零数据丢失）；
    - 新库从零建齐全部表；
    - 已有版本表的库应用待执行迁移。
    """
    tables = await _list_tables()
    if "alembic_version" in tables:
        cmd = "upgrade"
    elif tables:
        # 遗留库：无版本表但有业务表 → 先幂等 create_all 补建缺失业务表再 stamp 基线
        #（业务表历史增量引入，直接 stamp 会把缺表状态永久钉死在当前版本）
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        cmd = "stamp"
        logger.info(
            "检测到遗留数据库（无 alembic_version），create_all 补建缺失表后 stamp 基线为当前 head"
        )
    else:
        cmd = "upgrade"
        logger.info("检测到空数据库，alembic upgrade head 建齐全部表")
    await asyncio.to_thread(_run_alembic, cmd)
    logger.info("数据库迁移完成（%s head）", cmd)
