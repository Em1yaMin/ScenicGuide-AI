"""长期记忆服务（阶段2 T2）：对话完成后提取用户事实/偏好并写入 user_memory 向量库。

设计（船长决策 2026-08-05 确认 + 本任务细化）：

- **记忆只存用户事实/偏好/习惯**（自然语言记忆句），结构化数据（景点/路线）
  不走向量（工具直查，零幻觉原则）
- **存储**：`user_memory` 命名空间（app/core/rag.py 预留，Minimax embedding +
  轻量文件持久化），**每条记忆一个 Document**，`page_content` 为记忆句，
  metadata 携带 user/fact_type/source_conversation_id/timestamp，按 user 隔离
- **游客隔离**：visitor / guest-* 不写长期记忆（对话入口对游客/匿名一律跳过）
- **提取时机**：对话完成后后台异步执行（不阻塞响应），LLM 单次调用（复用
  app/core/llm.py 工厂，走 LLM_API_* 配置）；任何异常静默降级为不写记忆
- **去重/覆盖**：同 user 同「规范化 content」仅保留最新一条（更新 timestamp
  与来源会话），避免记忆无限膨胀；另设每用户记忆条数上限（MEMORY_MAX_PER_USER，
  默认 200，超限淘汰 timestamp 最旧的条目）

记忆条目 schema（**T3 契约**，sg-mem-long-recall 对齐；变更须同步
doc/long-term-memory.md 与 AGENTS.md）：

    user:                   str    用户标识（登录用户 id；游客不落库）
    fact_type:              str    preference（偏好）/ fact（事实）/ habit（习惯）
    content:                str    自然语言记忆句（如「喜欢拍照」「带老人小孩」）
    source_conversation_id: str    来源会话 ID（溯源用）
    timestamp:              float  提取时间（Unix 秒）
"""

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from app.core.config import settings
from app.core.llm import get_chat_llm
from app.core.rag import NAMESPACE_USER_MEMORY, get_vectorstore_for_namespace
from app.services.rag_service import _vector_index_lock

logger = logging.getLogger(__name__)

# ── 记忆条目 schema（T3 契约） ────────────────────────────────────────────

# 事实类型：preference（偏好）/ fact（事实）/ habit（习惯）
FACT_TYPES: Tuple[str, ...] = ("preference", "fact", "habit")
FACT_TYPE_LABELS: Dict[str, str] = {
    "preference": "偏好",
    "fact": "事实",
    "habit": "习惯",
}

# 游客/匿名标识前缀：visitor（默认无 token）与 guest-（游客 token 签发的 id）
_GUEST_PREFIXES: Tuple[str, ...] = ("visitor", "guest-")

# 单轮对话最多提取条数 / 单条内容长度上限（防 LLM 输出失控导致记忆膨胀）
_MEMORY_MAX_FACTS_PER_TURN = 10
_MEMORY_MAX_CONTENT_LEN = 200
# 提取 LLM 调用参数（单次低成本调用；后台执行，超时短一些避免任务残留）
_MEMORY_EXTRACT_MAX_TOKENS = 512
_MEMORY_EXTRACT_TIMEOUT = 30


