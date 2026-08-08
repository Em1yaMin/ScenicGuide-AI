"""管理员 agent 两阶段二次确认存储（批次6 B4）单测。

覆盖：登记待确认项（operation_id/工具名/参数/摘要）、get 查询、
consume 取出即删（防重放）、TTL 过期失效（惰性清理）、空 id/未知 id、
TTL 配置读取容错。
"""

import time

import pytest

from app.core.config import settings
from app.services.admin_agent import confirm


@pytest.fixture(autouse=True)
def _clear_pending():
    """每个测试清空待确认项存储（防跨测试污染）。"""
    with confirm._lock:
        confirm._pending.clear()
    yield
    with confirm._lock:
        confirm._pending.clear()


# ── 登记与查询 ────────────────────────────────────────────────────────────


def test_create_pending_generates_operation_id():
    """登记待确认项：生成唯一 operation_id，参数/摘要原样保存。"""
    op = confirm.create_pending(
        "delete_knowledge", {"kb_id": 3}, "知识条目 #3《灵山简介》"
    )
    assert op.operation_id and isinstance(op.operation_id, str)
    assert op.tool == "delete_knowledge"
    assert op.params == {"kb_id": 3}
    assert op.summary == "知识条目 #3《灵山简介》"
    # 可经 get_pending 查到（未确认前有效）
    assert confirm.get_pending(op.operation_id) is op


def test_get_pending_returns_none_for_unknown_or_empty():
    """未知/空 operation_id 查询返回 None。"""
    assert confirm.get_pending("") is None
    assert confirm.get_pending("no-such-id") is None


def test_consume_is_idempotent():
    """consume 取出即删除：同一 operation_id 只能确认一次（防重放）。"""
    op = confirm.create_pending("delete_knowledge", {"kb_id": 1}, "知识条目 #1")
    consumed = confirm.consume(op.operation_id)
    assert consumed is op
    # 再次确认返回 None（重复确认失效）
    assert confirm.consume(op.operation_id) is None
    assert confirm.get_pending(op.operation_id) is None


# ── TTL 过期 ──────────────────────────────────────────────────────────────


def test_pending_expires_after_ttl(monkeypatch):
    """超过 TTL 的待确认项自动失效（惰性清理），consume 返回 None。"""
    monkeypatch.setattr(settings, "ADMIN_AGENT_CONFIRM_TTL_SECONDS", 0)
    op = confirm.create_pending("delete_user_memory", {"memory_id": "abc"}, "记忆")
    # TTL=0：创建即过期
    assert confirm.get_pending(op.operation_id) is None
    assert confirm.consume(op.operation_id) is None


def test_pending_alive_before_ttl(monkeypatch):
    """TTL 内待确认项有效，TTL 后失效（时间推进模拟）。"""
    monkeypatch.setattr(settings, "ADMIN_AGENT_CONFIRM_TTL_SECONDS", 60)
    op = confirm.create_pending("delete_knowledge", {"kb_id": 2}, "知识条目 #2")
    assert confirm.get_pending(op.operation_id) is not None
    # 模拟时间流逝（created_at 前移超过 TTL）
    with confirm._lock:
        confirm._pending[op.operation_id].created_at = time.time() - 120
    assert confirm.get_pending(op.operation_id) is None
    assert confirm.consume(op.operation_id) is None


def test_expired_entries_lazily_cleaned(monkeypatch):
    """过期项被惰性清理（不再占用存储）。"""
    monkeypatch.setattr(settings, "ADMIN_AGENT_CONFIRM_TTL_SECONDS", 0)
    op1 = confirm.create_pending("delete_knowledge", {"kb_id": 1}, "知识条目 #1")
    op2 = confirm.create_pending("delete_knowledge", {"kb_id": 2}, "知识条目 #2")
    assert confirm.get_pending(op1.operation_id) is None  # 触发清理
    with confirm._lock:
        assert op2.operation_id not in confirm._pending  # 全部过期清理
        assert op1.operation_id not in confirm._pending


# ── 配置容错 ──────────────────────────────────────────────────────────────


def test_ttl_read_failure_falls_back(monkeypatch):
    """TTL 配置读取异常时回退默认 120s（不抛错）。"""
    monkeypatch.setattr(settings, "ADMIN_AGENT_CONFIRM_TTL_SECONDS", "not-a-number")
    assert confirm._ttl_seconds() == 120
