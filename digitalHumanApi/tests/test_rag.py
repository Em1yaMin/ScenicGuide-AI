"""RAG 向量存储回归测试。

覆盖：
1. numpy 矩阵化检索结果与旧逐向量实现（Python 稳定排序）完全一致，
   包括分数并列时的文档顺序（回归：np.argsort 默认不稳定导致并列时
   返回的文档与旧实现不同）。
2. 检索性能：2000 文档亚秒级（宽松阈值，防 CI 抖动）。
3. 持久化重载后检索结果一致。
4. C4（批次3）向量落盘：sidecar 缓存命中跳过 embedding API 重算、
   配置变更自动失效重建、旧格式升级写入、原子写安全、TF-IDF 路径不变。
"""
import json
import math
import time

import httpx
import pytest

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.core.embedding import MinimaxEmbeddings
from app.core.rag import SimpleVectorStore, TFIDFEmbeddings

TOPICS = [
    ("黄果树瀑布", "瀑布", "位于贵州省安顺市，是亚洲最大的瀑布，水势浩大"),
    ("荔波小七孔", "喀斯特", "世界自然遗产地，集洞、林、湖、瀑、石、水多种景观"),
    ("西江千户苗寨", "苗族", "中国最大苗族聚居村寨，吊脚楼依山而建"),
    ("梵净山", "山峰", "武陵山脉主峰，红云金顶，黔金丝猴栖息地"),
    ("青岩古镇", "古镇", "贵州四大古镇之一，明清建筑风格"),
    ("龙宫", "溶洞", "水旱溶洞群，全国最长的水溶洞"),
    ("万峰林", "峰林", "喀斯特峰林地貌，八卦田"),
    ("织金洞", "溶洞", "溶洞之王，钟乳石形态各异"),
    ("百里杜鹃", "花海", "绵延五十余公里的天然杜鹃花林带"),
    ("镇远古城", "古城", "舞阳河穿城而过，青龙洞古建筑群"),
]


def _make_docs(count: int) -> list[Document]:
    docs = []
    for i in range(count):
        topic, kind, desc = TOPICS[i % len(TOPICS)]
        docs.append(
            Document(
                page_content=(
                    f"第{i}篇：{topic}景区介绍。{topic}是{kind}类景观，{desc}。"
                    "游览建议：春季最佳，门票价格随季节浮动，周边有民宿和餐饮设施。"
                    "交通方式：高铁至贵阳后转乘旅游大巴。"
                ),
                metadata={"title": f"{topic}-{i}", "category": kind},
            )
        )
    return docs


def _old_search(store, query: str, k: int = 5):
    """旧实现：逐向量 Python 循环余弦相似度 + 稳定排序（基准参照）。"""
    query_vec = store.embedding.embed_query(query)
    scores = []
    for i, doc_vec in enumerate(store._vectors):
        sim = SimpleVectorStore._cosine_similarity(query_vec, doc_vec)
        scores.append((i, sim))
    scores.sort(key=lambda x: -x[1])
    return [
        (store._documents[i].page_content, round(sim, 6))
        for i, sim in scores[:k]
        if sim > 0.01
    ]


def test_numpy_search_matches_old_implementation(tmp_path):
    """numpy 矩阵化检索结果与旧逐向量实现完全一致（含并列分数场景）。"""
    store = SimpleVectorStore(collection_name="regress", persist_dir=tmp_path)
    store.add_documents(_make_docs(150))

    queries = [
        "黄果树瀑布门票多少钱，怎么去",
        "西江千户苗寨住宿推荐",
        "溶洞景观有哪些",
        "完全不相关的英文内容 qwerty asdfgh",
    ]
    for query in queries:
        new_results = store.similarity_search_with_score(query, k=5)
        new_normalized = [
            (d.page_content, round(s, 6)) for d, s in new_results
        ]
        assert new_normalized == _old_search(store, query), (
            f"query={query!r}: numpy 检索结果与旧实现不一致: {new_normalized}"
        )


def test_numpy_search_speed_sub_second(tmp_path):
    """2000 文档规模下检索亚秒级（旧实现约 2.4s，此处宽松断言防 CI 抖动）。"""
    store = SimpleVectorStore(collection_name="speed", persist_dir=tmp_path)
    store.add_documents(_make_docs(2000))

    # 预热（首次检索含矩阵构建）
    store.similarity_search_with_score("黄果树瀑布", k=5)

    start = time.perf_counter()
    for _ in range(5):
        store.similarity_search_with_score("黄果树瀑布门票多少钱，怎么去", k=5)
    elapsed = (time.perf_counter() - start) / 5
    assert elapsed < 1.0, f"2000 文档检索耗时 {elapsed:.3f}s，未达亚秒级目标"


