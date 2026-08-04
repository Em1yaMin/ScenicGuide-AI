"""
RAG 核心配置与向量存储。

向量化支持两种后端（由 app/core/embedding.py 的工厂选择）：
- Minimax Embedding API（配置 EMBEDDING_API_KEY 时启用）
- 轻量 TF-IDF（默认/降级路径，无外部依赖）

存储为命名空间隔离的文件持久化（无需外部向量库），
检索阶段用 numpy 矩阵乘法加速余弦相似度计算。
兼容 langchain_core 的 Document 和 Embeddings 接口。
"""

import fcntl
import json
import logging
import math
import os
import re
import threading
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.core.embedding import get_embedding_model

logger = logging.getLogger(__name__)

# 持久化目录
RAG_DATA_DIR: Path = Path(__file__).resolve().parents[2] / "rag_data"

# 默认文档目录
DEFAULT_DOC_DIR: Path = Path(__file__).resolve().parents[3] / "doc"

# 默认配置
DEFAULT_TOP_K: int = int(os.environ.get("RAG_TOP_K", "3"))
CHUNK_SIZE: int = int(os.environ.get("RAG_CHUNK_SIZE", "500"))
CHUNK_OVERLAP: int = int(os.environ.get("RAG_CHUNK_OVERLAP", "50"))


# ---------------------------------------------------------------------------
# 命名空间定义
# ---------------------------------------------------------------------------

# 命名空间：每个命名空间拥有独立向量索引与独立持久化文件，检索互不命中。
# - knowledge:   知识库条目（管理后台录入，预留索引接入）
# - documents:   doc/ 目录文档（现有全量索引）
# - user_memory: 用户长期记忆（预留，本阶段仅保留接口与持久化目录）
NAMESPACE_KNOWLEDGE = "knowledge"
NAMESPACE_DOCUMENTS = "documents"
NAMESPACE_USER_MEMORY = "user_memory"
ALL_NAMESPACES: Tuple[str, ...] = (
    NAMESPACE_KNOWLEDGE,
    NAMESPACE_DOCUMENTS,
    NAMESPACE_USER_MEMORY,
)
# 默认公共检索命名空间
DEFAULT_SEARCH_NAMESPACES: Tuple[str, ...] = (NAMESPACE_KNOWLEDGE, NAMESPACE_DOCUMENTS)

# 命名空间 → 持久化集合名（即 rag_data/ 下的文件前缀）。
# documents 复用旧集合名 "scenic_knowledge"，保证升级前已持久化的
# doc/ 文档数据（rag_data/scenic_knowledge_*.json）无需重建即可继续命中。
NAMESPACE_COLLECTIONS: dict = {
    NAMESPACE_KNOWLEDGE: "knowledge",
    NAMESPACE_DOCUMENTS: "scenic_knowledge",
    NAMESPACE_USER_MEMORY: "user_memory",
}


# ---------------------------------------------------------------------------
# 轻量 TF-IDF Embeddings
# ---------------------------------------------------------------------------

