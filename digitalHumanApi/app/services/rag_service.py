"""
RAG 服务层：文档加载、分块、向量化、存储、检索。

支持从 doc/景区知识库/ 目录加载景区资料（.md、.txt 文件，
索引范围限定景区知识文档，不混入 doc/ 下架构/设计文档），
并支持管理后台知识库条目（knowledge_base 表）的同步索引：
CRUD/导入变更后重建 knowledge 命名空间向量索引。
向量化后端由 app/core/embedding.py 工厂选择（Minimax Embedding API，
不可用时自动降级本地 TF-IDF），文件持久化存储，
无需 chromadb、sentence-transformers 等外部向量库服务。
"""

import logging
import os
import re
import threading
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document

from app.core.rag import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    CHUNKER_VERSION,
    DEFAULT_DOC_DIR,
    DEFAULT_TOP_K,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_KNOWLEDGE,
    get_vectorstore_for_namespace,
    normalize_namespaces,
)

logger = logging.getLogger(__name__)

# 检索上下文最大长度
MAX_CONTEXT_LEN: int = int(os.environ.get("RAG_MAX_CONTEXT_LEN", "2000"))

# ---------------------------------------------------------------------------
# 检索前查询规范化（Round 1 优化，2026-08-14）
# ---------------------------------------------------------------------------

# 口语化时间/营业意图词 → 同义检索词扩展表。
# 背景：TF-IDF 降级路径（embedding API 不可用）为词袋模型，查询「晚上营业到几点」
# 与文档中的「开放时间 9:00-21:30」词面无交集，演艺/开放信息块排名靠后（评测
# 用例 show_004 实测排到第 9 名）。扩展策略=保留原词 + 追加映射词（词袋检索对
# 词序不敏感，追加不改变原语义；远端 embedding 语义检索下追加词亦无副作用）。
_QUERY_TIME_EXPANSION: dict = {
    "几点": "时间",
    "营业": "开放",
    "开门": "开放",
    "关门": "闭园时间",
    "开到": "开放",
}

# 门票价格意图 → 检索词扩展表。
# 背景：文档对免票景区（拈花湾）以「免费开放/无门票限制」表述，词袋检索下
# 「门票价格」类问法与付费票价表（210 元）词面重叠、与免费表述词面无交集
# （评测 confuse_005 实测 210 元门票表排进 top3、免费开放块在 top3 外）。
# 触发词取窄口径「门票价格」（不含「多少钱/票价」等付费票询问，避免拉偏
# 付费票价检索）；仅追加不替换，目标词已有则跳过。
_QUERY_TICKET_EXPANSION: dict = {
    "门票价格": "免费开放",
}


def _normalize_search_query(query: str) -> str:
    """检索前查询规范化：追加时间/营业/门票意图同义词，提升召回。

    仅追加、不替换、不删除原词；目标词已存在时不再重复追加（防膨胀）；
    查询不含触发词时原样返回（零开销零副作用）。
    """
    query = (query or "").strip()
    if not query:
        return query
    extras = [
        target
        for trigger, target in _QUERY_TIME_EXPANSION.items()
        if trigger in query and target not in query
    ] + [
        target
        for trigger, target in _QUERY_TICKET_EXPANSION.items()
        if trigger in query and target not in query
    ]
    if not extras:
        return query
    return f"{query} {' '.join(extras)}"

# 向量库共享内存态（_documents/_vectors/_vector_matrix/embedding）互斥锁：
# 重建（clear+add_documents）与检索共用同一锁边界，避免并发交错导致
# 索引与 DB 不一致或检索期间向量维度不匹配。
_vector_index_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 文档加载
# ---------------------------------------------------------------------------