def test_persist_reload_keeps_results(tmp_path):
    """持久化后重新加载，检索结果保持一致。"""
    store = SimpleVectorStore(collection_name="reload", persist_dir=tmp_path)
    store.add_documents(_make_docs(50))
    before = [
        (d.page_content, round(s, 6))
        for d, s in store.similarity_search_with_score("织金洞门票", k=3)
    ]

    store2 = SimpleVectorStore(collection_name="reload", persist_dir=tmp_path)
    after = [
        (d.page_content, round(s, 6))
        for d, s in store2.similarity_search_with_score("织金洞门票", k=3)
    ]
    assert before == after
    assert store2.count() == 50


def test_tfidf_embedding_dimension_and_zero_guard():
    """向量维度与词汇表一致；零向量文档不导致 NaN 分数。"""
    emb = TFIDFEmbeddings()
    docs = [Document(page_content="贵州黄果树瀑布 喀斯特地貌") for _ in range(3)]
    emb.fit(docs)
    vecs = emb.embed_documents([d.page_content for d in docs])
    assert all(len(v) == len(emb._vocabulary) for v in vecs)

    # 零向量（无 token 命中）时相似度应为 0 而非 NaN
    vec = emb.embed_query("")
    assert math.isfinite(sum(vec))


def test_tfidf_vocabulary_tie_break_is_lexicographic():
    """词汇表同频 token 按字典序 tie-break，构建确定性（RAG 评测门禁 flaky 回归）。

    旧实现仅按 df 降序稳定排序，同频 token 的相对顺序依赖 Counter 插入顺序
    （= 各文档 set() 迭代顺序，受 PYTHONHASHSEED 随机化影响）；词汇表截断边界
    （df==1 token）的取舍随之变化，检索排名偶发翻转。修复后同 df token 必须
    按字典序排列，与 hash seed/文档顺序完全解耦。
    """
    emb = TFIDFEmbeddings()
    # 同 df 组内故意乱序插入：aaa/bbb/ccc 各出现于 2 篇文档（df=2），
    # dd/ee 各出现于 1 篇文档（df=1）——两组的相对顺序与组内顺序均须确定
    docs = [
        Document(page_content="词 aaa bbb"),
        Document(page_content="词 aaa bbb"),
        Document(page_content="词 ccc dd"),
        Document(page_content="词 ccc ee"),
    ]
    emb.fit(docs)
    ids = emb._vocabulary
    assert ids["aaa"] < ids["bbb"] < ids["ccc"] < ids["dd"] < ids["ee"]


def test_tfidf_vocabulary_independent_of_doc_order():
    """词汇表构建与文档顺序无关（RAG 评测门禁 flaky 回归之二）。

    文档顺序影响 Counter 插入顺序 → 旧实现同频 token tie-break 随之变化；
    _load_documents 的 rglob 顺序跨文件系统不保证一致。修复后任意顺序
    fit 的词汇表必须完全相同。
    """
    texts = [
        "文档甲 灵山胜境 大佛 梵宫 九龙灌浴 佛足坛 菩提大道",
        "文档乙 灵山胜境 禅意 拈花湾 禅行 曼飞龙塔 五印坛城",
        "文档丙 灵山胜境 大佛 梵宫 祥符禅寺 百子戏弥勒 五智门",
        "文档丁 灵山胜境 禅意 拈花湾 灵山小镇 拈花塔 微笑广场",
        "文档戊 灵山胜境 大佛 梵宫 九龙灌浴 阿育王柱 天下第一掌",
        "文档己 灵山胜境 禅意 拈花湾 禅行 半山衔日 香月花街",
    ]
    docs = [Document(page_content=t) for t in texts]
    emb_a = TFIDFEmbeddings()
    emb_a.fit(docs)
    emb_b = TFIDFEmbeddings()
    emb_b.fit(list(reversed(docs)))
    assert emb_a._vocabulary == emb_b._vocabulary
    assert emb_a._idf == emb_b._idf


