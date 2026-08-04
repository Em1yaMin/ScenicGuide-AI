"""Embedding 升级（Minimax）与命名空间隔离测试。

覆盖：
1. MinimaxEmbeddings 客户端：OpenAI 兼容请求格式（httpx.MockTransport）、
   批量分批、未配置 key 时报错。
2. get_embedding_model 工厂：配置 key → Minimax；未配置 → TF-IDF 降级。
3. 自定义 embedding（确定性向量）接入 SimpleVectorStore：向量化、检索、持久化重载。
4. 命名空间隔离：knowledge 检索不命中 documents（独立索引与持久化文件）。
5. Embedding API 不可用时的降级：索引/检索自动回退 TF-IDF，degraded 标记可观测。
"""
import json
import math

import httpx
import pytest

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.core.config import settings
from app.core.embedding import MinimaxEmbeddings, get_embedding_model
from app.core.rag import (
    DEFAULT_SEARCH_NAMESPACES,
    NAMESPACE_COLLECTIONS,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_KNOWLEDGE,
    NAMESPACE_USER_MEMORY,
    SimpleVectorStore,
    TFIDFEmbeddings,
    get_vectorstore_for_namespace,
    normalize_namespaces,
)
from app.services.rag_service import index_documents, search_documents

DIFY_DOC = "黄果树瀑布位于贵州省安顺市，是亚洲最大的瀑布，水势浩大，门票价格随季节浮动。"
KNOWLEDGE_DOC = "景区开放时间：旺季 8:00-18:00，淡季 8:30-17:30，请提前预约。"
MEMORY_DOC = "用户偏好：喜欢徒步路线，对溶洞景观特别感兴趣。"


# ---------------------------------------------------------------------------
# 测试用确定性 Embedding
# ---------------------------------------------------------------------------

class FakeEmbeddings(Embeddings):
    """确定性向量化：字符 → 固定 hash 桶（不依赖进程内 hash 随机化）。"""

    backend_name = "fake"
    DIM = 64

    @staticmethod
    def _char_hash(ch: str) -> int:
        # 多项式滚动 hash，跨进程稳定
        h = 0
        for c in ch:
            h = (h * 31 + ord(c)) & 0xFFFF
        return h

    def _embed(self, text: str) -> list:
        vec = [0.0] * self.DIM
        for ch in text:
            vec[self._char_hash(ch) % self.DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts):
        return [self._embed(t) for t in texts]

    def embed_query(self, text):
        return self._embed(text)


class FailingEmbeddings(Embeddings):
    """模拟 Embedding API 完全不可用（网络失败/鉴权失败）。"""

    backend_name = "failing"

    def embed_documents(self, texts):
        raise ConnectionError("simulated network failure")

    def embed_query(self, text):
        raise ConnectionError("simulated network failure")


# ---------------------------------------------------------------------------
# Minimax Embedding 客户端
# ---------------------------------------------------------------------------

def _mock_minimax_transport(captured: dict, fail: bool = False):
    """构造返回固定向量的 MockTransport，并捕获请求细节。"""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        if fail:
            return httpx.Response(401, json={"error": "unauthorized"})
        texts = captured["body"]["input"]
        return httpx.Response(200, json={
            "data": [
                {"embedding": [float(i + 1) / 10, 0.2, 0.3], "index": i}
                for i in range(len(texts))
            ],
            "model": captured["body"]["model"],
        })

    return httpx.MockTransport(handler)


def test_minimax_embeddings_openai_compatible_request():
    """请求格式符合 OpenAI 兼容 /embeddings 端点约定。"""
    captured = {}
    emb = MinimaxEmbeddings(
        api_key="test-key",
        api_base="https://api.minimaxi.com/v1",
        model="embedding-1",
        _transport=_mock_minimax_transport(captured),
    )
    vecs = emb.embed_documents(["你好", "世界"])
    assert vecs == [[0.1, 0.2, 0.3], [0.2, 0.2, 0.3]]
    assert captured["url"] == "https://api.minimaxi.com/v1/embeddings"
    assert captured["method"] == "POST"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"] == {"model": "embedding-1", "input": ["你好", "世界"]}

    # embed_query 走同一端点
    q_vec = emb.embed_query("查询")
    assert q_vec == [0.1, 0.2, 0.3]


