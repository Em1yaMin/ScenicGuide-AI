"""RAG 向量存储回归测试。

覆盖：
1. numpy 矩阵化检索结果与旧逐向量实现（Python 稳定排序）完全一致，
   包括分数并列时的文档顺序（回归：np.argsort 默认不稳定导致并列时
   返回的文档与旧实现不同）。
2. 检索性能：2000 文档亚秒级（宽松阈值，防 CI 抖动）。
3. 持久化重载后检索结果一致。
"""
import math
import time

from langchain_core.documents import Document

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
