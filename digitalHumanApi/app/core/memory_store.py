"""短期记忆持久化存储（阶段2 T1：会话历史 SQLite 落盘 + 进程内缓存）。

设计说明（方案选择，船长决策 2026-08-05：进程内 TTL 缓存 + SQLite，暂不上 Redis）：

- **单表 `conversation_sessions`**：一行一个会话，`messages` 为 JSON 消息序列
  （`[{"role": "human"|"ai", "content": ...}, ...]`），表结构覆盖任务要求的
  「conversation_id、user、消息序列（role/content）、时间戳、TTL 过期时间」
  全部要素；会话级读写频率低（每人每轮一次），JSON 整体读改写开销可忽略
- **标准库 sqlite3（同步）**：单连接 + 线程锁串行化，本地文件写 μs 级，
  不阻塞事件循环；不引入额外依赖（不用 aiosqlite/SQLAlchemy 管理本表），
  存储文件独立于主库 digital_human.db（默认 memory_store.db，互不影响）
- **读写策略 = 读走缓存 + 写双写**（chat_agent.py 配套）：
  - 每条消息即时落盘（写双写），崩溃无丢失窗口（优于「启动加载 + 定期落盘」）
  - 重启后懒恢复：首次访问该 conversation_id 时才从 SQLite 读回，避免
    启动全量加载；进程内 `_MAX_CONVERSATIONS` LRU 内存语义不变
    （LRU 淘汰只出缓存、不出库，SQLite 数据保留至 TTL 过期）
  - TTL 滑动窗口：每次追加消息刷新过期时间；过期行由后台任务定期清理
    （app/tasks/memory_cleanup.py）＋ 访问/追加时惰性删除双保险
- **source 字段**：`langchain`（本模块生成的 uuid）| `client`（客户端传入，
  如 Dify 生成的会话 id）。重启恢复时据此重建 chat_agent 的
  `_generated_conversation_ids` 归属判定（双轨回落：LangChain 生成的 id
  对 Dify 未知，回落时传空串让 Dify 新建会话）
"""
import json
import logging
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)

# 会话 source 取值
SOURCE_LANGCHAIN = "langchain"  # 本模块生成的 uuid 会话
SOURCE_CLIENT = "client"        # 客户端传入的会话 id（如 Dify 生成）


