"""后台任务：定期清理过期的短期记忆会话（SQLite 过期行 + 进程内缓存）。

与 analysis_loop / recommend_loop 同模式：while True → try/except → sleep →
CancelledError。启动后立即执行一次（满足「启动时 + 定期清理」），随后按
MEMORY_CLEANUP_INTERVAL_SECONDS 周期运行。存储层见 app/core/memory_store.py，
进程内缓存见 chat_agent.evict_expired_conversations。
"""
import asyncio
import logging
import time

from app.core import memory_store
from app.core.config import settings
from app.services import chat_agent

logger = logging.getLogger(__name__)


def cleanup_expired_memories() -> int:
    """执行一轮过期清理，返回清理总数（SQLite 删除行数 + 缓存驱逐数）。

    双保险：TTL 过期后，SQLite 侧删过期行、进程内缓存驱逐过期会话，
    保证同 conversation_id 续聊时按新会话处理（不恢复过期历史）。
    """
    now = time.time()
    deleted = memory_store.get_store().cleanup_expired(now)
    evicted = chat_agent.evict_expired_conversations(now)
    total = deleted + evicted
    if total:
        logger.info(
            "短期记忆过期清理完成: deleted_db=%d evicted_cache=%d",
            deleted,
            evicted,
        )
    return total


async def memory_cleanup_loop() -> None:
    """后台循环：定期清理过期会话（启动立即执行一次）。

    遵循 analysis_loop 模式（app/tasks/analysis.py）：
    while True → try/except → asyncio.sleep → CancelledError。
    """
    interval = settings.MEMORY_CLEANUP_INTERVAL_SECONDS
    logger.info("Memory cleanup task started (interval: %ds)", interval)
    try:
        while True:
            try:
                cleanup_expired_memories()
            except Exception:
                logger.exception(
                    "Memory cleanup cycle failed, will retry in %ds", interval
                )
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Memory cleanup task cancelled, shutting down")