def test_minimax_embeddings_batches_large_inputs():
    """超过 batch_size 时自动分批请求。"""
    captured = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["count"] += 1
        body = json.loads(request.content)
        texts = body["input"]
        return httpx.Response(200, json={
            "data": [
                {"embedding": [float(i), 0.0, 0.0], "index": i}
                for i in range(len(texts))
            ],
            "model": body["model"],
        })

    emb = MinimaxEmbeddings(
        api_key="k",
        api_base="https://api.minimaxi.com/v1",
        model="embedding-1",
        batch_size=2,
        _transport=httpx.MockTransport(handler),
    )
    vecs = emb.embed_documents(["a", "b", "c", "d", "e"])
    assert len(vecs) == 5
    assert captured["count"] == 3  # 2 + 2 + 1


def test_minimax_embeddings_raises_without_key():
    """未配置 key 时给出明确报错（由上层降级处理）。"""
    emb = MinimaxEmbeddings(api_key="", api_base="https://api.minimaxi.com/v1")
    with pytest.raises(RuntimeError, match="EMBEDDING_API_KEY"):
        emb.embed_query("test")


def test_minimax_embeddings_http_error_propagates():
    """HTTP 鉴权失败异常向上传播（由 SimpleVectorStore 捕获降级）。"""
    captured = {}
    emb = MinimaxEmbeddings(
        api_key="bad-key",
        api_base="https://api.minimaxi.com/v1",
        model="embedding-1",
        _transport=_mock_minimax_transport(captured, fail=True),
    )
    with pytest.raises(httpx.HTTPStatusError):
        emb.embed_query("test")


# ---------------------------------------------------------------------------
# 工厂：配置 key / 未配置 key
# ---------------------------------------------------------------------------

def test_factory_uses_minimax_when_key_configured(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "test-key")
    monkeypatch.setattr(settings, "EMBEDDING_API_BASE", "https://api.minimaxi.com/v1")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "embedding-1")
    model = get_embedding_model()
    assert isinstance(model, MinimaxEmbeddings)
    assert model.api_key == "test-key"