def _load_documents(doc_dir: Optional[Path] = None) -> List[Document]:
    """从指定目录递归加载所有 Markdown 和文本文件。

    Args:
        doc_dir: 文档目录路径，默认 doc/景区知识库/

    Returns:
        LangChain Document 列表
    """
    doc_dir = doc_dir or DEFAULT_DOC_DIR

    if not doc_dir.exists():
        logger.warning("Document directory does not exist: %s", doc_dir)
        return []

    documents: List[Document] = []
    extensions = ("*.md", "*.txt")

    for ext in extensions:
        for file_path in doc_dir.rglob(ext):
            try:
                content = file_path.read_text(encoding="utf-8")
                if not content.strip():
                    continue
                documents.append(Document(
                    page_content=content,
                    metadata={
                        "source": str(file_path),
                        "filename": file_path.name,
                    },
                ))
            except Exception as e:
                logger.warning("Failed to read %s: %s", file_path, e)

    logger.info("Loaded %d documents from %s", len(documents), doc_dir)
    return documents


# ---------------------------------------------------------------------------
# 文本分块
# ---------------------------------------------------------------------------

# Markdown 标题行（1-6 级）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# 过短正文过滤（标题已并入 section 前缀，不再单独成块；纯标题行不再产生碎片 chunk）
_MIN_BODY_LEN = 10


def _split_documents(documents: List[Document]) -> List[Document]:
    """递归字符分割器：按 Markdown 标题结构（section）→ 行 → 句子的优先级切分文本。

    相比旧实现（按段落/句子裸切），本实现（Round 1 优化，2026-08-14）：
    1. **标题感知**：以 #/##/### 标题为边界划分 section，chunk 携带完整标题链
       前缀（如「子表1：灵山胜境 景点数据集 > LS-011 灵山大佛」），消除旧策略
       下孤立标题行/字段行碎片（基线 3769 chunks 中 56% 为 <50 字符碎片，
       直接导致「灵山大佛有多高」只命中标题行、答案不在 top_k）。
    2. **字段保整**：超长 section 按行聚合切分（结构化数据集每行是一个完整字段
       「- 建筑/景观参数：...」），不截断字段；单行超长时再按句子二次切分，
       子块均带标题前缀。
    3. **无标题文档回退**：无 Markdown 标题时走旧的段落/句子切分，行为不变。
    4. metadata 新增 title（完整标题链），供检索来源展示/调试。
    """
    chunks: List[Document] = []

    for doc in documents:
        text = doc.page_content
        metadata = dict(doc.metadata)
        # 分块器版本标记（C7 2026-08-14）：documents 命名空间加载时校验，
        # 分块器升级后旧持久化 chunk 自动失效（见 rag.py _load 版本校验）
        metadata["chunker_version"] = CHUNKER_VERSION

        sections = _parse_markdown_sections(text)
        if sections:
            for title_chain, body in sections:
                md = dict(metadata)
                if title_chain:
                    md["title"] = title_chain
                chunks.extend(_chunk_section(title_chain, body, md))
        else:
            # 无标题文档：回退旧段落/句子切分（兼容纯文本知识文档）
            chunks.extend(_chunk_plain(text, metadata))

    logger.info(
        "Split %d documents into %d chunks (chunk_size=%d, overlap=%d)",
        len(documents),
        len(chunks),
        CHUNK_SIZE,
        CHUNK_OVERLAP,
    )
    return chunks


def _parse_markdown_sections(text: str) -> List[tuple]:
    """把 Markdown 文本解析为 [(标题链, 正文), ...]。

    - 以 #~###### 标题行为边界划分 section；标题链为祖先标题 + 当前标题
      （去掉 # 标记，" > " 连接），如「子表1：灵山胜境 景点数据集 > LS-011 灵山大佛」；
    - 标题前的无标题正文归入第一个 section（标题链为空）；
    - 正文为空的 section（如「字段说明」类标题）丢弃；
    - 文档没有任何标题行时返回 []（调用方回退旧切分）。
    """
    lines = text.splitlines()
    sections: List[tuple] = []
    stack: List[tuple] = []          # 当前标题路径 [(level, title), ...]（含当前标题）
    current_body: List[str] = []
    has_any_heading = False

    def flush():
        nonlocal current_body
        body = "\n".join(current_body).strip()
        if stack and body:
            # 标题链 = 标题栈全链（祖先 + 当前标题，去 # 标记）
            chain = " > ".join(t for _, t in stack)
            sections.append((chain, body))
        elif not stack and body:
            # 标题前的无标题正文：归入空标题链 section
            sections.append(("", body))
        current_body = []

    for raw_line in lines:
        stripped = raw_line.strip()
        m = _HEADING_RE.match(stripped) if stripped else None
        if m:
            has_any_heading = True
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            current_body.append(raw_line)
    flush()

    if not has_any_heading:
        return []
    return sections


