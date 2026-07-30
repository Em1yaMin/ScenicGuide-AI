"""
RAG 核心配置与向量存储。

使用纯 Python 实现的轻量 TF-IDF 向量化 + 文件持久化，
无需 chromadb、sentence-transformers 等外部依赖。
兼容 langchain_core 的 Document 和 Embeddings 接口。
"""

import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

# 持久化目录
CHROMA_DATA_DIR: Path = Path(__file__).resolve().parents[2] / "rag_data"

# 默认文档目录
DEFAULT_DOC_DIR: Path = Path(__file__).resolve().parents[3] / "doc"

# 默认配置
DEFAULT_TOP_K: int = int(os.environ.get("RAG_TOP_K", "3"))
CHUNK_SIZE: int = int(os.environ.get("RAG_CHUNK_SIZE", "500"))
CHUNK_OVERLAP: int = int(os.environ.get("RAG_CHUNK_OVERLAP", "50"))


# ---------------------------------------------------------------------------
# 轻量 TF-IDF Embeddings
# ---------------------------------------------------------------------------

class TFIDFEmbeddings(Embeddings):
    """纯 Python 实现的 TF-IDF 向量化，兼容 LangChain Embeddings 接口。

    特点:
    - 无需 numpy、scikit-learn、sentence-transformers 等外部依赖
    - 支持中文分词（基于字符 bigram + unigram）
    - 支持增量添加文档
    """

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

    使用 TF-IDF 向量 + 余弦相似度进行检索。
    持久化到 JSON 文件，兼容离线环境。
    """

    def __init__(
        self,
        collection_name: str = "scenic_knowledge",
        persist_dir: Optional[Path] = None,
    ):
        self.collection_name = collection_name
        self.persist_dir = persist_dir or CHROMA_DATA_DIR
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        self.embedding = TFIDFEmbeddings()
        self._documents: List[Document] = []
        self._vectors: List[List[float]] = []

        # 尝试加载已有数据
        self._load()

    @property
    def _docs_path(self) -> Path:
        return self.persist_dir / f"{self.collection_name}_docs.json"

    @property
    def _vocab_path(self) -> Path:
        return self.persist_dir / f"{self.collection_name}_vocab.json"

    def _load(self) -> None:
        """从磁盘加载向量存储。"""
        # 加载词汇表
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
                # 重新向量化
                if self._documents and self.embedding.is_fitted:
                    texts = [d.page_content for d in self._documents]
                    self._vectors = self.embedding.embed_documents(texts)
                elif self._documents and not self.embedding.is_fitted:
                    logger.warning(
                        "Documents loaded but vocabulary missing; "
                        "clearing documents to avoid silent inconsistency. "
                        "Run /index to rebuild."
                    )
                    self._documents = []
                    self._vectors = []
                logger.info(
                    "Loaded %d documents from %s",
                    len(self._documents),
                    self._docs_path,
                )
            except Exception as e:
                logger.warning("Failed to load documents: %s", e)
                self._documents = []
                self._vectors = []

    def _save(self) -> None:
        """持久化到磁盘。"""
        # 保存词汇表
        if self.embedding.is_fitted:
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

    def add_documents(self, documents: List[Document]) -> None:
        """添加文档到向量存储并持久化。"""
        if not documents:
            return

        # 拟合/更新 TF-IDF
        all_docs = self._documents + documents
        self.embedding.fit(all_docs)

        # 重新向量化全部文档
        texts = [d.page_content for d in all_docs]
        self._vectors = self.embedding.embed_documents(texts)
        self._documents = all_docs

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

        query_vec = self.embedding.embed_query(query)

        # 计算余弦相似度
        scores = []
        for i, doc_vec in enumerate(self._vectors):
            sim = self._cosine_similarity(query_vec, doc_vec)
            scores.append((i, sim))

        # 排序取 top-k
        scores.sort(key=lambda x: -x[1])
        top_k = scores[:k]

        return [
            (self._documents[i], score)
            for i, score in top_k
            if score > 0.01  # 过滤极低相似度
        ]

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
        self._documents = []
        self._vectors = []
        self.embedding = TFIDFEmbeddings()
        if self._docs_path.exists():
            self._docs_path.unlink()
        if self._vocab_path.exists():
            self._vocab_path.unlink()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_vectorstore: Optional[SimpleVectorStore] = None
_vectorstore_collection: Optional[str] = None
_vectorstore_dir: Optional[Path] = None


def get_vectorstore(
    collection_name: str = "scenic_knowledge",
    persist_dir: Optional[Path] = None,
) -> SimpleVectorStore:
    """获取全局向量存储单例。

    当 collection_name 或 persist_dir 变化时自动重建实例，
    以支持未来扩展多知识库场景。
    """
    global _vectorstore, _vectorstore_collection, _vectorstore_dir
    effective_dir = persist_dir or CHROMA_DATA_DIR
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


def get_embedding_model() -> TFIDFEmbeddings:
    """获取当前向量存储的 embedding 模型。"""
    return get_vectorstore().embedding