class TFIDFEmbeddings(Embeddings):
    """纯 Python 实现的 TF-IDF 向量化，兼容 LangChain Embeddings 接口。

    特点:
    - 无需 scikit-learn、sentence-transformers 等外部向量库依赖
    - 支持中文分词（基于字符 bigram + unigram）
    - 支持增量添加文档
    - 作为 Embedding API 不可用时的降级路径
    """

    backend_name: str = "tfidf"

    def __init__(self):
        self._vocabulary: dict = {}          # token → id
        self._idf: dict = {}                 # token → idf value
        self._doc_count: int = 0
        self._fitted: bool = False

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """中文友好的 tokenization: 字符 unigram + bigram。

        对中文文本做字符级 n-gram，同时保留英文/数字的单词边界。
        """
        # 清理文本
        text = text.lower().strip()
        tokens = []

        # 提取中文字符序列做 bigram
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        for ch in chinese_chars:
            tokens.append(ch)  # unigram
        for i in range(len(chinese_chars) - 1):
            tokens.append(chinese_chars[i] + chinese_chars[i + 1])  # bigram

        # 提取英文/数字单词
        words = re.findall(r'[a-z0-9]{2,}', text)
        tokens.extend(words)

        # 如果没有提取到任何 token，退化为字符级
        if not tokens:
            tokens = list(text)

        return tokens

    def _build_vocabulary(self, documents: List[Document]) -> None:
        """从文档集合构建词汇表并计算 IDF。"""
        self._vocabulary = {}
        self._idf = {}
        self._doc_count = len(documents)

        # 统计每个 token 在多少文档中出现
        doc_freq: Counter = Counter()

        for doc in documents:
            tokens = set(self._tokenize(doc.page_content))
            for token in tokens:
                doc_freq[token] += 1

        # 构建词汇表（按文档频率排序，取前 10000 个）
        sorted_tokens = sorted(doc_freq.items(), key=lambda x: -x[1])[:10000]
        for idx, (token, df) in enumerate(sorted_tokens):
            self._vocabulary[token] = idx
            # IDF = log((N + 1) / (df + 1)) + 1 (smooth)
            self._idf[token] = math.log((self._doc_count + 1) / (df + 1)) + 1

        self._fitted = True
        logger.info(
            "TF-IDF vocabulary built: %d tokens from %d documents",
            len(self._vocabulary),
            self._doc_count,
        )

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """对文档列表进行向量化。"""
        if not self._fitted:
            # 未拟合时使用 one-hot 风格简单向量
            return [self._simple_embed(t) for t in texts]
        return [self._tfidf_embed(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        """对查询文本进行向量化。"""
        if not self._fitted:
            return self._simple_embed(text)
        return self._tfidf_embed(text)

    def _tfidf_embed(self, text: str) -> List[float]:
        """计算 TF-IDF 向量。"""
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0] * max(1, len(self._vocabulary))

        # 计算 TF
        tf = Counter(tokens)
        max_tf = max(tf.values()) if tf else 1

        # 构建向量（稀疏表示为固定长度列表）
        vec_dim = len(self._vocabulary)
        vec = [0.0] * vec_dim

        for token, count in tf.items():
            if token in self._vocabulary:
                idx = self._vocabulary[token]
                tf_norm = count / max_tf
                idf_val = self._idf.get(token, 1.0)
                vec[idx] = tf_norm * idf_val

        return vec

    def _simple_embed(self, text: str) -> List[float]:
        """简单字符级向量（无词汇表时的回退方案）。"""
        tokens = self._tokenize(text)
        if not tokens:
            return [0.0]
        # 用字符 hash 生成固定维度向量
        dim = 256
        vec = [0.0] * dim
        for i, token in enumerate(tokens):
            h = hash(token) % dim
            vec[h] += 1.0
        # 归一化
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def fit(self, documents: List[Document]) -> None:
        """从文档集合训练 TF-IDF 词汇表。"""
        self._build_vocabulary(documents)

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def save(self, path: Path) -> None:
        """保存词汇表到文件。"""
        data = {
            "vocabulary": self._vocabulary,
            "idf": self._idf,
            "doc_count": self._doc_count,
            "fitted": self._fitted,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def load(self, path: Path) -> bool:
        """从文件加载词汇表。"""
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._vocabulary = data["vocabulary"]
            self._idf = data["idf"]
            self._doc_count = data["doc_count"]
            self._fitted = data["fitted"]
            logger.info(
                "TF-IDF vocabulary loaded: %d tokens",
                len(self._vocabulary),
            )
            return True
        except Exception as e:
            logger.warning("Failed to load TF-IDF vocabulary: %s", e)
            return False


# ---------------------------------------------------------------------------
# 轻量向量存储（替代 Chroma）
# ---------------------------------------------------------------------------

class SimpleVectorStore:
    """简单的文件持久化向量存储。

    使用 Embeddings 接口向量化（Minimax API 或本地 TF-IDF）+ 余弦相似度检索；
    检索时向量以 numpy 矩阵形态一次性做矩阵乘法（X @ q），
    避免逐向量 Python 循环，大幅提升检索速度。
    持久化到 JSON 文件，兼容离线环境。

    Embedding API 不可用（未配置 key / 网络失败）时自动降级为本地 TF-IDF：
    - 记录告警日志，并通过 `degraded` / `degraded_reason` / `embedding_backend`
      暴露可观测标记（检索接口会透出）。
    - 降级后向量化/检索继续可用，对话链路不中断。
    """

    def __init__(
        self,
        collection_name: str = "scenic_knowledge",
        persist_dir: Optional[Path] = None,
        embedding: Optional[Embeddings] = None,
    ):
        self.collection_name = collection_name
        self.persist_dir = Path(persist_dir) if persist_dir else RAG_DATA_DIR
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # 主 embedding（工厂按配置选择 Minimax API 或 TF-IDF）
        self.embedding: Embeddings = embedding or get_embedding_model()
        # 当前生效的 embedding（API 失败降级后指向 TF-IDF 备份）
        self._active_embedding: Embeddings = self.embedding
        # TF-IDF 降级备份（仅在主 embedding 为 API 且失败时惰性构建）
        self._tfidf_backup: Optional[TFIDFEmbeddings] = None
        # 降级标记（可观测）
        self.degraded: bool = False
        self.degraded_reason: str = ""

        self._documents: List[Document] = []
        self._vectors: List[List[float]] = []
        self._vector_matrix: Optional[np.ndarray] = None  # float32 矩阵缓存

        # 尝试加载已有数据
        self._load()

    @property
    def embedding_backend(self) -> str:
        """当前生效的 embedding 后端（minimax / tfidf），用于可观测标记。"""
        return getattr(self._active_embedding, "backend_name", "unknown")

    @property
    def _docs_path(self) -> Path:
        return self.persist_dir / f"{self.collection_name}_docs.json"

    @property
    def _vocab_path(self) -> Path:
        return self.persist_dir / f"{self.collection_name}_vocab.json"

    def _acquire_file_lock(self):
        """获取文件锁，防止多进程并发读写。"""
        lock_path = self.persist_dir / f"{self.collection_name}.lock"
        f = open(lock_path, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        return f

    @staticmethod
    def _release_file_lock(lock_file):
        """释放文件锁。"""
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()

    def _load(self) -> None:
        """从磁盘加载向量存储。"""
        lock = self._acquire_file_lock()
        try:
            # 本地 TF-IDF 时加载词汇表（API embedding 无词汇表概念）
            if isinstance(self.embedding, TFIDFEmbeddings):
                self.embedding.load(self._vocab_path)

            # 加载文档
            if self._docs_path.exists():
                try:
                    with open(self._docs_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self._documents = [
                        Document(
                            page_content=d["page_content"],
                            metadata=d.get("metadata", {}),
                        )
                        for d in data
                    ]
                    # 重新向量化（API embedding 失败时自动降级 TF-IDF）
                    if self._documents:
                        if (
                            isinstance(self.embedding, TFIDFEmbeddings)
                            and not self.embedding.is_fitted
                        ):
                            logger.warning(
                                "Documents loaded but vocabulary missing; "
                                "clearing documents to avoid silent inconsistency. "
                                "Run /index to rebuild."
                            )
                            self._documents = []
                            self._vectors = []
                        else:
                            texts = [d.page_content for d in self._documents]
                            self._vectors = self._embed_documents_safe(texts)
                            self._refresh_matrix()
                    logger.info(
                        "Loaded %d documents from %s",
                        len(self._documents),
                        self._docs_path,
                    )
                except Exception as e:
                    logger.warning("Failed to load documents: %s", e)
                    self._documents = []
                    self._vectors = []
        finally:
            self._release_file_lock(lock)

    def _save(self) -> None:
        """持久化到磁盘。"""
        lock = self._acquire_file_lock()
        try:
            # 保存词汇表（仅本地 TF-IDF 后端需要）
            if isinstance(self.embedding, TFIDFEmbeddings) and self.embedding.is_fitted:
                self.embedding.save(self._vocab_path)

            # 保存文档
            data = [
                {
                    "page_content": d.page_content,
                    "metadata": d.metadata,
                }
                for d in self._documents
            ]
            with open(self._docs_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        finally:
            self._release_file_lock(lock)

    def add_documents(self, documents: List[Document]) -> None:
        """添加文档到向量存储并持久化。"""
        if not documents:
            return

        # 拟合/更新 TF-IDF 词汇表（仅本地后端需要）
        all_docs = self._documents + documents
        self._documents = all_docs
        if isinstance(self.embedding, TFIDFEmbeddings):
            self.embedding.fit(all_docs)
        # 已降级到 TF-IDF 备份时，用全量文档重新拟合词汇表（包含新增文档）
        if self._tfidf_backup is not None:
            self._tfidf_backup.fit(all_docs)

        # 重新向量化全部文档（API 失败自动降级 TF-IDF）
        texts = [d.page_content for d in all_docs]
        self._vectors = self._embed_documents_safe(texts)
        self._refresh_matrix()

        self._save()
        logger.info(
            "Added %d documents, total: %d",
            len(documents),
            len(self._documents),
        )

    def similarity_search_with_score(
        self,
        query: str,
        k: int = DEFAULT_TOP_K,
    ) -> List[Tuple[Document, float]]:
        """检索与查询最相似的文档。

        Args:
            query: 查询文本
            k: 返回数量

        Returns:
            [(Document, score), ...] 按相似度降序排列
        """
        if not self._documents:
            return []

        query_vec = self._embed_query_safe(query)

        # 惰性重建矩阵（防止外部直接改动 _vectors 导致不一致）
        if self._vector_matrix is None or len(self._vector_matrix) != len(
            self._vectors
        ):
            self._refresh_matrix()

        X = self._vector_matrix
        q = np.asarray(query_vec, dtype=np.float32)

        # 一次矩阵乘法计算全部余弦相似度（替代逐向量 Python 循环）
        doc_norms = np.linalg.norm(X, axis=1)
        q_norm = np.linalg.norm(q)
        with np.errstate(divide="ignore", invalid="ignore"):
            sims = X @ q / (doc_norms * q_norm)
        # 零向量（norm=0）相似度置 0，与原实现（norm or 1.0）语义一致
        sims = np.nan_to_num(sims, nan=0.0, posinf=0.0, neginf=0.0)

        # 排序取 top-k，再过滤极低相似度（与原逻辑一致）。
        # 用稳定排序保持并列分数时按文档索引升序，与旧实现的
        # Python 稳定 sort 行为完全一致。
        top_indices = np.argsort(-sims, kind="stable")[:k]
        results = []
        for i in top_indices:
            score = float(sims[i])
            if score <= 0.01:
                continue
            results.append((self._documents[int(i)], score))
        return results

    def _refresh_matrix(self) -> None:
        """把稀疏列表形态的向量转为 float32 numpy 矩阵（节省内存）。"""
        if not self._vectors:
            self._vector_matrix = None
            return
        try:
            self._vector_matrix = np.asarray(self._vectors, dtype=np.float32)
        except ValueError:
            # 罕见：向量维度不一致时退化为补零对齐（与原实现语义一致）
            max_len = max(len(v) for v in self._vectors)
            padded = [v + [0.0] * (max_len - len(v)) for v in self._vectors]
            self._vector_matrix = np.asarray(padded, dtype=np.float32)

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        """计算两个向量的余弦相似度。"""
        if not a or not b:
            return 0.0

        # 对齐维度
        max_len = max(len(a), len(b))
        a_pad = a + [0.0] * (max_len - len(a))
        b_pad = b + [0.0] * (max_len - len(b))

        dot = sum(x * y for x, y in zip(a_pad, b_pad))
        norm_a = math.sqrt(sum(x * x for x in a_pad)) or 1.0
        norm_b = math.sqrt(sum(y * y for y in b_pad)) or 1.0

        return dot / (norm_a * norm_b)

    def count(self) -> int:
        """返回文档数量。"""
        return len(self._documents)

    def clear(self) -> None:
        """清空存储。"""
        lock = self._acquire_file_lock()
        try:
            self._documents = []
            self._vectors = []
            self._vector_matrix = None
            # 重置 embedding（重新走工厂，可能恢复为 API 后端）
            self.embedding = get_embedding_model()
            self._active_embedding = self.embedding
            self._tfidf_backup = None
            self.degraded = False
            self.degraded_reason = ""
            if self._docs_path.exists():
                self._docs_path.unlink()
            if self._vocab_path.exists():
                self._vocab_path.unlink()
        finally:
            self._release_file_lock(lock)

    # -- Embedding 降级支持 --

    def _embed_documents_safe(self, texts: List[str]) -> List[List[float]]:
        """向量化文档列表；API 失败时降级到本地 TF-IDF。"""
        try:
            return self._active_embedding.embed_documents(texts)
        except Exception as e:
            backup = self._activate_tfidf_fallback("文档向量化", e)
            return backup.embed_documents(texts)

    def _embed_query_safe(self, text: str) -> List[float]:
        """向量化查询；API 失败时降级到本地 TF-IDF。"""
        try:
            return self._active_embedding.embed_query(text)
        except Exception as e:
            backup = self._activate_tfidf_fallback("查询向量化", e)
            return backup.embed_query(text)

    def _activate_tfidf_fallback(self, operation: str, error: Exception) -> TFIDFEmbeddings:
        """激活 TF-IDF 降级路径（幂等）：惰性构建备份并标记降级。

        降级后立即用备份重新向量化已存文档，保证文档向量与查询向量
        同源同维度（API 向量与 TF-IDF 向量维度不同，混用会导致矩阵乘法失败）。
        """
        if self._tfidf_backup is None:
            self._tfidf_backup = TFIDFEmbeddings()
            if self._documents:
                self._tfidf_backup.fit(self._documents)
        self._active_embedding = self._tfidf_backup
        if self._documents:
            texts = [d.page_content for d in self._documents]
            self._vectors = self._tfidf_backup.embed_documents(texts)
            self._refresh_matrix()
        self.degraded = True
        self.degraded_reason = f"Embedding API 不可用（{operation}）: {error}"
        logger.warning("RAG 降级为本地 TF-IDF（%s）: %s", operation, error)
        return self._tfidf_backup


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_vectorstore: Optional[SimpleVectorStore] = None
_vectorstore_collection: Optional[str] = None
_vectorstore_dir: Optional[Path] = None
_vectorstore_lock = threading.Lock()


def get_vectorstore(
    collection_name: str = "scenic_knowledge",
    persist_dir: Optional[Path] = None,
) -> SimpleVectorStore:
    """获取全局向量存储单例。

    当 collection_name 或 persist_dir 变化时自动重建实例，
    以支持未来扩展多知识库场景。
    """
    global _vectorstore, _vectorstore_collection, _vectorstore_dir
    effective_dir = persist_dir or RAG_DATA_DIR
    if (
        _vectorstore is None
        or _vectorstore_collection != collection_name
        or _vectorstore_dir != effective_dir
    ):
        with _vectorstore_lock:
            if (
                _vectorstore is None
                or _vectorstore_collection != collection_name
                or _vectorstore_dir != effective_dir
            ):
                _vectorstore = SimpleVectorStore(
                    collection_name=collection_name,
                    persist_dir=effective_dir,
                )
                _vectorstore_collection = collection_name
                _vectorstore_dir = effective_dir
    return _vectorstore


def get_vectorstore_for_namespace(namespace: str = NAMESPACE_DOCUMENTS) -> SimpleVectorStore:
    """获取指定命名空间的向量存储单例（每个命名空间独立索引与持久化）。

    Args:
        namespace: 命名空间，可选 knowledge / documents / user_memory

    Raises:
        ValueError: 未知命名空间
    """
    if namespace not in NAMESPACE_COLLECTIONS:
        raise ValueError(
            f"未知 RAG 命名空间: {namespace!r}，可选: {', '.join(ALL_NAMESPACES)}"
        )
    return get_vectorstore(collection_name=NAMESPACE_COLLECTIONS[namespace])


def normalize_namespaces(namespace: Optional[str]) -> List[str]:
    """解析 namespace 参数（逗号分隔的字符串）。

    未传或为空时返回默认公共命名空间（knowledge + documents）；
    非法命名空间告警剔除；全部非法时回退默认值。
    """
    raw = namespace or ",".join(DEFAULT_SEARCH_NAMESPACES)
    namespaces: List[str] = []
    for item in raw.split(","):
        ns = item.strip()
        if ns in NAMESPACE_COLLECTIONS:
            namespaces.append(ns)
        else:
            logger.warning("忽略未知 RAG 命名空间: %r", ns)
    if not namespaces:
        logger.warning("namespace 参数无有效值（%r），回退默认命名空间", raw)
        namespaces = list(DEFAULT_SEARCH_NAMESPACES)
    return namespaces