def _chunk_section(title_chain: str, body: str, metadata: dict) -> List[Document]:
    """section 分块：完整 section 优先；超长时按行聚合切分（字段保整），
    单行超长再按句子二次切分；所有子块均带标题链前缀（自包含上下文）。"""
    prefix = f"{title_chain}\n" if title_chain else ""
    body = body.strip()
    if not body:
        return []

    # 完整 section 直接成块（≤ CHUNK_SIZE 时）
    if len(body) <= CHUNK_SIZE:
        return [Document(page_content=prefix + body, metadata=dict(metadata))]

    # 超长：按行聚合，预算扣除前缀长度（下限 120，防前缀挤占正文）
    budget = max(120, CHUNK_SIZE - len(prefix))
    lines = [ln for ln in body.split("\n") if ln.strip()]
    chunks: List[Document] = []
    current = ""

    def flush_current():
        nonlocal current
        if current.strip():
            chunks.append(
                Document(page_content=prefix + current.strip(), metadata=dict(metadata))
            )
        current = ""

    for line in lines:
        line = line.strip()
        if len(line) > budget:
            # 单行超长（如「详细介绍」长字段）：先收尾当前块，再按句子切分该行
            flush_current()
            chunks.extend(_chunk_long_line(prefix, line, budget, metadata))
            continue
        if len(current) + len(line) + 1 <= budget:
            current += line + "\n"
        else:
            flush_current()
            current = line + "\n"
    flush_current()
    return chunks


def _chunk_long_line(
    prefix: str, line: str, budget: int, metadata: dict
) -> List[Document]:
    """单行超长时按句子切分（带前缀的子块）。"""
    sentences = _split_by_separators(
        line, ["。", "！", "？", "；", ".", "!", "?", ";"]
    )
    chunks: List[Document] = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) <= budget:
            current += sent
        else:
            if len(current.strip()) >= _MIN_BODY_LEN:
                chunks.append(
                    Document(
                        page_content=prefix + current.strip(),
                        metadata=dict(metadata),
                    )
                )
            prev = current.strip()
            if CHUNK_OVERLAP > 0 and prev:
                start = max(0, len(prev) - CHUNK_OVERLAP)
                current = prev[start:] + sent
            else:
                current = sent
    if len(current.strip()) >= _MIN_BODY_LEN:
        chunks.append(
            Document(page_content=prefix + current.strip(), metadata=dict(metadata))
        )
    return chunks


def _chunk_plain(text: str, metadata: dict) -> List[Document]:
    """无标题文档回退路径：旧的段落 → 句子切分（行为与优化前一致）。"""
    chunks: List[Document] = []
    paragraphs = _split_by_separators(text, ["\n\n", "\n"])
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= CHUNK_SIZE:
            if len(para) >= _MIN_BODY_LEN:
                chunks.append(Document(page_content=para, metadata=dict(metadata)))
        else:
            sentences = _split_by_separators(
                para, ["。", "！", "？", "；", ".", "!", "?", ";"]
            )
            current = ""
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                if len(current) + len(sent) <= CHUNK_SIZE:
                    current += sent
                else:
                    if len(current.strip()) >= _MIN_BODY_LEN:
                        chunks.append(
                            Document(
                                page_content=current.strip(),
                                metadata=dict(metadata),
                            )
                        )
                    prev = current.strip()
                    if CHUNK_OVERLAP > 0 and len(prev) > 0:
                        start = max(0, len(prev) - CHUNK_OVERLAP)
                        current = prev[start:] + sent
                    else:
                        current = sent
            if len(current.strip()) >= _MIN_BODY_LEN:
                chunks.append(
                    Document(page_content=current.strip(), metadata=dict(metadata))
                )
    return chunks


