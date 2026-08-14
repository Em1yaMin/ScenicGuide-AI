"""Alembic 迁移程序化入口（启动时自动迁移，见 doc/alembic-migrations.md）。

main.py lifespan 与独立 CLI（app/tasks/backfill.py）调用 run_migrations()，
对三种数据库状态做平滑处理（旧库不丢数据、新库自动建齐）：

- 空库（无任何表）          → alembic upgrade head：从零建齐全部表
- 遗留库（有业务表、无版本表） → 先幂等 create_all（IF NOT EXISTS）补建历史增量引入的
                            缺失业务表，再 alembic stamp 基线并 upgrade head：重放全部迁移
                            ——迁移全部防御性（幂等）编写，create_all 已建的表/索引/列自动
                            跳过，仅「对既有表的列变更」（如账号管理的 users 加列）在旧形状
                            遗留库上真正执行；新形状遗留库（create_all 产物已含最新列）零 DDL
- 已版本化库（有 alembic_version） → alembic upgrade head：应用待执行迁移

注意：Alembic 命令为同步 API，且 env.py 内部使用 asyncio.run（async 模板），
因此必须在独立线程（asyncio.to_thread）中执行，避免与运行中事件循环冲突。
新增迁移必须防御性（幂等）编写（建表前查 has_table、加列前查列存在性、
建索引前查索引存在性），否则遗留库路径（stamp 基线 + 重放全部迁移）会报重复 DDL。
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
    - 已部署的现有库平滑过渡（遗留库 create_all 补表后 stamp 上一版本再 upgrade，
      使既有表的列变更迁移生效；已版本化库直接 upgrade）；
    - 新库从零建齐全部表；
    - 已有版本表的库应用待执行迁移。

    遗留库 stamp 到 head 上一版本的说明：直接 stamp head 会把旧形状遗留库
    （历史 create_all 产物，无新增列）永久钉死在缺失列状态；stamp 上一版本后
    upgrade 只应用 head 与上一版本之间的迁移（当前为 b4c3d2e1f0a9 加列迁移，
    内含列存在性防御检查）。新增迁移须遵循同样防御性写法（create_all 已建表/
    已加列的库上 upgrade 应幂等跳过），避免对既有表的重复 DDL 报错。
    """
    tables = await _list_tables()
    if "alembic_version" in tables:
        await asyncio.to_thread(_run_alembic, "upgrade")
        logger.info("数据库迁移完成（upgrade head）")
        return
    if tables:
        # 遗留库：无版本表但有业务表 → 先幂等 create_all 补建缺失业务表，
        # 再 stamp 基线 + upgrade head 重放全部迁移（迁移全部防御性编写：
        # create_all 已建的表/索引/列自动跳过，仅旧形状遗留库缺的列变更真正执行）
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        cfg = _alembic_config()
        from alembic.script import ScriptDirectory

        sd = ScriptDirectory.from_config(cfg)
        head = sd.get_current_head()
        # 沿 down_revision 链回溯到基线（down_revision=None 的起始迁移）
        rev = sd.get_revision(head)
        while rev is not None and rev.down_revision is not None:
            rev = sd.get_revision(rev.down_revision)
        base_rev = rev.revision if rev is not None else None
        await asyncio.to_thread(command.stamp, cfg, base_rev)
        await asyncio.to_thread(command.upgrade, cfg, "head")
        logger.info(
            "检测到遗留数据库（无 alembic_version），create_all 补建缺失表后 "
            "stamp 基线 %s 并 upgrade 到 head %s",
            base_rev,
            head,
        )
        logger.info("数据库迁移完成（stamp+upgrade head）")
        return
    await asyncio.to_thread(_run_alembic, "upgrade")
    logger.info("检测到空数据库，alembic upgrade head 建齐全部表")
    logger.info("数据库迁移完成（upgrade head）")
