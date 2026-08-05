"""
Embedding 模型选择与 Minimax Embedding 客户端。

设计:
- `MinimaxEmbeddings` 实现 langchain_core 的 `Embeddings` 接口，
  通过 Minimax 的 OpenAI 兼容端点（`{base}/embeddings`）向量化文本，
  仅依赖 httpx，不引入额外 SDK。
- `get_embedding_model()` 工厂按配置决定使用远端 API 还是本地 TF-IDF：
  - 配置了 `EMBEDDING_API_KEY` → Minimax Embedding
  - 未配置 → 本地 TF-IDF（降级模式，启动时告警）
  运行时 API 失败的进一步降级由 `SimpleVectorStore` 处理（见 app/core/rag.py）。

Minimax Embedding 官方文档: https://platform.minimaxi.com/document/embeddings
OpenAI 兼容端点形如 https://api.minimaxi.com/v1，模型名如 embedding-1。
"""

import logging
from typing import List, Optional

import httpx

from langchain_core.embeddings import Embeddings

from app.core.config import settings

logger = logging.getLogger(__name__)

# 单次请求最大文本条数（Minimax 单请求支持批量 input）
DEFAULT_BATCH_SIZE: int = 32
# 请求超时（秒）
DEFAULT_TIMEOUT: float = 30.0


class MinimaxEmbeddings(Embeddings):
    """OpenAI 兼容的 Minimax Embedding 客户端。

    调用 `POST {api_base}/embeddings`，请求体为
    `{"model": ..., "input": [...]}`，鉴权头 `Authorization: Bearer <key>`。

    与对话 LLM（LLM_*）及运营分析（OPENAI_*）配置完全独立，使用单独的 EMBEDDING_* 配置项。
    """

    backend_name: str = "minimax"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        timeout: float = DEFAULT_TIMEOUT,
        _transport: Optional[httpx.BaseTransport] = None,
    ):
        """初始化客户端。

        Args:
            api_key: Minimax API Key（EMBEDDING_API_KEY）
            api_base: OpenAI 兼容端点基址，默认 https://api.minimaxi.com/v1
            model: Embedding 模型名，默认 embedding-1
            batch_size: 单次请求文本条数上限
            timeout: 请求超时秒数
            _transport: 仅供测试注入 httpx 传输层（如 MockTransport）
        """
        self.api_key = api_key or settings.EMBEDDING_API_KEY
        self.api_base = (api_base or settings.EMBEDDING_API_BASE).rstrip("/")
        self.model = model or settings.EMBEDDING_MODEL
        self.batch_size = batch_size
        self.timeout = timeout
        self._transport = _transport

    # -- langchain_core Embeddings 接口 --

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量向量化文档文本（自动分批）。"""
        if not texts:
            return []
        results: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            results.extend(self._request(batch))
        return results

    def embed_query(self, text: str) -> List[float]:
        """向量化查询文本。"""
        return self._request([text])[0]

    # -- 内部实现 --

    def _request(self, texts: List[str]) -> List[List[float]]:
        """调用 OpenAI 兼容 /embeddings 端点。

        Raises:
            RuntimeError: 未配置 API Key
            httpx.HTTPError: 网络/鉴权/服务端错误（由上层降级处理）
        """
        if not self.api_key:
            raise RuntimeError(
                "EMBEDDING_API_KEY 未配置，无法调用 Minimax Embedding API"
            )

        url = f"{self.api_base}/embeddings"
        payload = {"model": self.model, "input": texts}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        with httpx.Client(
            headers=headers,
            timeout=self.timeout,
            transport=self._transport,
        ) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        # OpenAI 兼容响应: {"data": [{"embedding": [...], "index": i}, ...]}
        # index 字段允许服务端乱序返回，按 index 恢复输入顺序，防止向量错位
        data_items = data.get("data") or []
        if len(data_items) != len(texts):
            raise RuntimeError(
                "Minimax Embedding 响应条数不符: "
                f"请求 {len(texts)} 条，返回 {len(data_items)} 条"
            )
        if all("index" in item for item in data_items):
            sorted_items = sorted(data_items, key=lambda item: item["index"])
            if [item["index"] for item in sorted_items] == list(
                range(len(sorted_items))
            ):
                data_items = sorted_items
        embeddings = [item["embedding"] for item in data_items]
        return embeddings


def get_embedding_model() -> Embeddings:
    """按配置返回 embedding 模型。

    优先使用 Minimax Embedding API；未配置 `EMBEDDING_API_KEY` 时
    降级为本地 TF-IDF 向量化，并记录告警日志（可诊断）。
    """
    if settings.EMBEDDING_API_KEY:
        logger.info(
            "使用 Minimax Embedding: base=%s model=%s",
            settings.EMBEDDING_API_BASE,
            settings.EMBEDDING_MODEL,
        )
        return MinimaxEmbeddings()

    # 延迟导入避免与 app.core.rag 循环依赖
    from app.core.rag import TFIDFEmbeddings

    logger.warning(
        "EMBEDDING_API_KEY 未配置，RAG 向量化降级为本地 TF-IDF"
        "（配置 EMBEDDING_API_KEY 可启用 Minimax Embedding）"
    )
    return TFIDFEmbeddings()