def _split_by_separators(text: str, separators: List[str]) -> List[str]:
    """按分隔符列表递归切分文本。"""
    if not separators:
        return [text]

    sep = separators[0]
    if sep in text:
        parts = []
        segments = text.split(sep)
        for i, segment in enumerate(segments):
            # 保留分隔符（附加到前一段）
            if i < len(segments) - 1:
                segment = segment + sep
            if segment.strip():
                parts.append(segment)
        if len(parts) <= 1:
            return _split_by_separators(text, separators[1:])
        result = []
        for part in parts:
            result.extend(_split_by_separators(part, separators[1:]))
        return result

    return _split_by_separators(text, separators[1:])


# ---------------------------------------------------------------------------
# 索引 & 检索
# ---------------------------------------------------------------------------

def _invalidate_knowledge_tool_cache() -> None:
    """知识库索引重建后失效装配层工具缓存（A4 调优联动）。

    search_knowledge 工具缓存（app/services/deepagents/tool_cache.py）以
    knowledge+documents 为检索面——任一命名空间全量重建（文档索引 / 知识库
    CRUD 同步 / 手动索引）都会改变检索结果，立即失效缓存（版本号 +1，不等
    短 TTL）。导入放模块函数内避免循环依赖（tool_cache 为叶子模块）。
    失败仅告警（缓存靠短 TTL 自愈），不阻断索引主流程。
    """
    try:
        from app.services.deepagents.tool_cache import get_cache

        get_cache().invalidate_knowledge()
    except Exception as e:
        logger.warning("知识库工具缓存失效失败（短 TTL 兜底）: %s", e)


def _index_documents_sync(
    doc_dir: Optional[Path] = None,
    namespace: Optional[str] = None,
) -> dict:
    """同步执行全量文档索引。

    流程: 加载 → 分块 → 向量化 → 存储

    Args:
        doc_dir: 文档目录，默认 doc/景区知识库/
        namespace: 目标命名空间（默认 documents；索引 doc/ 景区知识文档）

    Returns:
        {"status", "documents", "chunks", "namespace", "embedding_backend",
         "degraded", "degraded_reason"}
    """
    doc_dir = doc_dir or DEFAULT_DOC_DIR
    if namespace is None or not namespace.strip():
        # 无 body 的旧调用方式默认索引 documents 命名空间
        ns = NAMESPACE_DOCUMENTS
    else:
        namespaces = normalize_namespaces(namespace)
        if len(namespaces) != 1:
            # 索引接口只接受单一命名空间，避免同一批 doc/景区知识库/ 文档写入多个命名空间
            raise ValueError(f"索引接口的 namespace 参数需为单一命名空间，收到: {namespaces}")
        ns = namespaces[0]

    # 加载
    documents = _load_documents(doc_dir)
    if not documents:
        return {"status": "empty", "documents": 0, "chunks": 0, "namespace": ns}

    # 分块
    chunks = _split_documents(documents)

    # 构建向量库
    vectorstore = get_vectorstore_for_namespace(ns)
    with _vector_index_lock:
        vectorstore.clear()  # 全量重建
        vectorstore.add_documents(chunks)

    # A4 工具缓存失效：knowledge+documents 是 search_knowledge 的检索面，
    # 任一命名空间全量重建后，知识检索缓存立即失效（不等 TTL）
    _invalidate_knowledge_tool_cache()

    return {
        "status": "ok",
        "documents": len(documents),
        "chunks": len(chunks),
        "namespace": ns,
        "embedding_backend": vectorstore.embedding_backend,
        "degraded": vectorstore.degraded,
        "degraded_reason": vectorstore.degraded_reason,
    }