def test_factory_falls_back_to_tfidf_without_key(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", "")
    model = get_embedding_model()
    assert isinstance(model, TFIDFEmbeddings)


# ---------------------------------------------------------------------------
# 自定义 embedding 接入向量存储 + 持久化重载
# ---------------------------------------------------------------------------

def test_store_with_custom_embedding_search_and_reload(tmp_path):
    """确定性 mock embedding：索引、检索、持久化重载后结果一致。"""
    store = SimpleVectorStore(
        collection_name="emb_test",
        persist_dir=tmp_path,
        embedding=FakeEmbeddings(),
    )
    store.add_documents([
        Document(page_content=DIFY_DOC, metadata={"filename": "dify.md"}),
        Document(page_content=KNOWLEDGE_DOC, metadata={"filename": "faq.md"}),
    ])
    assert store.embedding_backend == "fake"
    assert not store.degraded

    before = [
        (d.page_content, round(s, 6))
        for d, s in store.similarity_search_with_score("黄果树瀑布门票", k=2)
    ]
    assert before, "mock embedding 检索应有结果"
    assert "黄果树瀑布" in before[0][0]

    # 持久化重载（新实例，同一 embedding）
    store2 = SimpleVectorStore(
        collection_name="emb_test",
        persist_dir=tmp_path,
        embedding=FakeEmbeddings(),
    )
    after = [
        (d.page_content, round(s, 6))
        for d, s in store2.similarity_search_with_score("黄果树瀑布门票", k=2)
    ]
    assert before == after
    assert store2.count() == 2
    assert store2.embedding_backend == "fake"


# ---------------------------------------------------------------------------
# 命名空间隔离
# ---------------------------------------------------------------------------

def test_namespace_collections_are_distinct():
    """三个命名空间映射到独立持久化集合（独立索引/文件）。"""
    assert NAMESPACE_COLLECTIONS[NAMESPACE_KNOWLEDGE] != NAMESPACE_COLLECTIONS[NAMESPACE_DOCUMENTS]
    assert NAMESPACE_COLLECTIONS[NAMESPACE_DOCUMENTS] != NAMESPACE_COLLECTIONS[NAMESPACE_USER_MEMORY]
    # documents 命名空间复用旧集合名，兼容升级前已持久化的数据
    assert NAMESPACE_COLLECTIONS[NAMESPACE_DOCUMENTS] == "scenic_knowledge"


def test_namespace_isolation_knowledge_does_not_hit_documents(tmp_path):
    """knowledge 检索不命中 documents 命名空间，反之亦然。"""
    knowledge_store = SimpleVectorStore(
        collection_name=NAMESPACE_COLLECTIONS[NAMESPACE_KNOWLEDGE],
        persist_dir=tmp_path,
        embedding=FakeEmbeddings(),
    )
    docs_store = SimpleVectorStore(
        collection_name=NAMESPACE_COLLECTIONS[NAMESPACE_DOCUMENTS],
        persist_dir=tmp_path,
        embedding=FakeEmbeddings(),
    )
    knowledge_store.add_documents([Document(page_content=KNOWLEDGE_DOC)])
    docs_store.add_documents([Document(page_content=DIFY_DOC)])

    # 各自独立持久化文件
    assert (tmp_path / "knowledge_docs.json").exists()
    assert (tmp_path / "scenic_knowledge_docs.json").exists()

    # knowledge 检索只命中 knowledge
    k_results = knowledge_store.similarity_search_with_score("开放时间 预约", k=5)
    assert k_results
    assert all("开放时间" in d.page_content for d, _ in k_results)

    # documents 检索只命中 documents（同一查询不得命中 knowledge 内容）
    d_results = docs_store.similarity_search_with_score("开放时间 预约", k=5)
    assert d_results
    assert all("黄果树瀑布" in d.page_content for d, _ in d_results)

    # knowledge 检索只命中 knowledge 索引中的内容（documents 内容不在其中）
    k_empty = knowledge_store.similarity_search_with_score("亚洲最大的瀑布", k=5)
    assert k_empty
    assert all("开放时间" in d.page_content for d, _ in k_empty)


def test_normalize_namespaces_default_and_validation():
    """namespace 参数解析：默认公共命名空间、非法值剔除与回退。"""
    assert normalize_namespaces(None) == list(DEFAULT_SEARCH_NAMESPACES)
    assert normalize_namespaces("knowledge") == [NAMESPACE_KNOWLEDGE]
    assert normalize_namespaces(" knowledge , documents ") == [
        NAMESPACE_KNOWLEDGE,
        NAMESPACE_DOCUMENTS,
    ]
    assert normalize_namespaces("bogus") == list(DEFAULT_SEARCH_NAMESPACES)
    assert normalize_namespaces("knowledge,bogus") == [NAMESPACE_KNOWLEDGE]

    with pytest.raises(ValueError):
        get_vectorstore_for_namespace("bogus")


# ---------------------------------------------------------------------------
# Embedding API 不可用时的降级
# ---------------------------------------------------------------------------

def test_degrade_to_tfidf_when_embedding_api_down(tmp_path):
    """索引时 API 失败 → 自动降级 TF-IDF，degraded 标记可观测，检索仍可用。"""
    store = SimpleVectorStore(
        collection_name="degrade",
        persist_dir=tmp_path,
        embedding=FailingEmbeddings(),
    )
    store.add_documents([
        Document(page_content=DIFY_DOC, metadata={"filename": "dify.md"}),
        Document(page_content=KNOWLEDGE_DOC, metadata={"filename": "faq.md"}),
    ])

    assert store.degraded
    assert "simulated network failure" in store.degraded_reason
    assert store.embedding_backend == "tfidf"  # 已切换到 TF-IDF 备份

    results = store.similarity_search_with_score("黄果树瀑布门票", k=2)
    assert results, "降级后检索仍可用"
    assert "黄果树瀑布" in results[0][0].page_content


def test_degrade_on_query_when_query_embedding_fails(tmp_path):
    """索引正常但查询时 API 失败 → 查询路径降级，仍返回结果并标记。"""

    class QueryFailingEmbeddings(FakeEmbeddings):
        backend_name = "query-failing"

        def embed_query(self, text):
            raise ConnectionError("query api down")

    store = SimpleVectorStore(
        collection_name="degrade_query",
        persist_dir=tmp_path,
        embedding=QueryFailingEmbeddings(),
    )
    store.add_documents([
        Document(page_content=DIFY_DOC),
        Document(page_content=KNOWLEDGE_DOC),
    ])
    assert not store.degraded  # 索引阶段正常

    results = store.similarity_search_with_score("黄果树瀑布", k=2)
    assert store.degraded
    assert store.embedding_backend == "tfidf"
    assert results, "查询降级后仍返回结果"


def test_degraded_persistence_reload_still_works(tmp_path):
    """降级写入的持久化数据重载后（API 仍不可用）检索正常。"""
    store = SimpleVectorStore(
        collection_name="degrade_reload",
        persist_dir=tmp_path,
        embedding=FailingEmbeddings(),
    )
    store.add_documents([Document(page_content=DIFY_DOC)])
    assert store.degraded

    store2 = SimpleVectorStore(
        collection_name="degrade_reload",
        persist_dir=tmp_path,
        embedding=FailingEmbeddings(),
    )
    results = store2.similarity_search_with_score("黄果树瀑布", k=2)
    assert results
    assert store2.degraded
    assert store2.embedding_backend == "tfidf"


# ---------------------------------------------------------------------------
# 端到端：Minimax 客户端（MockTransport）接入向量存储
# ---------------------------------------------------------------------------

def test_store_with_minimax_mock_end_to_end(tmp_path):
    """真实 MinimaxEmbeddings 客户端（mock 传输层）接入存储：索引+检索。"""
    captured = {}
    emb = MinimaxEmbeddings(
        api_key="test-key",
        api_base="https://api.minimaxi.com/v1",
        model="embedding-1",
        _transport=_mock_minimax_transport(captured),
    )
    store = SimpleVectorStore(
        collection_name="minimax_e2e",
        persist_dir=tmp_path,
        embedding=emb,
    )
    store.add_documents([Document(page_content=DIFY_DOC)])
    assert store.embedding_backend == "minimax"
    assert not store.degraded
    # 每次向量化都实际走了 /embeddings 端点
    assert captured["url"].endswith("/embeddings")

    results = store.similarity_search_with_score("黄果树瀑布", k=2)
    assert results
    assert store.embedding_backend == "minimax"


# ---------------------------------------------------------------------------
# 服务层 / API 层：namespace 参数贯通
# ---------------------------------------------------------------------------

async def test_index_and_search_service_with_namespace(tmp_path):
    """索引/检索服务层接受 namespace 参数，且命名空间互不命中。"""
    (tmp_path / "a.md").write_text(
        "# 黄果树瀑布\n黄果树瀑布位于贵州安顺，是亚洲最大的瀑布。", encoding="utf-8"
    )
    (tmp_path / "b.md").write_text(
        "# 荔波小七孔\n世界自然遗产地，喀斯特地貌景观。", encoding="utf-8"
    )

    result = await index_documents(doc_dir=tmp_path, namespace="documents")
    assert result["status"] == "ok"
    assert result["documents"] == 2
    assert result["namespace"] == "documents"
    assert result["embedding_backend"] in ("minimax", "tfidf")

    # documents 命名空间检索命中
    results = await search_documents("黄果树瀑布", namespace="documents", top_k=2)
    assert results
    assert results[0]["namespace"] == "documents"
    assert "黄果树瀑布" in results[0]["content"]

    # knowledge 命名空间（未索引）不命中同一查询
    results_k = await search_documents("黄果树瀑布", namespace="knowledge", top_k=2)
    assert results_k == []

    # 默认命名空间（knowledge+documents）命中 documents 内容
    results_default = await search_documents("黄果树瀑布", top_k=2)
    assert results_default
    assert results_default[0]["namespace"] == "documents"


async def test_rag_search_api_accepts_namespace(client):
    """/api/rag/search 支持 namespace 参数，默认行为不破坏现有调用方。"""
    # 不带 namespace（现有调用方）→ 默认公共命名空间
    resp = await client.post("/api/rag/search", json={"query": "黄果树瀑布"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["namespaces"] == list(DEFAULT_SEARCH_NAMESPACES)
    assert "embedding_backend" in data
    assert "degraded" in data

    # 带单命名空间
    resp = await client.post(
        "/api/rag/search",
        json={"query": "黄果树瀑布", "namespace": "user_memory"},
    )
    assert resp.status_code == 200
    assert resp.json()["namespaces"] == ["user_memory"]

    # 带逗号分隔多命名空间
    resp = await client.post(
        "/api/rag/search",
        json={"query": "黄果树瀑布", "namespace": "knowledge,documents"},
    )
    assert resp.status_code == 200
    assert resp.json()["namespaces"] == ["knowledge", "documents"]


async def test_rag_index_api_accepts_namespace(client):
    """/api/rag/index 支持 namespace 参数（无 body 的旧调用方式仍兼容）。"""
    # 不带 body（现有调用方）→ 默认 documents
    resp = await client.post("/api/rag/index")
    assert resp.status_code == 200
    assert resp.json()["namespace"] == "documents"
    assert resp.json()["status"] in ("ok", "empty", "error")

    # 带 namespace body
    resp = await client.post(
        "/api/rag/index",
        json={"namespace": "user_memory"},
    )
    assert resp.status_code == 200
    assert resp.json()["namespace"] == "user_memory"