@dataclass
class MemoryEntry:
    """单条用户长期记忆（T3 契约 schema）。

    序列化后按 user 写入 user_memory 命名空间（每条记忆一个 Document，
    metadata 携带结构化字段，page_content 为自然语言记忆句）。

    memory_id 为内部扩展字段（deepagents 记忆工具化按 id 删除用）：写入时
    生成 uuid 存 Document metadata，不进入 to_dict（T3 契约键集合不变）。
    """

    user: str = ""
    fact_type: str = "fact"
    content: str = ""
    source_conversation_id: str = ""
    timestamp: float = 0.0
    memory_id: str = ""  # 内部扩展：Document metadata 标识（旧条目为空）

    def __post_init__(self) -> None:
        if self.fact_type not in FACT_TYPES:
            self.fact_type = "fact"
        self.content = (self.content or "").strip()
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        # T3 契约键集合（user/fact_type/content/source_conversation_id/timestamp）
        # 保持不变；memory_id 为内部扩展，不序列化（见类注释）
        return {
            "user": self.user,
            "fact_type": self.fact_type,
            "content": self.content,
            "source_conversation_id": self.source_conversation_id,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "MemoryEntry":
        return MemoryEntry(
            user=str(data.get("user", "") or ""),
            fact_type=str(data.get("fact_type", "fact") or "fact"),
            content=str(data.get("content", "") or ""),
            source_conversation_id=str(data.get("source_conversation_id", "") or ""),
            timestamp=float(data.get("timestamp", 0.0) or 0.0),
            memory_id=str(data.get("memory_id", "") or ""),
        )


def is_guest_user(user: Optional[str]) -> bool:
    """游客/匿名判定：visitor 或 guest- 前缀（游客不写长期记忆）。"""
    if not user:
        return True
    user = user.strip()
    return user == _GUEST_PREFIXES[0] or user.startswith(_GUEST_PREFIXES[1])


def _normalize_content(content: str) -> str:
    """规范化记忆句用于去重：去空白/中英文标点、转小写。

    同 user 同规范化 content 视为同一事实（覆盖更新而非追加）。
    """
    return re.sub(r"[\s，。！？、；：,.!?;:]+", "", content or "").lower()


# ── 存储访问（user_memory 命名空间） ──────────────────────────────────────


def _memory_store():
    """user_memory 命名空间向量存储（进程内单例，见 app/core/rag.py）。"""
    return get_vectorstore_for_namespace(NAMESPACE_USER_MEMORY)


def _doc_to_entry(doc: Document) -> MemoryEntry:
    """向量库 Document → MemoryEntry（metadata 解析，字段缺失取默认值）。"""
    md = doc.metadata or {}
    return MemoryEntry(
        user=str(md.get("user", "") or ""),
        fact_type=str(md.get("fact_type", "fact") or "fact"),
        content=str(doc.page_content or ""),
        source_conversation_id=str(md.get("source_conversation_id", "") or ""),
        timestamp=float(md.get("timestamp", 0.0) or 0.0),
        memory_id=str(md.get("memory_id", "") or ""),
    )


def _all_entries() -> List[MemoryEntry]:
    """读取 user_memory 命名空间全量记忆（按 metadata 解析，须在持锁时调用）。"""
    store = _memory_store()
    entries: List[MemoryEntry] = []
    for doc in store._documents:
        entry = _doc_to_entry(doc)
        if entry.user:
            entries.append(entry)
    return entries


# legacy 派生 id 前缀：旧条目（升级前写入、无 memory_id）按「归一化 content」
# 派生稳定 id 供按 id 删除（与 deepagents/memory_tools.py 的 _LEGACY_ID_PREFIX 对齐）
_LEGACY_ID_PREFIX = "legacy:"


def memory_id_of(entry: MemoryEntry) -> str:
    """条目对外 memory_id：新条目为 uuid；旧条目（无 memory_id）派生稳定 id。

    派生规则（与 deepagents 记忆工具 _entry_to_json 对齐）：
    `legacy:{归一化 content}`——同 user 同内容去重覆盖保证派生 id 唯一稳定，
    可按 id 删除。管理端（B5）列表/查找/删除统一走本函数，避免派生规则漂移。
    """
    if entry.memory_id:
        return entry.memory_id
    return f"{_LEGACY_ID_PREFIX}{_normalize_content(entry.content)}"


def _doc_matches_memory_id(doc: Document, memory_id: str) -> bool:
    """Document 是否匹配指定 memory_id（含 legacy 派生 id 兼容）。"""
    return memory_id_of(_doc_to_entry(doc)) == memory_id


def _entry_to_document(entry: MemoryEntry) -> Document:
    """记忆条目 → 向量库 Document（metadata 携带结构化字段 + memory_id）。

    memory_id 为内部扩展（deepagents 记忆工具化按 id 删除用）：entry 未携带
    时生成 uuid 并回填（写入路径只调一次，id 稳定落库）。
    """
    if not entry.memory_id:
        entry.memory_id = uuid.uuid4().hex
    return Document(
        page_content=entry.content,
        metadata={
            "user": entry.user,
            "fact_type": entry.fact_type,
            "source_conversation_id": entry.source_conversation_id,
            "timestamp": entry.timestamp,
            "memory_id": entry.memory_id,
        },
    )


# ── 写入（含去重覆盖与条数上限） ──────────────────────────────────────────


async def save_user_memory(user: str, facts: List[MemoryEntry]) -> dict:
    """写入/覆盖用户长期记忆（按 user 隔离，同事实覆盖更新）。

    **T3 契约接口**：sg-mem-long-recall 通过本函数写入记忆条目；
    检索侧用 search_user_memory。游客（visitor / guest-*）直接跳过不写。

    Args:
        user: 用户标识（登录用户 id）
        facts: 待写入的记忆条目列表；同 user 同规范化 content 的旧条目
            被覆盖（更新 timestamp 与来源会话），不会无限追加

    Returns:
        {"written": int, "overwritten": int, "total": int, "user": str,
         "skipped": bool}；skipped=True 表示游客/无有效事实未写入
    """
    return await asyncio.get_running_loop().run_in_executor(
        None, _save_user_memory_sync, user, facts
    )


def _save_user_memory_sync(user: str, facts: List[MemoryEntry]) -> dict:
    """save_user_memory 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    if is_guest_user(user) or not facts:
        return {
            "written": 0,
            "overwritten": 0,
            "total": 0,
            "user": user,
            "skipped": True,
        }

    # 批内去重：同规范化 content 只保留首条（与跨批覆盖语义一致，防重复膨胀）
    incoming: List[MemoryEntry] = []
    seen_keys = set()
    for f in facts:
        if not f.content:
            continue
        key = _normalize_content(f.content)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        incoming.append(f)
    if not incoming:
        return {
            "written": 0,
            "overwritten": 0,
            "total": 0,
            "user": user,
            "skipped": True,
        }

    store = _memory_store()
    written = 0
    overwritten = 0
    with _vector_index_lock:
        # 覆盖：删除同 user 且规范化 content 与任一新事实相同的旧条目
        overwritten = store.delete_documents(
            lambda doc: (
                doc.metadata.get("user") == user
                and _normalize_content(doc.page_content) in seen_keys
            )
        )

        # 写入新条目（保留调用方时间戳；缺失时用当前时间）
        # 注意：内部新构造的 MemoryEntry 透传 f.memory_id（B5 管理端预生成
        # 稳定 id 用）；为空时由 _entry_to_document 生成 uuid（既有行为不变）
        now = time.time()
        docs = [
            _entry_to_document(
                MemoryEntry(
                    user=user,
                    fact_type=f.fact_type,
                    content=f.content,
                    source_conversation_id=f.source_conversation_id,
                    timestamp=f.timestamp or now,
                    memory_id=f.memory_id,
                )
            )
            for f in incoming
        ]
        if docs:
            store.add_documents(docs)
            written = len(docs)

        # 条数上限：超限淘汰 timestamp 最早的条目（防记忆膨胀）
        _enforce_cap_locked(user, store)

    total = len([e for e in _all_entries() if e.user == user])
    logger.info(
        "长期记忆写入: user=%s written=%d overwritten=%d total=%d",
        user,
        written,
        overwritten,
        total,
    )
    return {
        "written": written,
        "overwritten": overwritten,
        "total": total,
        "user": user,
        "skipped": False,
    }


def _enforce_cap_locked(user: str, store) -> None:
    """每用户记忆条数上限：超限时淘汰 timestamp 最早的条目（须持锁调用）。"""
    limit = settings.MEMORY_MAX_PER_USER
    if limit <= 0:
        return
    user_entries = [e for e in _all_entries() if e.user == user]
    overflow = len(user_entries) - limit
    if overflow <= 0:
        return
    # 按 timestamp 升序取应淘汰的条目（同 timestamp 时保持稳定顺序）
    to_evict = sorted(user_entries, key=lambda e: (e.timestamp,))[:overflow]
    evict_keys = {_normalize_content(e.content) for e in to_evict}
    removed = store.delete_documents(
        lambda doc: (
            doc.metadata.get("user") == user
            and _normalize_content(doc.page_content) in evict_keys
        )
    )
    logger.warning(
        "记忆条数超限，淘汰最旧 %d 条（user=%s, limit=%d）",
        removed,
        user,
        limit,
    )


# ── 检索（阶段2 T3：对话前注入使用） ──────────────────────────────────────


async def search_user_memory(
    user: str,
    query: str,
    top_k: int = 3,
) -> List[MemoryEntry]:
    """检索指定用户的长期记忆（按 user 隔离，语义相似度降序）。

    **T3 契约接口**：sg-mem-long-recall 在对话前注入用户记忆时调用
    （查询文本建议用用户当前问题或固定画像问题，如「用户的偏好和情况」）。
    游客返回空列表（游客无长期记忆）。

    Args:
        user: 用户标识
        query: 检索文本
        top_k: 返回条数（默认 3）

    Returns:
        按相似度降序的记忆条目列表（检索异常降级返回空列表，不阻断调用方）
    """
    if is_guest_user(user) or not query:
        return []
    return await asyncio.get_running_loop().run_in_executor(
        None, _search_user_memory_sync, user, query, top_k
    )


def _search_user_memory_sync(
    user: str,
    query: str,
    top_k: int = 3,
) -> List[MemoryEntry]:
    """search_user_memory 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    if is_guest_user(user) or not query:
        return []
    store = _memory_store()
    try:
        with _vector_index_lock:
            results = store.similarity_search_with_score_filtered(
                query,
                k=top_k,
                predicate=lambda d: d.metadata.get("user") == user,
            )
    except Exception as e:
        logger.warning("长期记忆检索失败（降级返回空）: %s", e)
        return []
    return [
        MemoryEntry(
            user=user,
            fact_type=str(doc.metadata.get("fact_type", "fact") or "fact"),
            content=str(doc.page_content or ""),
            source_conversation_id=str(doc.metadata.get("source_conversation_id", "") or ""),
            timestamp=float(doc.metadata.get("timestamp", 0.0) or 0.0),
        )
        for doc, _score in results
    ]


async def list_user_memories(user: str) -> List[MemoryEntry]:
    """列出指定用户的全部长期记忆条目（含 memory_id，供按 id 删除用）。

    **A2 新增公开接口**（deepagents 记忆工具化 list_user_memories 工具调用）：
    返回按写入顺序的全量条目；旧条目（升级前写入、无 memory_id）的
    memory_id 为空串，由调用方派生兼容 id（见 delete_user_memory）。
    游客返回空列表。
    """
    if is_guest_user(user):
        return []
    return await asyncio.get_running_loop().run_in_executor(
        None, _list_user_memories_sync, user
    )


def _list_user_memories_sync(user: str) -> List[MemoryEntry]:
    """list_user_memories 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    if is_guest_user(user):
        return []
    with _vector_index_lock:
        return [e for e in _all_entries() if e.user == user]


async def delete_user_memory(user: str, memory_id: str) -> dict:
    """删除指定用户的单条长期记忆（按 user + memory_id 匹配，user 隔离）。

    **A2 新增公开接口**（deepagents 记忆工具化 delete_user_memory 工具调用）：
    仅能删除传入 user 自己的记忆（谓词同时校验 user 与 memory_id，跨用户
    删除必然失败）；兼容旧条目派生 id（`legacy:{归一化 content}`，见
    list_user_memories 说明）。游客直接返回未删除。

    Returns:
        {"deleted": int, "user": str}：deleted 为实际删除条数（0 或 1）
    """
    return await asyncio.get_running_loop().run_in_executor(
        None, _delete_user_memory_sync, user, memory_id
    )


def _delete_user_memory_sync(user: str, memory_id: str) -> dict:
    """delete_user_memory 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    if is_guest_user(user) or not memory_id:
        return {"deleted": 0, "user": user}
    store = _memory_store()
    with _vector_index_lock:
        deleted = store.delete_documents(
            lambda doc: (
                doc.metadata.get("user") == user
                and _doc_matches_memory_id(doc, memory_id)
            )
        )
    if deleted:
        logger.info("长期记忆删除: user=%s memory_id=%s", user, memory_id)
    return {"deleted": deleted, "user": user}


async def count_user_memory(user: str) -> int:
    """统计指定用户的记忆条数（测试/管理/可观测用）。"""
    if is_guest_user(user):
        return 0
    return await asyncio.get_running_loop().run_in_executor(
        None, _count_user_memory_sync, user
    )


def _count_user_memory_sync(user: str) -> int:
    """count_user_memory 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    if is_guest_user(user):
        return 0
    with _vector_index_lock:
        return len([e for e in _all_entries() if e.user == user])


async def find_memory_by_id(memory_id: str) -> Optional[MemoryEntry]:
    """按 memory_id 跨用户查找记忆条目（B5 管理端：管理员例外跨用户操作）。

    匹配规则与 delete_user_memory 一致（uuid 精确匹配 + legacy 派生 id 兼容，
    见 memory_id_of）；未找到返回 None。空 id 直接返回 None。
    """
    if not memory_id:
        return None
    return await asyncio.get_running_loop().run_in_executor(
        None, _find_memory_by_id_sync, memory_id
    )


def _find_memory_by_id_sync(memory_id: str) -> Optional[MemoryEntry]:
    """find_memory_by_id 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    with _vector_index_lock:
        for doc in _memory_store()._documents:
            if _doc_matches_memory_id(doc, memory_id):
                return _doc_to_entry(doc)
    return None


async def count_memories_per_user() -> Dict[str, int]:
    """统计各用户的记忆条数（B5 管理端用户列表用，单次持锁批量统计）。

    Returns:
        {user: count}：user 为记忆库中的用户标识字符串（注册用户为 str(id)，
        如 "1"）；游客无记忆不会出现。
    """
    return await asyncio.get_running_loop().run_in_executor(
        None, _count_memories_per_user_sync
    )


def _count_memories_per_user_sync() -> Dict[str, int]:
    """count_memories_per_user 的同步实现（经 run_in_executor 在事件循环外执行）。"""
    counts: Dict[str, int] = {}
    with _vector_index_lock:
        for entry in _all_entries():
            counts[entry.user] = counts.get(entry.user, 0) + 1
    return counts


# ── LLM 提取（对话完成后台执行，失败静默降级） ───────────────────────────

_EXTRACT_PROMPT = """你是景区导览系统的用户画像提取器。从下面一段对话中，提取游客表达出的、可在未来对话中复用的用户信息（偏好/事实/习惯）。

提取规则：
1. 只提取明确表达的可复用信息（如「喜欢拍照」「带老人小孩」「偏好半日游」「去过灵山」），不要推断或编造
2. 不提取助手自己的回答内容、不提取景点百科知识、不提取一次性的问句
3. 没有可提取信息时返回空数组 []
4. 输出必须是合法的 JSON 数组，禁止输出任何其他文字、注释或 markdown 代码块

每项格式：{{"fact_type": "preference|fact|habit", "content": "简短的中文记忆句"}}
- preference：偏好（喜欢/偏好/更倾向…）
- fact：事实（带老人小孩、去过某地、同行人数、时间限制…）
- habit：习惯（经常/每次/平时都…）

【游客】
{query}

【助手】
{answer}
"""


def extract_facts(
    query: str,
    answer: str,
    llm: Optional[Any] = None,
) -> List[MemoryEntry]:
    """从一轮对话中提取用户事实/偏好（LLM 单次调用）。

    Args:
        query: 游客输入
        answer: 助手回答
        llm: 可注入的 ChatOpenAI 实例（测试用）；默认走 llm.py 工厂
            （get_chat_llm，LLM_API_* 配置，max_tokens 经 extra_body 注入）

    Returns:
        提取到的记忆条目列表（user 为空，由调用方填充）；
        任何异常/解析失败返回空列表（静默降级，不写记忆）
    """
    if not query or not answer:
        return []
    try:
        llm = llm or get_chat_llm(
            max_tokens=_MEMORY_EXTRACT_MAX_TOKENS,
            temperature=0.2,
            timeout=_MEMORY_EXTRACT_TIMEOUT,
        )
        prompt = _EXTRACT_PROMPT.format(
            query=query[:2000],
            answer=answer[:2000],
        )
        response = llm.invoke(prompt)
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            content = str(content or "")
        return _parse_facts(content)
    except Exception as e:
        # 提取失败静默降级：不影响对话响应，仅告警日志
        logger.warning("记忆提取失败（静默降级，不写记忆）: %s", e)
        return []


def _parse_facts(text: str) -> List[MemoryEntry]:
    """解析 LLM 输出的 JSON 数组为 MemoryEntry 列表（容错解析）。

    - 剥离可能的 markdown 代码块围栏
    - 只取第一个 [...] 数组片段
    - 单条无效时跳过，全部无效返回空列表（不抛错）
    """
    if not text:
        return []
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end <= start:
        logger.warning("记忆提取 LLM 输出无 JSON 数组，忽略: %r", text[:200])
        return []
    try:
        data = json.loads(cleaned[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        logger.warning("记忆提取 LLM 输出非 JSON，忽略: %r", text[:200])
        return []
    if not isinstance(data, list):
        return []

    facts: List[MemoryEntry] = []
    for item in data[:_MEMORY_MAX_FACTS_PER_TURN]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "") or "").strip()
        if not content:
            continue
        if len(content) > _MEMORY_MAX_CONTENT_LEN:
            content = content[:_MEMORY_MAX_CONTENT_LEN]
        fact_type = str(item.get("fact_type", "fact") or "fact").strip()
        facts.append(MemoryEntry(user="", fact_type=fact_type, content=content))
    return facts


async def extract_and_store(
    user: str,
    conversation_id: str,
    query: str,
    answer: str,
) -> dict:
    """对话完成后的记忆提取钩子（chat_agent 后台调度调用）。

    - 游客（visitor / guest-*）或 MEMORY_ENABLED=false → 直接跳过（不产生长期记忆）
    - LLM 提取/解析失败 → 静默降级（不写记忆、不抛错、不阻塞对话响应）
    - 提取到的事实经去重覆盖写入 user_memory（按 user 隔离）

    Returns:
        save_user_memory 的结果 dict；跳过/失败时为 {"written": 0, "skipped": True}
    """
    if is_guest_user(user) or not settings.MEMORY_ENABLED:
        return {"written": 0, "skipped": True}
    try:
        loop = asyncio.get_running_loop()
        facts = await loop.run_in_executor(None, extract_facts, query, answer)
    except Exception as e:
        logger.warning("长期记忆提取后台任务异常（静默降级）: %s", e)
        return {"written": 0, "skipped": True}
    if not facts:
        return {"written": 0, "skipped": True}
    for f in facts:
        f.user = user
        f.source_conversation_id = conversation_id
    result = await save_user_memory(user, facts)
    logger.info(
        "长期记忆提取并写入: user=%s facts=%d",
        user,
        len(facts),
    )
    return result