async def index_documents(
    doc_dir: Optional[Path] = None,
    namespace: Optional[str] = None,
) -> dict:
    """执行全量文档索引。

    流程: 加载 → 分块 → 向量化 → 存储

    Args:
        doc_dir: 文档目录，默认 doc/景区知识库/
        namespace: 目标命名空间（默认 documents）

    Returns:
        {"status": str, "documents": int, "chunks": int, "namespace": str}
    """
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, _index_documents_sync, doc_dir, namespace
    )


# ---------------------------------------------------------------------------
# 知识库条目索引（管理后台 CRUD 变更同步）
# ---------------------------------------------------------------------------

def _build_knowledge_documents(entries: List[dict]) -> List[Document]:
    """将知识库条目（DB 行）转为待向量化的 Document 列表。

    条目以「标题 + 正文」整体作为检索单元（条目粒度短于文档分块粒度，
    不再做段落切分），metadata 携带条目归属信息供检索来源展示。

    Args:
        entries: [{"id", "title", "category", "content"}, ...]

    Returns:
        LangChain Document 列表（跳过空内容条目）
    """
    documents: List[Document] = []
    for entry in entries:
        title = (entry.get("title") or "").strip()
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        page_content = f"{title}\n{content}" if title else content
        documents.append(Document(
            page_content=page_content,
            metadata={
                "kb_id": entry.get("id"),
                "title": title,
                "category": entry.get("category", ""),
                "filename": f"知识库条目#{entry.get('id')}",
            },
        ))
    return documents


def _index_knowledge_entries_sync(
    entries: List[dict],
    namespace: Optional[str] = None,
) -> dict:
    """同步执行知识库条目全量重建（knowledge 命名空间）。

    管理后台增/删/改/导入后调用：以当前 DB 全量条目重建向量索引，
    保证索引与数据库一致（条目量小，全量重建开销可忽略且无残留）。

    Args:
        entries: 知识库条目字典列表（含 id/title/category/content）
        namespace: 目标命名空间，默认 knowledge

    Returns:
        {"status", "documents", "namespace", "embedding_backend",
         "degraded", "degraded_reason"}
    """
    if namespace is None or not namespace.strip():
        ns = NAMESPACE_KNOWLEDGE
    else:
        namespaces = normalize_namespaces(namespace)
        if len(namespaces) != 1:
            raise ValueError(f"索引接口的 namespace 参数需为单一命名空间，收到: {namespaces}")
        ns = namespaces[0]

    documents = _build_knowledge_documents(entries)
    vectorstore = get_vectorstore_for_namespace(ns)
    with _vector_index_lock:
        if not documents:
            # 库已清空：清空向量存储，避免残留旧条目可被检索
            vectorstore.clear()
            # A4 工具缓存失效（库清空同样影响检索结果）
            _invalidate_knowledge_tool_cache()
            return {"status": "empty", "documents": 0, "namespace": ns}

        vectorstore.clear()  # 全量重建
        vectorstore.add_documents(documents)

    # A4 工具缓存失效：知识库 CRUD 同步索引后，search_knowledge 缓存立即失效
    _invalidate_knowledge_tool_cache()

    return {
        "status": "ok",
        "documents": len(documents),
        "namespace": ns,
        "embedding_backend": vectorstore.embedding_backend,
        "degraded": vectorstore.degraded,
        "degraded_reason": vectorstore.degraded_reason,
    }


async def index_knowledge_entries(
    entries: List[dict],
    namespace: Optional[str] = None,
) -> dict:
    """执行知识库条目全量重建（默认 knowledge 命名空间）。

    由 knowledge_service 在 CRUD/导入变更后调用，同步更新向量索引。

    Args:
        entries: 知识库条目字典列表（含 id/title/category/content）
        namespace: 目标命名空间，默认 knowledge

    Returns:
        {"status": str, "documents": int, "namespace": str}
    """
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, _index_knowledge_entries_sync, entries, namespace
    )


