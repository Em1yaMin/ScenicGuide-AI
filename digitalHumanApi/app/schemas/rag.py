"""RAG 相关的请求/响应模型。"""

from typing import List, Optional

from pydantic import BaseModel, Field


class RagSearchRequest(BaseModel):
    """RAG 检索请求。"""

    query: str = Field(..., description="查询文本", min_length=1, max_length=2000)
    top_k: int = Field(default=3, description="返回结果数量", ge=1, le=20)


class RagSearchResult(BaseModel):
    """单条检索结果。"""

    content: str = Field(..., description="文档片段内容")
    source: str = Field(default="", description="来源文件名")
    score: float = Field(..., description="相似度分数")


class RagSearchResponse(BaseModel):
    """RAG 检索响应。"""

    query: str = Field(..., description="原始查询")
    results: List[RagSearchResult] = Field(default_factory=list, description="检索结果")
    context: str = Field(default="", description="拼接后的上下文字符串")


class RagIndexResponse(BaseModel):
    """RAG 索引响应。"""

    status: str = Field(..., description="状态: ok / empty / error")
    documents: int = Field(default=0, description="加载的文档数")
    chunks: int = Field(default=0, description="生成的 chunk 数")
    message: Optional[str] = Field(default=None, description="附加信息")