class ConversationMemoryStore:
    """会话历史 SQLite 存储（短期记忆）。

    线程安全：单连接 + `_lock` 串行化全部读写（chat_agent 调用路径在
    `_conversations_lock` 内，本锁仅在 `_conversations_lock` 之后获取，
    无锁序倒置）；会话级写频率低，串行化无性能瓶颈。
    """

    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self._db_path = db_path
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._closed = False
        self._init_schema()

    # ── 基础 ──────────────────────────────────────────────────────────────

    def ttl_seconds(self) -> int:
        """会话 TTL（秒）。"""
        return self._ttl_seconds

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    conversation_id TEXT PRIMARY KEY,
                    user TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'client',
                    messages TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    last_active_at REAL NOT NULL,
                    ttl_expires_at REAL NOT NULL
                )
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    # ── 会话 CRUD ─────────────────────────────────────────────────────────

    def create_session(
        self,
        conversation_id: str,
        user: str = "visitor",
        source: str = SOURCE_CLIENT,
        now: Optional[float] = None,
    ) -> None:
        """新建会话（幂等：已存在则整体覆盖为全新空会话）。"""
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO conversation_sessions
                    (conversation_id, user, source, messages, created_at,
                     last_active_at, ttl_expires_at)
                VALUES (?, ?, ?, '[]', ?, ?, ?)
                """,
                (conversation_id, user, source, now, now, now + self._ttl_seconds),
            )
            self._conn.commit()

    def load_session(
        self, conversation_id: str, now: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """按 id 读取会话；不存在或已过期返回 None（过期行顺手删除，惰性清理）。

        Returns:
            {"conversation_id", "user", "source",
             "messages": List[{"role", "content"}],
             "created_at", "last_active_at", "ttl_expires_at"} 或 None
        """
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT conversation_id, user, source, messages, created_at,"
                " last_active_at, ttl_expires_at"
                " FROM conversation_sessions WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            if data["ttl_expires_at"] <= now:
                # 过期：删除该行（惰性清理），按不存在处理
                self._conn.execute(
                    "DELETE FROM conversation_sessions WHERE conversation_id = ?",
                    (conversation_id,),
                )
                self._conn.commit()
                return None
            data["messages"] = json.loads(data["messages"] or "[]")
            return data

    def append_messages(
        self,
        conversation_id: str,
        messages: List[Tuple[str, str]],
        max_messages: int,
        user: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        """追加消息并滑动刷新 TTL；超限丢弃最早消息（与进程内
        `_MAX_HISTORY_MESSAGES` 语义一致，调用方传相同上限）。

        Returns:
            True 追加成功；False 会话不存在/已过期（调用方应按新会话重建）
        """
        now = time.time() if now is None else now
        with self._lock:
            row = self._conn.execute(
                "SELECT user, messages, ttl_expires_at FROM conversation_sessions"
                " WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return False
            if row["ttl_expires_at"] <= now:
                # 过期：删除该行（惰性清理），调用方按新会话处理
                self._conn.execute(
                    "DELETE FROM conversation_sessions WHERE conversation_id = ?",
                    (conversation_id,),
                )
                self._conn.commit()
                return False
            seq = json.loads(row["messages"] or "[]")
            for role, content in messages:
                seq.append({"role": role, "content": content})
            if len(seq) > max_messages:
                del seq[: len(seq) - max_messages]
            new_user = user if user else row["user"]
            self._conn.execute(
                "UPDATE conversation_sessions SET user = ?, messages = ?,"
                " last_active_at = ?, ttl_expires_at = ?"
                " WHERE conversation_id = ?",
                (
                    new_user,
                    json.dumps(seq, ensure_ascii=False),
                    now,
                    now + self._ttl_seconds,
                    conversation_id,
                ),
            )
            self._conn.commit()
            return True

    def delete_session(self, conversation_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM conversation_sessions WHERE conversation_id = ?",
                (conversation_id,),
            )
            self._conn.commit()

    def cleanup_expired(self, now: Optional[float] = None) -> int:
        """删除全部过期会话行（后台清理任务调用）。返回删除行数。"""
        now = time.time() if now is None else now
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM conversation_sessions WHERE ttl_expires_at <= ?",
                (now,),
            )
            self._conn.commit()
            return cur.rowcount

    def count_sessions(self) -> int:
        """当前会话行数（测试/调试用）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM conversation_sessions"
            ).fetchone()
            return row["n"]


# ── 模块级单例（懒初始化；测试用 reset_store 重建） ──────────────────────

_store: Optional[ConversationMemoryStore] = None


def get_store() -> ConversationMemoryStore:
    """获取短期记忆存储单例（首次调用按 settings 初始化，失败上抛由
    调用方降级处理——chat_agent 对持久化失败仅告警，对话不阻断）。"""
    global _store
    if _store is None:
        _store = ConversationMemoryStore(
            db_path=settings.MEMORY_DB_PATH,
            ttl_seconds=int(settings.MEMORY_TTL_DAYS * 86400),
        )
        logger.info(
            "短期记忆存储初始化: path=%s ttl_seconds=%d",
            settings.MEMORY_DB_PATH,
            int(settings.MEMORY_TTL_DAYS * 86400),
        )
    return _store


def reset_store(
    db_path: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> ConversationMemoryStore:
    """重建存储实例（测试隔离 / 配置热更新）。None 时用 settings 默认值。"""
    global _store
    if _store is not None:
        _store.close()
    if db_path is None:
        db_path = settings.MEMORY_DB_PATH
    if ttl_seconds is None:
        ttl_seconds = int(settings.MEMORY_TTL_DAYS * 86400)
    _store = ConversationMemoryStore(db_path=db_path, ttl_seconds=ttl_seconds)
    return _store


def close_store() -> None:
    global _store
    if _store is not None:
        _store.close()
        _store = None
