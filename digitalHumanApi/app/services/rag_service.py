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
from pathlib import Path
from typing import List, Optional

from langchain_core.documents import Document

from app.core.rag import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
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

def _split_documents(documents: List[Document]) -> List[Document]:
    """递归字符分割器：按段落 → 句子 → 字符的优先级切分文本。

    使用纯 Python 实现，无需 langchain_text_splitters。
    """
    chunks: List[Document] = []

    for doc in documents:
        text = doc.page_content
        metadata = doc.metadata

        # 按段落切分
        paragraphs = _split_by_separators(text, ["\n\n", "\n"])

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(para) <= CHUNK_SIZE:
                if len(para) >= 10:  # 过滤过短片段
                    chunks.append(Document(
                        page_content=para,
                        metadata=dict(metadata),
                    ))
            else:
                # 段落过长，按句子切分
                sentences = _split_by_separators(
                    para,
                    ["。", "！", "？", "；", ".", "!", "?", ";"],
                )
                current_chunk = ""
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    if len(current_chunk) + len(sent) <= CHUNK_SIZE:
                        current_chunk += sent
                    else:
                        if len(current_chunk.strip()) >= 10:
                            chunks.append(Document(
                                page_content=current_chunk.strip(),
                                metadata=dict(metadata),
                            ))
                        # 块重叠：前一 chunk 末尾 CHUNK_OVERLAP 字符作为下一 chunk 前缀
                        prev_chunk = current_chunk.strip()
                        if CHUNK_OVERLAP > 0 and len(prev_chunk) > 0:
                            start = max(0, len(prev_chunk) - CHUNK_OVERLAP)
                            current_chunk = prev_chunk[start:] + sent
                        else:
                            current_chunk = sent

                # 处理最后一段
                if len(current_chunk.strip()) >= 10:
                    chunks.append(Document(
                        page_content=current_chunk.strip(),
                        metadata=dict(metadata),
                    ))

    logger.info(
        "Split %d documents into %d chunks (chunk_size=%d, overlap=%d)",
        len(documents),
        len(chunks),
        CHUNK_SIZE,
        CHUNK_OVERLAP,
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
            # 索引接口只接受单一命名空间，避免同一批 doc/ 文档写入多个命名空间
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
    vectorstore.clear()  # 全量重建
    vectorstore.add_documents(chunks)

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
    if not documents:
        # 库已清空：清空向量存储，避免残留旧条目可被检索
        vectorstore = get_vectorstore_for_namespace(ns)
        vectorstore.clear()
        return {"status": "empty", "documents": 0, "namespace": ns}

    vectorstore = get_vectorstore_for_namespace(ns)
    vectorstore.clear()  # 全量重建
    vectorstore.add_documents(documents)

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

        for ns in namespaces:
            vectorstore = get_vectorstore_for_namespace(ns)
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
