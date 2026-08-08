"""工具结果归一化缓存（A4 成本/速度调优，批次3 收官）。

背景：A1 实测 deepseek-v4-flash 在 tool-calling 下收敛偏保守，同一查询
反复调用高频只读工具（search_spots / search_routes / search_knowledge 等）
是 input token 的主要放大器。A4 组合拳之一：对只读工具按「归一化查询键」
缓存结果——同 query 不重查，降低每轮工具调用开销与 token 消耗。

设计要点（船长决策 + 实测依据见 doc/deepagents-tuning.md）：

- **归一化键**：`(tool_name, 归一化参数...)`——文本参数去空白/标点/大小写
  （中英文标点统一剔除），kwargs 按键名排序保证键稳定；"灵山 景区" 与
  "灵山景区"、"带老人、小孩" 与 "带老人小孩" 命中同一缓存
- **TTL 分级**：景点/路线等静态 JSON 数据（`app/data/`）安全长缓存
  （默认 1h）；知识检索（search_knowledge，检索 knowledge+documents
  命名空间）内容可变，短 TTL（默认 5min）+ **索引重建即时失效**
  （epoch 版本号，见 `invalidate_knowledge()`，由 rag_service 在
  `_index_documents_sync` / `_index_knowledge_entries_sync` 全量重建时
  调用——知识库 CRUD 同步索引的既有机制天然挂钩，无需额外接线）
- **结果不可变**：工具返回 JSON 字符串（结构化数据/检索结果），缓存
  引用安全；不缓存记忆工具（有副作用）与操作类工具（不存在）
- **上限保护**：条目数上限（默认 512，LRU 淘汰最久未命中），防长跑
  进程内存无限增长；并发安全（threading.Lock，langgraph 在线程池执行
  同步工具）
- **统计**：命中/未命中计数器（`stats()`），供实测报告与观测
- **总开关**：`AGENT_TOOL_CACHE_ENABLED=false` 时 `wrap_tool` 原样返回
  工具对象（零行为变化，回退开关）

缓存对象为 langchain `StructuredTool` 的包装副本（同名/同描述/同
args_schema），仅替换执行函数；SSE step 事件（按工具名）、RAG 来源
提取（search_knowledge 的 ToolMessage 内容不变）均不受影响。
"""
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from langchain_core.tools import StructuredTool

from app.core.config import settings

logger = logging.getLogger(__name__)

# 缓存条目数上限（防内存无限增长；超出后 LRU 淘汰最久未命中条目）
_DEFAULT_CACHE_LIMIT = 512

# 归一化用标点/空白集合（中英文标点 + 空白符，统一剔除）
_PUNCT_RE = re.compile(r"[\s，。！？；：、,\.!?;:'\"`~～@#\$%^&\*()（）\[\]【】\{\}<>《》\|\\/_\-\—–]+")


def normalize_query(text: str) -> str:
    """查询文本归一化：去空白/标点/大小写。

    "灵山 景区！" → "灵山景区"；"带老人、小孩" → "带老人小孩"。
    归一化后不同写法命中同一缓存键（同 query 不重查）。
    """
    return _PUNCT_RE.sub("", (text or "").strip().lower())


def _normalize_arg(value: Any) -> str:
    """参数归一化（字符串走文本归一化，其余转字符串）。"""
    if isinstance(value, str):
        return normalize_query(value)
    return str(value)


