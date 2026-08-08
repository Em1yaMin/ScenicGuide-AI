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
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
from collections import Counter
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.core.embedding import MinimaxEmbeddings, get_embedding_model

logger = logging.getLogger(__name__)

# 持久化目录
RAG_DATA_DIR: Path = Path(__file__).resolve().parents[2] / "rag_data"

# 默认文档目录：仅限定到 doc/景区知识库/（三份景区知识 md），
# 避免把 doc/ 下的架构/设计/部署文档混入 RAG 索引。
DEFAULT_DOC_DIR: Path = Path(__file__).resolve().parents[3] / "doc" / "景区知识库"

# 默认配置
DEFAULT_TOP_K: int = int(os.environ.get("RAG_TOP_K", "3"))
CHUNK_SIZE: int = int(os.environ.get("RAG_CHUNK_SIZE", "500"))
CHUNK_OVERLAP: int = int(os.environ.get("RAG_CHUNK_OVERLAP", "50"))


# ---------------------------------------------------------------------------
# 命名空间定义
# ---------------------------------------------------------------------------

# 命名空间：每个命名空间拥有独立向量索引与独立持久化文件，检索互不命中。
# - knowledge:   知识库条目（管理后台录入，CRUD/导入变更后同步重建索引）
# - documents:   doc/景区知识库/ 文档（全量索引）
# - user_memory: 用户长期记忆（阶段2 T2 已接入写入端：对话后提取用户事实/偏好，
#                按 user 隔离，见 app/services/memory_service.py）
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
# doc/景区知识库/ 文档数据（rag_data/scenic_knowledge_*.json）无需重建即可继续命中。
NAMESPACE_COLLECTIONS: dict = {
    NAMESPACE_KNOWLEDGE: "knowledge",
    NAMESPACE_DOCUMENTS: "scenic_knowledge",
    NAMESPACE_USER_MEMORY: "user_memory",
}


