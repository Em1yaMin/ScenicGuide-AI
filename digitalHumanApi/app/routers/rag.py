"""
RAG 知识库检索 API 路由。

提供三个核心接口：
- POST /api/rag/search  — 语义检索（支持命名空间限定）
- POST /api/rag/index   — 触发全量重新索引（指定命名空间）
- GET  /api/rag/status  — 各命名空间状态与 embedding 后端/降级信息
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter

from app.core.rag import normalize_namespaces
from app.schemas.rag import (
    RagIndexRequest,
    RagIndexResponse,
    RagSearchRequest,
    RagSearchResponse,
)
from app.services.rag_service import (
    build_rag_context,
    get_rag_meta,
    index_documents,
    search_documents,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["RAG知识库"])


@router.post("/search", response_model=RagSearchResponse)
async def rag_search(request: RagSearchRequest) -> RagSearchResponse:
    """语义检索本地知识库。

    输入查询文本，返回最相关的文档片段及拼接后的上下文。
    可通过 namespace 参数限定检索范围（默认 knowledge+documents）。
    响应中透出 embedding_backend / degraded 可观测标记：
    当 Embedding API 不可用时自动降级为本地 TF-IDF，对话链路不中断。
    """
    results = await search_documents(
        query=request.query,
        top_k=request.top_k,
        namespace=request.namespace,
    )
    context = build_rag_context(results)
    meta = get_rag_meta()
    searched_namespaces = normalize_namespaces(request.namespace)

    logger.info(
        "RAG search: query='%s', top_k=%d, found=%d, backend=%s, degraded=%s",
        request.query[:80],
        request.top_k,
        len(results),
        meta["embedding_backend"],
        meta["degraded"],
    )

    return RagSearchResponse(
        query=request.query,
        results=[
            {
                "content": r["content"],
                "source": r["source"],
                "score": r["score"],
                "namespace": r.get("namespace", ""),
            }
            for r in results
        ],
        context=context,
        namespaces=searched_namespaces,
        embedding_backend=meta["embedding_backend"],
        degraded=meta["degraded"],
        degraded_reason=meta["degraded_reason"],
    )


@router.post("/index", response_model=RagIndexResponse)
async def rag_index(
    request: Optional[RagIndexRequest] = None,
) -> RagIndexResponse:
    """触发全量重新索引。

    重新扫描 doc/ 目录，加载所有景区资料文件，
    分块、向量化并存储到指定命名空间（默认 documents）的向量存储
    （JSON 文件持久化）。
    """
    namespace = request.namespace if request else None
    try:
        result = await index_documents(namespace=namespace)
        return RagIndexResponse(
            status=result["status"],
            documents=result["documents"],
            chunks=result["chunks"],
            namespace=result.get("namespace", "documents"),
            embedding_backend=result.get("embedding_backend", "tfidf"),
            degraded=result.get("degraded", False),
            degraded_reason=result.get("degraded_reason", ""),
            message=f"已索引 {result['documents']} 个文档，生成 {result['chunks']} 个片段",
        )
    except Exception as e:
        logger.exception("RAG index failed")
        return RagIndexResponse(
            status="error",
            documents=0,
            chunks=0,
            namespace=namespace or "documents",
            message=f"索引失败: {str(e)}",
        )


@router.get("/status", response_model=Dict[str, Any])
async def rag_status() -> Dict[str, Any]:
    """查询 RAG 知识库状态（各命名空间文档数、embedding 后端等）。"""
    try:
        from app.core.rag import (
            ALL_NAMESPACES,
            NAMESPACE_COLLECTIONS,
            get_vectorstore_for_namespace,
        )

        namespaces = []
        total = 0
        backends = set()
        degraded = False
        degraded_reason = ""
        for ns in ALL_NAMESPACES:
            vectorstore = get_vectorstore_for_namespace(ns)
            count = vectorstore.count()
            total += count
            backend = vectorstore.embedding_backend
            backends.add(backend)
            if vectorstore.degraded:
                degraded = True
                degraded_reason = vectorstore.degraded_reason
            namespaces.append({
                "namespace": ns,
                "collection_name": NAMESPACE_COLLECTIONS[ns],
                "document_count": count,
                "embedding_backend": backend,
                "degraded": vectorstore.degraded,
                "degraded_reason": vectorstore.degraded_reason,
            })

        return {
            "status": "ready",
            "document_count": total,
            "namespaces": namespaces,
            "embedding_backend": ",".join(sorted(backends)) or "unknown",
            "degraded": degraded,
            "degraded_reason": degraded_reason,
        }
    except Exception as e:
        logger.warning("RAG status check failed: %s", e)
        return {
            "status": "not_ready",
            "document_count": 0,
            "namespaces": [],
            "error": str(e),
        }
