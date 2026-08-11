"""
Embedding 模型选择与 Embedding 客户端。

设计:
- `MinimaxEmbeddings` 实现 langchain_core 的 `Embeddings` 接口，
  通过远端 Embedding 端点（`{base}/embeddings`）向量化文本，
  仅依赖 httpx，不引入额外 SDK。
- `get_embedding_model()` 工厂按配置决定使用远端 API 还是本地 TF-IDF：
  - 配置了 `EMBEDDING_API_KEY` → 远端 Embedding
  - 未配置 → 本地 TF-IDF（降级模式，启动时告警）
  运行时 API 失败的进一步降级由 `SimpleVectorStore` 处理（见 app/core/rag.py）。

请求体格式（`EMBEDDING_API_STYLE` 开关，2026-08-07 从 Minimax 迁移到 Qwen/DashScope 实测）：
- `minimax`（默认，兼容旧配置）：Minimax 原生 API 格式（非 OpenAI 兼容）——
  请求体 `{"model", "texts", "type"}`，`type` 为 `query`（检索查询）/ `db`（文档库），
  响应为 `{"vectors": [[...], ...]}`。实测（2026-08-06）模型 embo-01：`embed_documents` 传
  `type=db`、`embed_query` 传 `type=query`（两者向量空间不同，混用会显著降低检索质量）。
- `openai`：OpenAI 兼容格式——请求体 `{"model", "input": [...]}`，响应
  `{"data": [{"embedding": [...]}]}`。DashScope compatible-mode 实测（2026-08-07）
  模型 qwen3.7-text-embedding（1024 维），端点 https://dashscope.aliyuncs.com/compatible-mode/v1。
  该格式无 query/db 区分，响应解析与 minimax 共用（兼容两种响应）。
  **单请求 input 上限 20 条**（2026-08-11 船长实测：21+ 返回 400 "batch size is invalid"），
  默认批量 `DEFAULT_BATCH_SIZE=20`（可用 EMBEDDING_BATCH_SIZE 覆盖，见 config.py）。

Minimax Embedding 官方文档: https://platform.minimaxi.com/document/embeddings
"""

import logging
from typing import List, Optional

import httpx

from langchain_core.embeddings import Embeddings

from app.core.config import settings

logger = logging.getLogger(__name__)

# 单次请求最大文本条数。
# 2026-08-11 船长实测：DashScope compatible-mode /embeddings 单请求 input 上限 20 条，
# 21+ 返回 400 "batch size is invalid, it should not be larger than 20"（此前硬编码 32 导致
# 批量向量化整体失败并静默降级 TF-IDF）。默认 20 即 DashScope 硬上限；可用环境变量
# EMBEDDING_BATCH_SIZE 覆盖（见 config.py Settings），适配其他 Embedding 后端的批量限制。
DEFAULT_BATCH_SIZE: int = 20
# 请求超时（秒）
DEFAULT_TIMEOUT: float = 30.0