class ToolResultCache:
    """线程安全的工具结果缓存（归一化键 → 结果 + TTL + 知识版本）。"""

    def __init__(self, limit: int = _DEFAULT_CACHE_LIMIT) -> None:
        self._limit = limit
        self._entries: "OrderedDict[Tuple[str, ...], Tuple[str, float, int]]" = (
            OrderedDict()
        )
        self._lock = threading.Lock()
        # 知识库索引版本号：rag_service 全量重建（knowledge/documents 命名
        # 空间）时 +1，search_knowledge 缓存条目记录写入时的版本，读取时
        # 版本不一致即失效（知识库内容变更后缓存立即失效，不等 TTL）
        self._knowledge_epoch = 0
        self._hits = 0
        self._misses = 0

    # ── 键构造 ──────────────────────────────────────────────────────────

    def make_key(
        self, tool_name: str, args: Tuple[Any, ...], kwargs: Dict[str, Any]
    ) -> Tuple[str, ...]:
        """构造归一化缓存键：(tool_name, 归一化位置参数..., 排序后的 kwargs)。

        kwargs 按键名排序（`top_k=3` 与 `3` 的位置参数形态不共享键——
        二者工具语义相同但为保持实现简单不做位置/关键字合并，实测模型
        调用形态稳定，不影响命中率）。
        """
        parts: list = [tool_name]
        parts.extend(_normalize_arg(a) for a in args)
        for k in sorted(kwargs):
            parts.append(f"{k}={_normalize_arg(kwargs[k])}")
        return tuple(parts)

    # ── 读写 ────────────────────────────────────────────────────────────

    def get(self, key: Tuple[str, ...], now: Optional[float] = None) -> Optional[str]:
        """读取缓存；未命中/过期/知识版本失效返回 None。

        命中时把条目移到队尾（LRU 语义）；惰性清除已过期条目。
        """
        now = time.time() if now is None else now
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            value, expires_at, epoch = entry
            if expires_at <= now:
                del self._entries[key]
                self._misses += 1
                return None
            if key[0] == "search_knowledge" and epoch != self._knowledge_epoch:
                # 知识索引已重建：缓存内容可能过期，立即失效
                del self._entries[key]
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return value

    def set(
        self,
        key: Tuple[str, ...],
        value: str,
        ttl_seconds: float,
        now: Optional[float] = None,
    ) -> None:
        """写入缓存（记录当前知识版本；超限 LRU 淘汰最久条目）。"""
        now = time.time() if now is None else now
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = (value, now + ttl_seconds, self._knowledge_epoch)
            while len(self._entries) > self._limit:
                self._entries.popitem(last=False)

    # ── 失效与统计 ───────────────────────────────────────────────────────

    def invalidate_knowledge(self) -> None:
        """知识库索引重建后的缓存失效：版本号 +1 并清除 knowledge 条目。

        由 rag_service 在 `_index_documents_sync` / `_index_knowledge_entries_sync`
        全量重建后调用（知识库 CRUD 同步索引的既有机制挂钩）。
        景点/路线等静态数据工具不受影响（版本检查仅作用于 search_knowledge）。
        """
        with self._lock:
            self._knowledge_epoch += 1
            stale = [k for k in self._entries if k[0] == "search_knowledge"]
            for k in stale:
                del self._entries[k]
            if stale:
                logger.info(
                    "知识库索引重建，已失效 search_knowledge 缓存: entries=%d epoch=%d",
                    len(stale),
                    self._knowledge_epoch,
                )

    def invalidate_all(self) -> None:
        """清空全部缓存（测试与观测用）。"""
        with self._lock:
            self._entries.clear()
            self._knowledge_epoch += 1

    def stats(self) -> Dict[str, Any]:
        """缓存统计（命中/未命中/条目数），供实测报告与观测。"""
        with self._lock:
            return {
                "hits": self._hits,
                "misses": self._misses,
                "entries": len(self._entries),
                "knowledge_epoch": self._knowledge_epoch,
            }


# 模块级单例（所有 agent 实例共享：rag/无 rag 两个缓存 agent 共用一份缓存，
# 且 agent 重建不丢缓存；实测统计也以此为观测点）
_CACHE = ToolResultCache()


def get_cache() -> ToolResultCache:
    """获取模块级缓存单例（测试注入/观测用）。"""
    return _CACHE


def wrap_tool(
    tool_obj: StructuredTool,
    *,
    ttl_seconds: float,
) -> Any:
    """包装只读工具为缓存版本（同名/同描述/同 args_schema，仅替换执行函数）。

    Args:
        tool_obj: 原始 StructuredTool（recommend_agent 的结构化直查工具）
        ttl_seconds: 缓存 TTL（秒）；静态数据长 TTL，知识检索短 TTL

    Returns:
        缓存包装后的新工具对象；`AGENT_TOOL_CACHE_ENABLED=false` 时原样
        返回原工具（零行为变化）。search_knowledge 的索引版本失效由缓存
        键首段工具名判定（见 `ToolResultCache.get`），无需额外参数。
    """
    if not settings.AGENT_TOOL_CACHE_ENABLED:
        return tool_obj
    cache = _CACHE
    original_func = tool_obj.func

    def _cached_run(*args: Any, **kwargs: Any) -> str:
        key = cache.make_key(tool_obj.name, args, kwargs)
        hit = cache.get(key)
        if hit is not None:
            return hit
        # 原函数为纯只读查询（本地 JSON/向量检索，同步执行），结果 JSON 串
        result = original_func(*args, **kwargs)
        cache.set(key, result, ttl_seconds)
        return result

    return StructuredTool.from_function(
        func=_cached_run,
        name=tool_obj.name,
        description=tool_obj.description,
        args_schema=tool_obj.args_schema,
    )
