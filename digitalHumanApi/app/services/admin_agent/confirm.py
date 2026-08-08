"""管理员 agent 删除类工具两阶段二次确认存储（批次6 B4）。

船长决策：删除类操作必须二次确认 + 审计日志。本模块实现「待确认项」存储：

- 删除类工具**首次调用只登记**待确认项（生成 operation_id + 目标摘要），
  不执行任何删除；模型需再次调用 `confirm_operation(operation_id)` 才会
  真正执行（两阶段契约，见 tools.py）
- 待确认项存进程内字典（短 TTL 过期，默认 120s，见
  settings.ADMIN_AGENT_CONFIRM_TTL_SECONDS）：过期/重复确认/未确认均不可执行，
  需重新发起删除请求
- `consume` 取出即删除（防重放：同一 operation_id 只能确认一次）；
  每次访问惰性清理过期项
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class PendingOperation:
    """一条待确认的删除操作（确认后执行的参数 + 目标摘要）。"""

    operation_id: str
    tool: str  # 确认后实际执行的工具名（delete_knowledge / delete_user_memory）
    params: dict  # 执行参数（JSON 可序列化，确认时原样传给执行器）
    summary: str  # 目标摘要（模型展示 + 审计日志用，如「知识条目 #3《灵山简介》」）
    created_at: float = field(default_factory=time.time)


# ── 进程内待确认项存储（短 TTL，无需落盘） ───────────────────────────────

_pending: Dict[str, PendingOperation] = {}
_lock = threading.Lock()


def _ttl_seconds() -> int:
    """待确认项有效时长（秒）：配置读取（测试可 monkeypatch settings）。"""
    try:
        return int(settings.ADMIN_AGENT_CONFIRM_TTL_SECONDS)
    except Exception:
        return 120


def create_pending(tool: str, params: dict, summary: str) -> PendingOperation:
    """登记一条待确认操作，返回带 operation_id 的 PendingOperation。

    Args:
        tool: 确认后执行的操作名（与 _CONFIRM_EXECUTORS 键一致）
        params: 执行参数（JSON 可序列化，如 {"kb_id": 3}）
        summary: 目标摘要（含目标标识/标题，供模型与审计展示）
    """
    op = PendingOperation(
        operation_id=uuid.uuid4().hex,
        tool=tool,
        params=dict(params),
        summary=summary,
    )
    with _lock:
        _expire_locked()
        _pending[op.operation_id] = op
    logger.info(
        "管理员Agent待确认操作已登记: operation_id=%s tool=%s summary=%r ttl=%ds",
        op.operation_id,
        tool,
        summary,
        _ttl_seconds(),
    )
    return op


def get_pending(operation_id: str) -> Optional[PendingOperation]:
    """按 id 查询待确认项（不删除）；不存在/已过期返回 None。"""
    if not operation_id:
        return None
    with _lock:
        _expire_locked()
        op = _pending.get(operation_id)
        if op is None:
            return None
        return op


def consume(operation_id: str) -> Optional[PendingOperation]:
    """取出并删除待确认项（确认执行唯一入口，防重放）。

    已过期/不存在/已被确认均返回 None（调用方返回「操作不存在或已过期」）。
    """
    if not operation_id:
        return None
    with _lock:
        _expire_locked()
        return _pending.pop(operation_id, None)


def _expire_locked() -> None:
    """惰性清理过期待确认项（须持锁调用）。"""
    ttl = _ttl_seconds()
    now = time.time()
    expired = [
        op_id
        for op_id, op in _pending.items()
        if now - op.created_at > ttl
    ]
    for op_id in expired:
        tool = _pending[op_id].tool
        del _pending[op_id]
        logger.info(
            "管理员Agent待确认操作已过期失效: operation_id=%s tool=%s",
            op_id,
            tool,
        )