def test_delete_documents_and_filtered_search(tmp_path):
    """delete_documents 按谓词删除（向量同步清除、持久化一致）；
    similarity_search_with_score_filtered 仅在该谓词子集内检索。

    支撑用户长期记忆的覆盖更新与按 user 隔离检索。
    """
    store = SimpleVectorStore(collection_name="mem", persist_dir=tmp_path)
    store.add_documents(
        [
            Document(page_content="喜欢拍照", metadata={"user": "u1"}),
            Document(page_content="带老人小孩", metadata={"user": "u1"}),
            Document(page_content="偏好半日游", metadata={"user": "u2"}),
        ]
    )
    assert store.count() == 3

    # 过滤子集检索：u1 只能命中 u1 的记忆
    hits = store.similarity_search_with_score_filtered(
        "拍照", k=5, predicate=lambda d: d.metadata.get("user") == "u1"
    )
    assert all(d.metadata.get("user") == "u1" for d, _ in hits)
    assert any("拍照" in d.page_content for d, _ in hits)
    # 无谓词时等同全量检索（numpy 矩阵与逐向量实现存在浮点末位差异，按内容+取整分数比较）
    filtered = store.similarity_search_with_score_filtered(
        "拍照", k=5, predicate=None
    )
    full = store.similarity_search_with_score("拍照", k=5)
    assert [d.page_content for d, _ in filtered] == [d.page_content for d, _ in full]
    assert [round(s, 4) for _, s in filtered] == [round(s, 4) for _, s in full]

    # 删除 u1 的「喜欢拍照」（谓词按 user + 内容匹配）
    removed = store.delete_documents(
        lambda d: d.metadata.get("user") == "u1" and "拍照" in d.page_content
    )
    assert removed == 1
    assert store.count() == 2

    # 无匹配谓词：返回 0 且不改变存储
    assert store.delete_documents(lambda d: False) == 0
    assert store.count() == 2

    # 持久化重载后删除结果保持一致
    store2 = SimpleVectorStore(collection_name="mem", persist_dir=tmp_path)
    assert store2.count() == 2
    assert all("拍照" not in d.page_content for d in store2._documents)


# ---------------------------------------------------------------------------
# C4：向量落盘——启动免重算
# ---------------------------------------------------------------------------

class _CountingEmbeddings(Embeddings):
    """确定性 5 维向量 + embed_documents/embed_query 调用计数。"""

    backend_name = "counting"

    def __init__(self):
        self.embed_documents_calls = 0
        self.embed_query_calls = 0

    def _vec(self, text: str) -> list:
        chars = [float(ord(c) % 7 + 1) / 10.0 for c in text[:5]]
        return chars + [0.0] * (5 - len(chars))

    def embed_documents(self, texts):
        self.embed_documents_calls += 1
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        self.embed_query_calls += 1
        return self._vec(text)


