"""短期记忆存储层（app/core/memory_store.py）与过期清理任务单测。

覆盖：会话创建/读取/追加/截断（_MAX_HISTORY_MESSAGES 语义）、TTL 滑动
窗口、过期清理（显式 now）、删除、重启恢复（同一文件重建实例）、模块级
单例重置、后台清理函数（SQLite 过期行 + 进程内缓存驱逐）。
"""

import time

from app.core import memory_store
from app.core.memory_store import (
    ConversationMemoryStore,
    SOURCE_CLIENT,
    SOURCE_LANGCHAIN,
)
from app.tasks.memory_cleanup import cleanup_expired_memories


def _make_store(tmp_path, ttl_seconds=86400):
    s = ConversationMemoryStore(db_path=str(tmp_path / "mem.db"), ttl_seconds=ttl_seconds)
    return s


def test_create_and_load_session(tmp_path):
    store = _make_store(tmp_path)
    store.create_session("c1", user="user-1", source=SOURCE_LANGCHAIN)
    session = store.load_session("c1")
    assert session is not None
    assert session["conversation_id"] == "c1"
    assert session["user"] == "user-1"
    assert session["source"] == SOURCE_LANGCHAIN
    assert session["messages"] == []
    assert session["ttl_expires_at"] > session["created_at"]
    store.close()


def test_load_missing_session_returns_none(tmp_path):
    store = _make_store(tmp_path)
    assert store.load_session("ghost") is None
    store.close()


def test_append_and_load_messages(tmp_path):
    store = _make_store(tmp_path)
    store.create_session("c1")
    assert (
        store.append_messages("c1", [("human", "你好"), ("ai", "你好呀")], max_messages=20)
        is True
    )
    session = store.load_session("c1")
    assert session["messages"] == [
        {"role": "human", "content": "你好"},
        {"role": "ai", "content": "你好呀"},
    ]
    store.close()


def test_append_truncates_oldest(tmp_path):
    """超限丢弃最早消息（与进程内 _MAX_HISTORY_MESSAGES 语义一致）。"""
    store = _make_store(tmp_path)
    store.create_session("c1")
    for i in range(3):
        assert store.append_messages("c1", [("human", f"q{i}"), ("ai", f"a{i}")], max_messages=2)
    session = store.load_session("c1")
    assert session["messages"] == [
        {"role": "human", "content": "q2"},
        {"role": "ai", "content": "a2"},
    ]
    store.close()


def test_append_missing_session_returns_false(tmp_path):
    store = _make_store(tmp_path)
    assert (
        store.append_messages("ghost", [("human", "x"), ("ai", "y")], max_messages=20)
        is False
    )
    assert store.count_sessions() == 0
    store.close()


def test_append_refreshes_ttl_sliding_window(tmp_path):
    """TTL 滑动窗口：追加消息后过期时间刷新为 now + TTL。"""
    store = _make_store(tmp_path, ttl_seconds=86400)
    store.create_session("c1", now=1000.0)
    assert store.append_messages("c1", [("human", "q"), ("ai", "a")], max_messages=20, now=1500.0)
    session = store.load_session("c1", now=1500.0)
    assert session["ttl_expires_at"] == 1500.0 + 86400
    store.close()


def test_load_expired_returns_none_and_deletes_row(tmp_path):
    store = _make_store(tmp_path, ttl_seconds=86400)
    store.create_session("c1", now=1000.0)
    # 过期后读取：返回 None 且行被惰性删除
    assert store.load_session("c1", now=1000.0 + 86400 + 1) is None
    assert store.count_sessions() == 0
    store.close()


def test_append_expired_returns_false_and_deletes_row(tmp_path):
    store = _make_store(tmp_path, ttl_seconds=86400)
    store.create_session("c1", now=1000.0)
    assert store.append_messages("c1", [("human", "q"), ("ai", "a")], max_messages=20, now=9999999999.0) is False
    assert store.count_sessions() == 0
    store.close()


def test_cleanup_expired(tmp_path):
    store = _make_store(tmp_path, ttl_seconds=86400)
    store.create_session("c1", now=1000.0)   # expires 87400
    store.create_session("c2", now=1000.0)   # expires 87400
    store.create_session("c3", now=2000.0)   # expires 88400
    assert store.cleanup_expired(now=1500.0) == 0  # 未到过期点
    assert store.count_sessions() == 3
    assert store.cleanup_expired(now=88000.0) == 2  # c1/c2 过期删除
    assert store.count_sessions() == 1
    assert store.load_session("c3", now=88000.0) is not None
    store.close()


def test_delete_session(tmp_path):
    store = _make_store(tmp_path)
    store.create_session("c1")
    store.delete_session("c1")
    assert store.load_session("c1") is None
    assert store.count_sessions() == 0
    store.close()


def test_restart_recovery(tmp_path):
    """同一 SQLite 文件重建实例（模拟重启）后会话与消息可恢复。"""
    path = str(tmp_path / "mem.db")
    s1 = ConversationMemoryStore(db_path=path, ttl_seconds=86400)
    s1.create_session("c1", user="u1", source=SOURCE_LANGCHAIN)
    s1.append_messages("c1", [("human", "你好"), ("ai", "你好呀")], max_messages=20)
    s1.close()

    s2 = ConversationMemoryStore(db_path=path, ttl_seconds=86400)
    session = s2.load_session("c1")
    assert session is not None
    assert session["user"] == "u1"
    assert session["source"] == SOURCE_LANGCHAIN
    assert session["messages"] == [
        {"role": "human", "content": "你好"},
        {"role": "ai", "content": "你好呀"},
    ]
    s2.close()


def test_module_singleton_get_and_reset(tmp_path):
    """模块级单例：get_store 懒初始化，reset_store 可重建（测试隔离）。"""
    path = str(tmp_path / "mem.db")
    s = memory_store.reset_store(db_path=path)
    assert memory_store.get_store() is s
    s2 = memory_store.reset_store(db_path=":memory:")
    assert memory_store.get_store() is s2
    assert s2 is not s
    assert s2 is not None


def test_cleanup_expired_memories(monkeypatch, tmp_path):
    """后台清理函数：SQLite 过期行删除 + 进程内过期缓存驱逐，双保险。"""
    path = str(tmp_path / "mem.db")
    memory_store.reset_store(db_path=path)
    store = memory_store.get_store()
    # 过期行：创建时间极早（TTL 早已到期）
    store.create_session("expired-db", now=1.0)
    # 未过期行
    store.create_session("alive-db", now=time.time())

    from app.services import chat_agent

    with chat_agent._conversations_lock:
        chat_agent._conversations["alive-cache"] = []
        chat_agent._conversations["expired-cache"] = []
        chat_agent._conversation_meta["alive-cache"] = chat_agent._SessionMeta(
            expires_at=time.time() + 3600, user="v", source=SOURCE_CLIENT
        )
        chat_agent._conversation_meta["expired-cache"] = chat_agent._SessionMeta(
            expires_at=time.time() - 1, user="v", source=SOURCE_CLIENT
        )
    try:
        total = cleanup_expired_memories()
        assert total == 2  # expired-db 行 + expired-cache 缓存
        assert store.count_sessions() == 1
        assert store.load_session("alive-db") is not None
        with chat_agent._conversations_lock:
            assert "expired-cache" not in chat_agent._conversations
            assert "expired-cache" not in chat_agent._conversation_meta
            assert "alive-cache" in chat_agent._conversations
    finally:
        with chat_agent._conversations_lock:
            chat_agent._conversations.clear()
            chat_agent._conversation_meta.clear()
