"""
RAG 知识库检索 API 路由。

提供两个核心接口：
- POST /api/rag/search  — 语义检索
- POST /api/rag/index   — 触发全量重新索引
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter

from app.schemas.rag import RagIndexResponse, RagSearchRequest, RagSearchResponse
from app.services.rag_service import (
    build_rag_context,
    index_documents,
    search_documents,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["RAG知识库"])


@router.post("/search", response_model=RagSearchResponse)
async def rag_search(request: RagSearchRequest) -> RagSearchResponse:
    """语义检索本地知识库。

    输入查询文本，返回最相关的文档片段及拼接后的上下文。
    可用于对话前的知识增强。
    """
    results = await search_documents(
        query=request.query,
        top_k=request.top_k,
    )
    context = build_rag_context(results)

    logger.info(
        "RAG search: query='%s', top_k=%d, found=%d",
        request.query[:80],
        request.top_k,
        len(results),
    )

    return RagSearchResponse(
        query=request.query,
        results=[
            {"content": r["content"], "source": r["source"], "score": r["score"]}
            for r in results
        ],
        context=context,
    )


@router.post("/index", response_model=RagIndexResponse)
async def rag_index() -> RagIndexResponse:
    """触发全量重新索引。

    重新扫描 doc/ 目录，加载所有景区资料文件，
    分块、向量化并存储到 Chroma 向量数据库。
    """
    try:
        result = await index_documents()
        return RagIndexResponse(
            status=result["status"],
            documents=result["documents"],
            chunks=result["chunks"],
            message=f"已索引 {result['documents']} 个文档，生成 {result['chunks']} 个片段",
        )
    except Exception as e:
        logger.exception("RAG index failed")
        return RagIndexResponse(
            status="error",
            documents=0,
            chunks=0,
            message=f"索引失败: {str(e)}",
        )


@router.get("/status", response_model=Dict[str, Any])
async def rag_status() -> Dict[str, Any]:
    """查询 RAG 知识库状态（文档数、集合信息等）。"""
    try:
        from app.core.rag import get_vectorstore

        vectorstore = get_vectorstore()
        count = vectorstore.count()
        return {
            "status": "ready",
            "collection_name": vectorstore.collection_name,
            "document_count": count,
            "embedding_model": "TF-IDF (local)",
            "vocabulary_size": len(vectorstore.embedding._vocabulary) if vectorstore.embedding.is_fitted else 0,
        }
    except Exception as e:
        logger.warning("RAG status check failed: %s", e)
        return {
            "status": "not_ready",
            "document_count": 0,
            "error": str(e),
        }