async def search_documents(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    namespace: Optional[str] = None,
) -> List[dict]:
    """检索与查询最相关的文档片段（可在多个命名空间内联合检索）。

    这是对话 RAG、后台检索、推荐 Agent 共用的统一检索入口：
    - 对话 RAG（dialogue.py）/ ADH rag-context：默认 knowledge+documents 公共命名空间
    - 后台检索（/api/rag/search）：可通过 namespace 参数限定
    - 推荐 Agent（recommend_agent.py 的 search_knowledge 工具）：同步包装本函数

    Args:
        query: 查询文本
        top_k: 返回结果数量
        namespace: 命名空间（逗号分隔多个），默认 knowledge+documents

    Returns:
        [{"content": str, "source": str, "score": float, "namespace": str}, ...]
    """
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, _search_documents_sync, query, top_k, namespace
    )


def _search_documents_sync(
    query: str,
    top_k: int = DEFAULT_TOP_K,
    namespace: Optional[str] = None,
) -> List[dict]:
    """search_documents 的同步实现（供 asyncio executor / 推荐 Agent 同步工具复用）。"""
    try:
        namespaces = normalize_namespaces(namespace)
        all_results: List[dict] = []
        # 查询规范化（时间/营业意图词扩展）与评测/其他消费方共用同一逻辑
        query = _normalize_search_query(query)

        for ns in namespaces:
            vectorstore = get_vectorstore_for_namespace(ns)
            with _vector_index_lock:
                results = vectorstore.similarity_search_with_score(query, k=top_k)
            for doc, score in results:
                all_results.append({
                    "content": doc.page_content,
                    "source": doc.metadata.get("filename", doc.metadata.get("source", "")),
                    "score": float(score),
                    "namespace": ns,
                })

        # 跨命名空间合并：按分数降序取 top_k（分数并列时保持稳定顺序）
        all_results.sort(key=lambda r: -r["score"])
        return all_results[:top_k]
    except Exception as e:
        logger.warning("RAG search failed: %s", e)
        return []


def get_rag_meta(namespaces: Optional[List[str]] = None) -> dict:
    """当前 RAG 检索链路的可观测元信息（embedding 后端与降级状态）。

    按给定命名空间（默认公共命名空间 knowledge+documents）汇总
    embedding 后端与降级标记，供检索/索引接口透出
    （降级时响应中带可观测标记）。
    """
    if namespaces is None:
        namespaces = normalize_namespaces(None)
    backends = set()
    degraded = False
    degraded_reason = ""
    for ns in namespaces:
        try:
            vectorstore = get_vectorstore_for_namespace(ns)
            backends.add(vectorstore.embedding_backend)
            if vectorstore.degraded:
                degraded = True
                degraded_reason = vectorstore.degraded_reason
        except Exception as e:
            logger.warning("get_rag_meta failed for namespace %s: %s", ns, e)
    return {
        "embedding_backend": ",".join(sorted(backends)) or "unknown",
        "degraded": degraded,
        "degraded_reason": degraded_reason,
    }


def build_rag_context(search_results: List[dict]) -> str:
    """将检索结果拼接为上下文文本，用于注入对话 prompt。

    Args:
        search_results: search_documents 返回的结果列表

    Returns:
        格式化的上下文字符串
    """
    if not search_results:
        return ""

    parts = ["【本地知识库检索结果】"]
    for i, result in enumerate(search_results, 1):
        source = result.get("source", "未知来源")
        content = result.get("content", "")
        parts.append(f"[{i}] ({source}) {content}")

    context = "\n".join(parts)

    # 限制上下文总长度
    if len(context) > MAX_CONTEXT_LEN:
        context = context[:MAX_CONTEXT_LEN] + "\n...(内容过长，已截断)"

    return context