# ---------------------------------------------------------------------------
# 原子 JSON 写入（C4：向量落盘基础设施）
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data, indent: Optional[int] = 2) -> None:
    """临时文件 + 原子替换写 JSON（防中途崩溃损坏持久化文件）。

    先写同目录临时文件并 fsync，再 os.replace 原子替换；任一异常时
    清理临时文件不残留。indent=None 时紧凑输出（向量 sidecar 数据量大，
    缩进会放大文件体积近 5 倍）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if indent is None:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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
        """保存词汇表到文件（临时文件+原子替换，防中途崩溃损坏）。"""
        data = {
            "vocabulary": self._vocabulary,
            "idf": self._idf,
            "doc_count": self._doc_count,
            "fitted": self._fitted,
        }
        _atomic_write_json(path, data)

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
    C4（批次3）：文档文件旁新增 `{collection}_vectors.json` sidecar 落盘向量 +
    embedding 绑定标识 + 文档摘要，配置一致时启动复用缓存跳过 embedding API
    重算（详见 _load/_try_reuse_cached_vectors）。

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

    @property
    def _vectors_path(self) -> Path:
        """向量 sidecar 文件（C4：向量落盘，{collection}_vectors.json）。"""
        return self.persist_dir / f"{self.collection_name}_vectors.json"

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
        """从磁盘加载向量存储。

        C4（批次3）向量落盘：docs 文件旁新增 `{collection}_vectors.json` sidecar，
        持久化向量 + embedding 绑定标识 + 文档摘要。启动时若缓存有效
        （配置一致、条数/维度齐整、文档摘要匹配）则直接复用，跳过
        embedding API 全量重算（3769 片段 ≈ 2 分钟，且 Qwen 按量计费）。
        旧格式（无 sidecar）自动回退重算并升级写入；embedding 配置变更
        （模型/请求体风格/端点）自动失效重建；TF-IDF 路径行为不变
        （词汇表机制，重算成本可忽略，不写 sidecar）。
        """
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
                    # 优先复用 sidecar 落盘向量（缓存命中则跳过 embedding API 重算）
                    if self._documents and self._try_reuse_cached_vectors():
                        pass
                    else:
                        # 缓存缺失/失效：回退重算（API 失败自动降级 TF-IDF）
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
                            # 升级写入：旧格式升级 / 配置变更重建均在此落盘新缓存；
                            # 写失败不影响已加载的内存态（下次启动回退重算）
                            try:
                                self._save_locked()
                            except Exception as e:
                                logger.warning(
                                    "Failed to persist vector cache: %s", e
                                )
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

    def _try_reuse_cached_vectors(self) -> bool:
        """尝试复用 sidecar 落盘向量；成功返回 True 并填充 _vectors/_vector_matrix。

        校验顺序（任一不满足即判定缓存失效，回退重算）：
        1. 向量条数与文档数一致（文档增删后旧缓存条数不符）；
        2. 向量维度齐整（损坏/手改文件防御）；
        3. 文档摘要一致（docs 与 sidecar 两次原子写之间的崩溃窗口防御）；
        4. embedding 绑定标识一致（配置变更如 minimax→qwen 必须失效重建，
           维度不同复用会静默检索错乱）；
        5. TF-IDF 词汇表已拟合（查询向量与文档向量须同源同维度）。
        """
        if not self._vectors_path.exists():
            return False
        try:
            with open(self._vectors_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(
                "Failed to read vector cache %s: %s", self._vectors_path, e
            )
            return False
        cached = data.get("vectors")
        if not isinstance(cached, list) or len(cached) != len(self._documents):
            logger.info(
                "Vector cache invalid for %s: 条数不符，回退重算",
                self.collection_name,
            )
            return False
        if not self._vectors_are_homogeneous(cached):
            logger.info(
                "Vector cache invalid for %s: 向量维度不齐整，回退重算",
                self.collection_name,
            )
            return False
        if data.get("docs_digest") != self._docs_digest(self._documents):
            logger.info(
                "Vector cache stale for %s: 文档摘要不匹配，回退重算",
                self.collection_name,
            )
            return False
        cached_binding = data.get("embedding")
        if cached_binding != self._embedding_identity():
            logger.info(
                "Vector cache invalid for %s: embedding 配置变更"
                "（%s → %s），回退重建",
                self.collection_name,
                cached_binding,
                self._embedding_identity(),
            )
            return False
        if (
            isinstance(self.embedding, TFIDFEmbeddings)
            and not self.embedding.is_fitted
        ):
            logger.info(
                "Vector cache invalid for %s: TF-IDF 词汇表未拟合，回退重算",
                self.collection_name,
            )
            return False
        self._vectors = cached
        self._refresh_matrix()
        logger.info(
            "Vector cache hit for %s: 复用 %d 条向量"
            "（binding=%s，跳过 embedding API 重算）",
            self.collection_name,
            len(cached),
            json.dumps(cached_binding, ensure_ascii=False),
        )
        return True

    def _save(self) -> None:
        """持久化到磁盘（加文件锁）。"""
        lock = self._acquire_file_lock()
        try:
            self._save_locked()
        finally:
            self._release_file_lock(lock)

    def _save_locked(self) -> None:
        """持久化（调用方须已持有文件锁）。

        C4 起文档文件旁同步写入 `{collection}_vectors.json` sidecar：
        向量 + embedding 绑定标识 + 文档摘要，供下次启动复用缓存跳过
        embedding API 重算（详见 _load/_try_reuse_cached_vectors）。
        TF-IDF 生效时（主 TF-IDF 或 API 降级备份）不写 sidecar（词汇表机制，
        重算成本可忽略，行为不变；历史 API sidecar 亦不删除——文档未变时
        API 恢复后仍可复用）。
        """
        # 保存词汇表（仅本地 TF-IDF 后端需要）
        if isinstance(self.embedding, TFIDFEmbeddings) and self.embedding.is_fitted:
            self.embedding.save(self._vocab_path)

        # 保存文档（保持既有数组格式，向后兼容旧版读取）
        data = [
            {
                "page_content": d.page_content,
                "metadata": d.metadata,
            }
            for d in self._documents
        ]
        _atomic_write_json(self._docs_path, data)

        if isinstance(self._active_embedding, TFIDFEmbeddings):
            return
        if self._documents:
            sidecar = {
                "version": 2,
                "embedding": self._embedding_identity(),
                "docs_digest": self._docs_digest(self._documents),
                "dimension": len(self._vectors[0]) if self._vectors else 0,
                "vectors": self._vectors,
            }
            # 向量数据量大，紧凑输出（indent=None）避免文件体积膨胀近 5 倍
            _atomic_write_json(self._vectors_path, sidecar, indent=None)
        elif self._vectors_path.exists():
            # 文档清空后删除旧 sidecar，避免残留过期缓存
            self._vectors_path.unlink()

    def _embedding_identity(self) -> dict:
        """当前生效 embedding 的绑定标识（sidecar 缓存向量须与其一致才可复用）。

        - API embedding（MinimaxEmbeddings）：绑定后端/模型/请求体风格/端点。
          任一变更（minimax→qwen 切换、EMBEDDING_API_STYLE 变更、换端点）都会
          改变向量空间，复用旧向量将静默检索错乱，必须自动失效重建。
        - 其他 embedding（自定义/降级 TF-IDF）：仅绑定后端名。
        """
        emb = self._active_embedding
        identity = {"backend": getattr(emb, "backend_name", "unknown")}
        if isinstance(emb, MinimaxEmbeddings):
            identity.update(
                model=emb.model,
                api_style=emb.api_style,
                api_base=emb.api_base,
            )
        return identity

    @staticmethod
    def _docs_digest(documents: List[Document]) -> str:
        """文档内容摘要（顺序敏感序列化 + SHA-256），sidecar 与 docs 一致性校验。

        文档顺序影响向量对齐，摘要须覆盖顺序；metadata 键序经 sort_keys
        归一，保证写入与加载两端摘要一致。
        """
        payload = [
            {"page_content": d.page_content, "metadata": d.metadata}
            for d in documents
        ]
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _vectors_are_homogeneous(vectors: List[List[float]]) -> bool:
        """校验缓存向量为齐整的数值列表（非空、维度一致、元素为数值）。"""
        dim: Optional[int] = None
        for v in vectors:
            if not isinstance(v, list) or not v:
                return False
            if not all(isinstance(x, (int, float)) for x in v):
                return False
            if dim is None:
                dim = len(v)
            elif len(v) != dim:
                return False
        return True

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

    def delete_documents(self, predicate) -> int:
        """删除满足谓词的文档（按位置索引同步删除向量，避免全量重新向量化）。

        用于按 user 隔离的用户长期记忆的覆盖更新：先删旧条目再写入新条目。

        Args:
            predicate: Callable[[Document], bool]，返回 True 的文档将被删除

        Returns:
            实际删除的文档数（无匹配时返回 0，不触发持久化）
        """
        keep_docs: List[Document] = []
        keep_vectors: List[List[float]] = []
        removed = 0
        for doc, vec in zip(self._documents, self._vectors):
            if predicate(doc):
                removed += 1
            else:
                keep_docs.append(doc)
                keep_vectors.append(vec)
        if not removed:
            return 0
        self._documents = keep_docs
        if len(keep_vectors) == len(keep_docs):
            self._vectors = keep_vectors
        else:
            # 防御：向量与文档数量不一致（理论上不发生），按剩余文档重新向量化
            self._vectors = self._embed_documents_safe(
                [d.page_content for d in keep_docs]
            )
        self._refresh_matrix()
        self._save()
        logger.info(
            "Deleted %d documents from %s, remaining: %d",
            removed,
            self.collection_name,
            len(self._documents),
        )
        return removed

    def similarity_search_with_score_filtered(
        self,
        query: str,
        k: int = DEFAULT_TOP_K,
        predicate=None,
    ) -> List[Tuple[Document, float]]:
        """在满足谓词的文档子集内检索（如按 user 隔离的用户长期记忆）。

        与 similarity_search_with_score 的排序/过滤语义一致，但仅对
        predicate 返回 True 的文档计算相似度（子集规模通常很小，
        逐向量循环即可，无需构建临时矩阵）。

        Args:
            query: 查询文本
            k: 返回数量
            predicate: Callable[[Document], bool]，None 时检索全部文档

        Returns:
            [(Document, score), ...] 按相似度降序排列
        """
        if not self._documents:
            return []
        query_vec = self._embed_query_safe(query)
        results: List[Tuple[Document, float]] = []
        for doc, vec in zip(self._documents, self._vectors):
            if predicate is not None and not predicate(doc):
                continue
            sim = self._cosine_similarity(query_vec, vec)
            if sim <= 0.01:
                continue
            results.append((doc, sim))
        results.sort(key=lambda x: -x[1])
        return results[:k]

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
            if self._vectors_path.exists():
                self._vectors_path.unlink()
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

# 全局向量存储缓存：以 (collection_name, persist_dir) 为键，
# 每个命名空间每进程只构建一次，避免跨命名空间检索时反复重建与重新向量化。
_vectorstores: dict = {}
_vectorstore_lock = threading.Lock()


def get_vectorstore(
    collection_name: str = "scenic_knowledge",
    persist_dir: Optional[Path] = None,
) -> SimpleVectorStore:
    """获取全局向量存储单例（按 collection_name + persist_dir 缓存）。"""
    effective_dir = str(Path(persist_dir)) if persist_dir else str(RAG_DATA_DIR)
    key = (collection_name, effective_dir)
    store = _vectorstores.get(key)
    if store is None:
        with _vectorstore_lock:
            store = _vectorstores.get(key)
            if store is None:
                store = SimpleVectorStore(
                    collection_name=collection_name,
                    persist_dir=Path(effective_dir),
                )
                _vectorstores[key] = store
    return store


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
            if ns not in namespaces:
                namespaces.append(ns)
        else:
            logger.warning("忽略未知 RAG 命名空间: %r", ns)
    if not namespaces:
        logger.warning("namespace 参数无有效值（%r），回退默认命名空间", raw)
        namespaces = list(DEFAULT_SEARCH_NAMESPACES)
    return namespaces
