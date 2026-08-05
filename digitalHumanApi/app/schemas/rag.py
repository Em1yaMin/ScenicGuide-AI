"""RAG 相关的请求/响应模型。"""

from typing import List, Optional

from pydantic import BaseModel, Field


class RagSearchRequest(BaseModel):
    """RAG 检索请求。"""

    query: str = Field(..., description="查询文本", min_length=1, max_length=2000)
    top_k: int = Field(default=3, description="返回结果数量", ge=1, le=20)
    namespace: Optional[str] = Field(
        default=None,
        description="命名空间，逗号分隔多个（knowledge/documents/user_memory）；"
        "不传时默认检索 knowledge+documents 公共命名空间",
    )


class RagIndexRequest(BaseModel):
    """RAG 索引请求。"""

    namespace: Optional[str] = Field(
        default=None,
        description="目标命名空间（knowledge/documents/user_memory）；"
        "不传时默认 documents（doc/景区知识库/ 文档索引）",
    )


class RagSearchResult(BaseModel):
    """单条检索结果。"""

    content: str = Field(..., description="文档片段内容")
    source: str = Field(default="", description="来源文件名")
    score: float = Field(..., description="相似度分数")
    namespace: str = Field(default="", description="命中的命名空间")


class RagSearchResponse(BaseModel):
    """RAG 检索响应。"""

    query: str = Field(..., description="原始查询")
    results: List[RagSearchResult] = Field(default_factory=list, description="检索结果")
    context: str = Field(default="", description="拼接后的上下文字符串")
    namespaces: List[str] = Field(default_factory=list, description="实际检索的命名空间")
    embedding_backend: str = Field(default="tfidf", description="embedding 后端（minimax/tfidf）")
    degraded: bool = Field(default=False, description="是否降级（Embedding API 不可用）")
    degraded_reason: str = Field(default="", description="降级原因")


class RagIndexResponse(BaseModel):
    """RAG 索引响应。"""

    status: str = Field(..., description="状态: ok / empty / error")
    documents: int = Field(default=0, description="加载的文档数")
    chunks: int = Field(default=0, description="生成的 chunk 数")
    namespace: str = Field(default="documents", description="写入的命名空间")
    embedding_backend: str = Field(default="tfidf", description="embedding 后端（minimax/tfidf）")
    degraded: bool = Field(default=False, description="是否降级（Embedding API 不可用）")
    degraded_reason: str = Field(default="", description="降级原因")
    message: Optional[str] = Field(default=None, description="附加信息")