def _counting_transport(calls: dict):
    """构造可计数的 Minimax 风格 MockTransport（固定 3 维向量）。

    MinimaxEmbeddings 解析响应时 vectors 数组优先，openai 兼容风格
    的 data 数组同样兼容（handler 同时读取 texts/input 两个字段）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] = calls.get("count", 0) + 1
        body = json.loads(request.content)
        texts = body.get("texts") or body.get("input") or []
        return httpx.Response(200, json={
            "vectors": [
                [float(i + 1) / 10, 0.2, 0.3]
                for i in range(len(texts))
            ],
            "model": body.get("model"),
        })

    return httpx.MockTransport(handler)


def _make_minimax_emb(model: str = "embo-01", calls: dict = None):
    """构造 MinimaxEmbeddings（MockTransport，可共享 calls 计数）。"""
    calls = calls if calls is not None else {}
    return MinimaxEmbeddings(
        api_key="test-key",
        api_base="https://api.minimaxi.com/v1",
        model=model,
        api_style="minimax",
        _transport=_counting_transport(calls),
    )


def test_vector_cache_hit_skips_embedding_api(tmp_path):
    """C4 验收：启动加载命中缓存时零 embedding API 调用（MockTransport 计数）。"""
    calls: dict = {}
    store = SimpleVectorStore(
        collection_name="cache_hit",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(calls=calls),
    )
    store.add_documents(_make_docs(20))
    assert calls["count"] > 0, "首次索引必须调用 embedding API"
    before = [
        (d.page_content, round(s, 6))
        for d, s in store.similarity_search_with_score("织金洞门票", k=3)
    ]

    # 同配置重启：加载命中缓存，不得产生任何 embedding API 调用
    calls["count"] = 0
    store2 = SimpleVectorStore(
        collection_name="cache_hit",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(calls=calls),
    )
    assert calls["count"] == 0, "缓存命中时不得调用 embedding API"
    assert store2.count() == 20

    after = [
        (d.page_content, round(s, 6))
        for d, s in store2.similarity_search_with_score("织金洞门票", k=3)
    ]
    assert before == after


def test_vector_cache_hit_skips_embed_documents_direct(tmp_path):
    """直接断言：缓存命中时 embed_documents 完全未被调用（加载阶段）。"""
    emb1 = _CountingEmbeddings()
    store = SimpleVectorStore(
        collection_name="direct_count", persist_dir=tmp_path, embedding=emb1
    )
    store.add_documents(_make_docs(10))
    assert emb1.embed_documents_calls > 0

    emb2 = _CountingEmbeddings()
    store2 = SimpleVectorStore(
        collection_name="direct_count", persist_dir=tmp_path, embedding=emb2
    )
    assert emb2.embed_documents_calls == 0
    assert emb2.embed_query_calls == 0
    assert store2.count() == 10

    before = [
        (d.page_content, round(s, 6))
        for d, s in store.similarity_search_with_score("西江千户苗寨", k=3)
    ]
    after = [
        (d.page_content, round(s, 6))
        for d, s in store2.similarity_search_with_score("西江千户苗寨", k=3)
    ]
    assert before == after


def test_vector_cache_invalidated_on_embedding_config_change(tmp_path):
    """embedding 配置变更（模型/风格/端点）→ 缓存自动失效重建并更新绑定。"""
    store1 = SimpleVectorStore(
        collection_name="cfg_change",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(model="embo-01"),
    )
    store1.add_documents(_make_docs(10))

    # minimax→qwen 切换（模型/请求体风格/端点均变）：必须重新向量化
    calls2: dict = {}
    emb2 = MinimaxEmbeddings(
        api_key="test-key",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3.7-text-embedding",
        api_style="openai",
        _transport=_counting_transport(calls2),
    )
    store2 = SimpleVectorStore(
        collection_name="cfg_change", persist_dir=tmp_path, embedding=emb2
    )
    assert calls2["count"] > 0, "配置变更后必须重新向量化"
    assert store2.count() == 10
    # 可观测标记按实际后端展示（openai 风格 = Qwen/DashScope）
    assert store2.embedding_backend == "qwen-dashscope"

    # sidecar 绑定已更新为新配置
    with open(tmp_path / "cfg_change_vectors.json", encoding="utf-8") as f:
        sidecar = json.load(f)
    assert sidecar["embedding"]["model"] == "qwen3.7-text-embedding"
    assert sidecar["embedding"]["api_style"] == "openai"
    assert sidecar["embedding"]["api_base"].startswith("https://dashscope")

    # 新配置再次启动 → 命中缓存（零 API 调用）
    calls2["count"] = 0
    store3 = SimpleVectorStore(
        collection_name="cfg_change",
        persist_dir=tmp_path,
        embedding=MinimaxEmbeddings(
            api_key="test-key",
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            model="qwen3.7-text-embedding",
            api_style="openai",
            _transport=_counting_transport(calls2),
        ),
    )
    assert calls2["count"] == 0
    assert store3.count() == 10


def test_vector_cache_invalidated_on_backend_change(tmp_path):
    """后端变更（如 API 切换为另一自定义 embedding）→ 缓存失效重建。"""
    emb1 = _CountingEmbeddings()
    store = SimpleVectorStore(
        collection_name="backend_change", persist_dir=tmp_path, embedding=emb1
    )
    store.add_documents(_make_docs(8))

    class _OtherCounting(_CountingEmbeddings):
        backend_name = "counting-other"

    emb2 = _OtherCounting()
    store2 = SimpleVectorStore(
        collection_name="backend_change", persist_dir=tmp_path, embedding=emb2
    )
    assert emb2.embed_documents_calls > 0, "后端变更必须重新向量化"
    assert store2.count() == 8


def test_old_format_docs_upgrade_writes_vectors(tmp_path):
    """旧格式（无向量无绑定信息）自动回退重算并升级写入 sidecar。"""
    docs = _make_docs(5)
    old_file = [
        {"page_content": d.page_content, "metadata": d.metadata}
        for d in docs
    ]
    (tmp_path / "oldfmt_docs.json").write_text(
        json.dumps(old_file, ensure_ascii=False), encoding="utf-8"
    )

    calls: dict = {}
    store = SimpleVectorStore(
        collection_name="oldfmt",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(calls=calls),
    )
    assert calls.get("count", 0) > 0, "旧格式无缓存，须回退重算"
    assert store.count() == 5

    # 升级写入：sidecar 已生成，绑定当前配置
    sidecar_path = tmp_path / "oldfmt_vectors.json"
    assert sidecar_path.exists()
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar = json.load(f)
    assert sidecar["version"] == 2
    assert sidecar["embedding"]["model"] == "embo-01"
    assert len(sidecar["vectors"]) == 5
    assert len(sidecar["vectors"][0]) == 3

    # 升级后再启动 → 命中缓存（零 API 调用）
    calls["count"] = 0
    store2 = SimpleVectorStore(
        collection_name="oldfmt",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(calls=calls),
    )
    assert calls["count"] == 0
    assert store2.count() == 5


def test_vector_cache_stale_when_docs_digest_mismatch(tmp_path):
    """docs 与 sidecar 内容不一致（同条数但文档已变）→ 摘要校验失败回退重算。"""
    store = SimpleVectorStore(
        collection_name="digest",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(),
    )
    store.add_documents(_make_docs(4))

    # 模拟崩溃窗口：docs 被覆盖为同条数不同内容（sidecar 未同步）
    replaced = [
        Document(
            page_content=f"替换后的第{i}篇内容，与缓存文档完全不同",
            metadata={"title": f"new-{i}"},
        )
        for i in range(4)
    ]
    (tmp_path / "digest_docs.json").write_text(
        json.dumps(
            [
                {"page_content": d.page_content, "metadata": d.metadata}
                for d in replaced
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    calls: dict = {}
    store2 = SimpleVectorStore(
        collection_name="digest",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(calls=calls),
    )
    assert calls.get("count", 0) > 0, "摘要不匹配须回退重算"
    assert store2.count() == 4
    # 重算后 sidecar 已同步更新（绑定新文档摘要）
    with open(tmp_path / "digest_vectors.json", encoding="utf-8") as f:
        sidecar = json.load(f)
    assert len(sidecar["vectors"]) == 4
    assert sidecar["docs_digest"] == SimpleVectorStore._docs_digest(replaced)


def test_atomic_write_preserves_original_on_failure(tmp_path, monkeypatch):
    """原子写：os.replace 失败时原文件不受损、临时文件不残留。"""
    store = SimpleVectorStore(collection_name="atomic", persist_dir=tmp_path)
    store.add_documents(_make_docs(3))
    docs_path = tmp_path / "atomic_docs.json"
    original = docs_path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        store._save()

    assert docs_path.read_text(encoding="utf-8") == original
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_tfidf_path_behavior_unchanged_no_sidecar(tmp_path):
    """TF-IDF 路径行为不变：不写 sidecar，重载重算（词汇表机制）。"""
    store = SimpleVectorStore(collection_name="tfidf_ns", persist_dir=tmp_path)
    store.add_documents(_make_docs(5))
    assert not (tmp_path / "tfidf_ns_vectors.json").exists()
    assert (tmp_path / "tfidf_ns_vocab.json").exists()

    before = [
        (d.page_content, round(s, 6))
        for d, s in store.similarity_search_with_score("黄果树瀑布", k=3)
    ]
    store2 = SimpleVectorStore(collection_name="tfidf_ns", persist_dir=tmp_path)
    after = [
        (d.page_content, round(s, 6))
        for d, s in store2.similarity_search_with_score("黄果树瀑布", k=3)
    ]
    assert before == after
    assert store2.count() == 5


def test_clear_removes_vector_sidecar(tmp_path):
    """clear() 同步删除 docs/vocab/vectors sidecar。"""
    store = SimpleVectorStore(
        collection_name="clear_sidecar",
        persist_dir=tmp_path,
        embedding=_CountingEmbeddings(),
    )
    store.add_documents(_make_docs(3))
    assert (tmp_path / "clear_sidecar_vectors.json").exists()
    store.clear()
    assert not (tmp_path / "clear_sidecar_vectors.json").exists()
    assert not (tmp_path / "clear_sidecar_docs.json").exists()


def test_degraded_api_session_keeps_sidecar_tfidf_restart_recomputes(tmp_path):
    """API 降级会话不写/不覆盖 sidecar；无 key TFIDF 重启回退重算不复用降级向量。

    复现场景（f1 回归）：
    1. TFIDF 主会话写 vocab V1（文档集 D1）；
    2. API 会话文档变更 D2 后 API 失败降级写盘——不得覆盖历史 API sidecar；
    3. 无 key TFIDF 重启：不得复用降级会话的 TF-IDF 向量（与主词汇表不同源），
       须回退重算且检索正常。
    """
    docs_d1 = [
        Document(
            page_content="黄山风景区位于安徽省，以奇松、怪石、云海闻名",
            metadata={"title": "黄山"},
        ),
        Document(
            page_content="泰山位于山东省，五岳之首，日出与石刻壮观",
            metadata={"title": "泰山"},
        ),
        Document(
            page_content="庐山位于江西省，瀑布与云雾景观著称",
            metadata={"title": "庐山"},
        ),
    ]
    extra = [
        Document(
            page_content="长白山天池位于吉林省，火山口湖景色壮美",
            metadata={"title": "长白山"},
        )
    ]

    # 1. TFIDF 主会话（无 key）：拟合 D1 并落盘 vocab V1（TF-IDF 不写 sidecar）
    store0 = SimpleVectorStore(collection_name="degraded_ns", persist_dir=tmp_path)
    store0.add_documents(docs_d1)
    vocab_path = tmp_path / "degraded_ns_vocab.json"
    assert vocab_path.exists()

    # 2a. API 会话（正常）：加载 docs D1，重算并写入历史 API sidecar
    calls: dict = {}
    store1 = SimpleVectorStore(
        collection_name="degraded_ns",
        persist_dir=tmp_path,
        embedding=_make_minimax_emb(calls=calls),
    )
    assert calls["count"] > 0
    sidecar_path = tmp_path / "degraded_ns_vectors.json"
    assert sidecar_path.exists()

    # 2b. API 失败降级会话：追加文档 D2 触发 API 调用失败 → 降级 TF-IDF 写盘
    def failing_handler(request):
        raise httpx.ConnectError("simulated embedding API failure")

    failing_emb = MinimaxEmbeddings(
        api_key="test-key",
        api_base="https://api.minimaxi.com/v1",
        model="embo-01",
        api_style="minimax",
        _transport=httpx.MockTransport(failing_handler),
    )
    store2 = SimpleVectorStore(
        collection_name="degraded_ns",
        persist_dir=tmp_path,
        embedding=failing_emb,
    )
    assert store2.degraded is False
    store2.add_documents(extra)
    assert store2.degraded is True
    # 降级会话不得覆盖历史 API sidecar（binding/docs_digest/条数保持 D1 版本）
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar = json.load(f)
    assert sidecar["embedding"]["backend"] == "minimax"
    assert sidecar["docs_digest"] == SimpleVectorStore._docs_digest(docs_d1)
    assert len(sidecar["vectors"]) == len(docs_d1)
    # 降级备份词汇表 V2 不落盘（vocab 仅主 TF-IDF 保存，避免重启加载错配）
    vocab = json.loads(vocab_path.read_text(encoding="utf-8"))
    assert vocab["doc_count"] == len(docs_d1)

    # 3. 无 key TFIDF 重启：不得复用降级会话的 TF-IDF 向量，须回退重算
    store3 = SimpleVectorStore(collection_name="degraded_ns", persist_dir=tmp_path)
    assert store3.count() == len(docs_d1) + len(extra)
    v1_dim = len(store3.embedding._vocabulary)
    assert v1_dim > 0
    # 向量由主词汇表 V1 重算产出（维度 = V1 词汇表大小），非降级备份 V2
    assert all(len(v) == v1_dim for v in store3._vectors)
    assert len(store2._tfidf_backup._vocabulary) != v1_dim
    # 检索正常（不抛向量维度不匹配 ValueError）
    results = store3.similarity_search_with_score("黄山", k=2)
    assert results
    # 历史 API sidecar 在 TF-IDF 会话期间保留（不删除、不覆盖）
    with open(sidecar_path, encoding="utf-8") as f:
        sidecar_after = json.load(f)
    assert sidecar_after["embedding"]["backend"] == "minimax"