class MinimaxEmbeddings(Embeddings):
    """Embedding 客户端（支持 Minimax 原生 / OpenAI 兼容两种请求体格式）。

    调用 `POST {api_base}/embeddings`：
    - `api_style="minimax"`（默认）：请求体 `{"model", "texts", "type"}`，
      实测模型 embo-01；
    - `api_style="openai"`：请求体 `{"model", "input": [...]}`（DashScope
      compatible-mode，实测模型 qwen3.7-text-embedding，1024 维）。

    鉴权头 `Authorization: Bearer <key>`；响应解析兼容 Minimax 原生
    （`vectors` 数组）与 OpenAI 兼容（`data[].embedding`）两种格式。

    与对话 LLM（LLM_*）及运营分析（OPENAI_*）配置完全独立，使用单独的 EMBEDDING_* 配置项。
    """

    backend_name: str = "minimax"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        api_style: Optional[str] = None,
        batch_size: Optional[int] = None,
        timeout: float = DEFAULT_TIMEOUT,
        _transport: Optional[httpx.BaseTransport] = None,
    ):
        """初始化客户端。

        Args:
            api_key: Embedding API Key（EMBEDDING_API_KEY）
            api_base: Embedding 端点基址，默认 https://api.minimaxi.com/v1
            model: Embedding 模型名，默认 embo-01
            api_style: 请求体格式 "minimax"|"openai"（EMBEDDING_API_STYLE，默认 minimax）
            batch_size: 单次请求文本条数上限（None 时取 EMBEDDING_BATCH_SIZE，默认 20）
            timeout: 请求超时秒数
            _transport: 仅供测试注入 httpx 传输层（如 MockTransport）
        """
        self.api_key = api_key or settings.EMBEDDING_API_KEY
        self.api_base = (api_base or settings.EMBEDDING_API_BASE).rstrip("/")
        self.model = model or settings.EMBEDDING_MODEL
        self.api_style = (api_style or settings.EMBEDDING_API_STYLE or "minimax").lower()
        if self.api_style not in ("minimax", "openai"):
            logger.warning(
                "未知 EMBEDDING_API_STYLE=%r，回退 minimax（可选值 minimax|openai）",
                self.api_style,
            )
            self.api_style = "minimax"
        self.batch_size = (
            batch_size if batch_size is not None else settings.EMBEDDING_BATCH_SIZE
        )
        self.timeout = timeout
        self._transport = _transport

    # -- langchain_core Embeddings 接口 --

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量向量化文档文本（自动分批；type=db，文档库向量）。"""
        if not texts:
            return []
        results: List[List[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            results.extend(self._request(batch, type_="db"))
        return results

    def embed_query(self, text: str) -> List[float]:
        """向量化查询文本（type=query，检索查询向量）。"""
        return self._request([text], type_="query")[0]

    # -- 内部实现 --

    def _request(self, texts: List[str], type_: str) -> List[List[float]]:
        """调用 Embedding 端点。

        Args:
            texts: 待向量化文本列表
            type_: "db"（文档库向量）或 "query"（检索查询向量），
                  仅 minimax 原生格式使用；openai 风格请求体无此字段

        Raises:
            RuntimeError: 未配置 API Key / 响应格式异常
            httpx.HTTPError: 网络/鉴权/服务端错误（由上层降级处理）
        """
        if not self.api_key:
            raise RuntimeError(
                "EMBEDDING_API_KEY 未配置，无法调用 Embedding API"
            )

        url = f"{self.api_base}/embeddings"
        if self.api_style == "openai":
            # OpenAI 兼容请求体（DashScope compatible-mode，实测 qwen3.7-text-embedding）
            # {"model", "input": [...]}，无 type 字段（该端点无 query/db 区分）
            payload = {"model": self.model, "input": texts}
        else:
            # Minimax 原生请求体（实测 embo-01）：{"model", "texts", "type"}
            payload = {"model": self.model, "texts": texts, "type": type_}
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

        # Minimax 业务错误（仅原生格式会带 base_resp）：HTTP 200 但响应体携带
        # base_resp 错误码（如 status_code=1008 insufficient balance，实测 2026-08-06）。
        # 若未检查会落入下方"响应条数不符"的误导性错误，故此处先行暴露。
        # openai 风格（DashScope compatible-mode）错误走标准 HTTP 状态码，无 base_resp。
        if self.api_style != "openai":
            base_resp = data.get("base_resp") or {}
            status_code = base_resp.get("status_code")
            if status_code not in (None, 0):
                status_msg = base_resp.get("status_msg") or "unknown error"
                raise RuntimeError(
                    f"Embedding API 返回业务错误 status_code={status_code}: {status_msg}"
                )

        # Minimax 原生响应: {"vectors": [[...], ...]}（按请求顺序返回）
        vectors = data.get("vectors")
        if isinstance(vectors, list):
            if len(vectors) != len(texts):
                raise RuntimeError(
                    "Embedding 响应条数不符: "
                    f"请求 {len(texts)} 条，返回 {len(vectors)} 条"
                )
            return vectors

        # OpenAI 兼容响应:
        # {"data": [{"embedding": [...], "index": i}, ...]}
        # index 字段允许服务端乱序返回，按 index 恢复输入顺序，防止向量错位
        data_items = data.get("data") or []
        if len(data_items) != len(texts):
            raise RuntimeError(
                "Embedding 响应条数不符: "
                f"请求 {len(texts)} 条，返回 {len(data_items)} 条"
            )
        if all("index" in item for item in data_items):
            sorted_items = sorted(data_items, key=lambda item: item["index"])
            if [item["index"] for item in sorted_items] == list(
                range(len(sorted_items))
            ):
                data_items = sorted_items
        return [item["embedding"] for item in data_items]


def get_embedding_model() -> Embeddings:
    """按配置返回 embedding 模型。

    优先使用 Minimax Embedding API；未配置 `EMBEDDING_API_KEY` 时
    降级为本地 TF-IDF 向量化，并记录告警日志（可诊断）。
    """
    if settings.EMBEDDING_API_KEY:
        logger.info(
            "使用远端 Embedding: base=%s model=%s style=%s",
            settings.EMBEDDING_API_BASE,
            settings.EMBEDDING_MODEL,
            settings.EMBEDDING_API_STYLE,
        )
        return MinimaxEmbeddings()

    # 延迟导入避免与 app.core.rag 循环依赖
    from app.core.rag import TFIDFEmbeddings

    logger.warning(
        "EMBEDDING_API_KEY 未配置，RAG 向量化降级为本地 TF-IDF"
        "（配置 EMBEDDING_API_KEY 可启用远端 Embedding）"
    )
    return TFIDFEmbeddings()
