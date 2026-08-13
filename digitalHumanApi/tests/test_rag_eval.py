"""RAG 景区知识问答评测门禁（pytest，离线可复跑）。

对 doc/rag-eval/eval_set.json 全量 58 题跑检索层评测：
- 默认 TF-IDF（conftest 固定无 embedding key）完全离线；
- 独立临时持久化目录，不污染 rag_data；
- 准确率 ≥ 90%（船长要求），且各关键题型（拒答/边界混淆/景点）不低于 90%；
- 失败用例明细用 CLI 复现：`python -m scripts.rag_eval --json-out /tmp/report.json`

评测设计/轮次分数/失败分析见 doc/rag-eval/README.md 与 report_*.md。
"""
import pytest

from app.services.rag_eval import (
    DEFAULT_EVAL_SET,
    _first_hit_rank,
    load_eval_set,
    run_eval,
)
from app.services.rag_service import (
    _load_documents,
    _normalize_search_query,
    _parse_markdown_sections,
    _split_documents,
)

# 总体准确率门禁（船长要求 ≥90%，当前 100%）
MIN_ACCURACY = 0.90
# 关键题型门禁（拒答/边界混淆/景点介绍）
KEY_TYPE_MIN_ACCURACY = 0.90


@pytest.fixture(scope="module")
def eval_report(tmp_path_factory):
    """全量评测一次（建索引约 2-4s），供多个用例共享。"""
    eval_set = load_eval_set()
    return run_eval(
        cases=eval_set["cases"],
        top_k=3,
        backend="tfidf",
        persist_dir=tmp_path_factory.mktemp("rag_eval"),
    )


# ---------------------------------------------------------------------------
# 评测集完整性
# ---------------------------------------------------------------------------

def test_eval_set_exists_and_covers_required_types():
    """评测集 ≥50 题，覆盖全部要求题型。"""
    assert DEFAULT_EVAL_SET.exists(), f"评测集缺失: {DEFAULT_EVAL_SET}"
    eval_set = load_eval_set()
    assert len(eval_set["cases"]) >= 50, "评测集应 ≥50 题"

    types = {c["type"] for c in eval_set["cases"]}
    required = {
        "spot", "ticket", "show", "location", "route",
        "history", "tips", "confuse", "negative",
    }
    missing = required - types
    assert not missing, f"评测集缺少题型: {missing}"

    # 每道正向题必须有答案关键词；negative 题必须有 neg_keywords
    for case in eval_set["cases"]:
        if case["type"] == "negative":
            assert case.get("neg_keywords"), f"{case['id']} 缺少 neg_keywords"
        else:
            assert case.get("answer_keywords"), f"{case['id']} 缺少 answer_keywords"
            assert case.get("keyword_mode") in ("any", "all"), f"{case['id']} keyword_mode 非法"


# ---------------------------------------------------------------------------
# 评测门禁
# ---------------------------------------------------------------------------

def test_overall_accuracy_above_gate(eval_report):
    """总体准确率 ≥ 90%（船长要求）。"""
    acc = eval_report["totals"]["accuracy"]
    assert acc >= MIN_ACCURACY, (
        f"RAG 评测准确率 {acc:.1%}（{eval_report['totals']['passed']}/"
        f"{eval_report['totals']['total']}）低于门禁 {MIN_ACCURACY:.0%}；"
        "失败用例见 eval_report['failed']，可用 CLI 复现"
    )


def test_key_types_accuracy_above_gate(eval_report):
    """关键题型（拒答/边界混淆/景点介绍）准确率 ≥ 90%。"""
    for t in ("negative", "confuse", "spot"):
        bucket = eval_report["by_type"].get(t)
        assert bucket, f"评测报告缺少题型 {t}"
        assert bucket["accuracy"] >= KEY_TYPE_MIN_ACCURACY, (
            f"题型 {t} 准确率 {bucket['accuracy']:.1%} "
            f"（{bucket['passed']}/{bucket['total']}）低于门禁 {KEY_TYPE_MIN_ACCURACY:.0%}"
        )


def test_negative_cases_never_hit_out_of_kb(eval_report):
    """知识库外问题：任何 negative 用例都不得命中库外关键词。"""
    eval_set = load_eval_set()
    by_id = {c["id"]: c for c in eval_set["cases"]}
    for item in eval_report["failed"]:
        case = by_id[item["id"]]
        if case["type"] == "negative":
            # 失败原因若为误命中库外关键词则严重回归
            assert "误命中" not in item["reason"], (
                f"negative 用例 {item['id']} 误命中库外内容: {item['reason']}"
            )


# ---------------------------------------------------------------------------
# 分块/查询规范化基础设施（Round 1 优化行为契约）
# ---------------------------------------------------------------------------

def test_sections_carry_title_context():
    """标题感知分块：chunk 携带完整标题链，不再产生孤立标题/字段碎片。"""
    docs = _load_documents()
    chunks = _split_documents(docs)
    assert chunks

    # 每个 chunk 都应自包含标题上下文（灵山文档全为带标题 Markdown）
    for chunk in chunks:
        assert chunk.metadata.get("title"), "chunk 应携带标题链 metadata"
        assert chunk.page_content.startswith(chunk.metadata["title"]), (
            "chunk 内容应以标题链开头（自包含上下文）"
        )
    # 不再有 <50 字符的碎片 chunk（旧策略 3769 块中 56% 为碎片）
    short = [c for c in chunks if len(c.page_content) < 50]
    assert not short, f"仍存在碎片 chunk {len(short)} 个: {[c.page_content[:30] for c in short[:5]]}"


def test_parse_markdown_sections_structure():
    """标题栈解析：标题链不重复、层级正确、正文归属正确。"""
    text = (
        "# 景区\n"
        "介绍段落\n"
        "## 子表\n"
        "### 景点甲\n"
        "- 字段：内容甲\n"
        "### 景点乙\n"
        "- 字段：内容乙\n"
        "## 另一子表\n"
        "### 景点丙\n"
        "正文丙\n"
    )
    sections = _parse_markdown_sections(text)
    chains = [c for c, _ in sections]
    assert chains == [
        "景区",
        "景区 > 子表 > 景点甲",
        "景区 > 子表 > 景点乙",
        "景区 > 另一子表 > 景点丙",
    ], f"标题链解析错误: {chains}"
    bodies = dict(sections)
    assert "介绍段落" in bodies["景区"]
    assert "内容甲" in bodies["景区 > 子表 > 景点甲"]
    assert "正文丙" in bodies["景区 > 另一子表 > 景点丙"]
    # 无标题纯文本 → 空（回退旧切分）
    assert _parse_markdown_sections("纯文本\n没有标题") == []


def test_query_normalization_expands_time_intent():
    """查询规范化：时间/营业意图词追加同义词，其余查询原样返回。"""
    assert _normalize_search_query("拈花湾晚上营业到几点") == (
        "拈花湾晚上营业到几点 时间 开放"
    )
    assert _normalize_search_query("景区几点开门") == "景区几点开门 时间 开放"
    # 目标词已存在不重复追加（“开放”已有则不重复），其余触发仍生效；无触发词原样
    assert _normalize_search_query("灵山梵宫几点开门 开放") == (
        "灵山梵宫几点开门 开放 时间"
    )
    assert _normalize_search_query("灵山大佛有多高") == "灵山大佛有多高"
    assert _normalize_search_query("") == ""


def test_query_normalization_expands_ticket_intent():
    """查询规范化：门票价格意图追加免费开放（免票景区表述桥接），其余原样。"""
    assert _normalize_search_query("拈花湾的门票价格是多少") == (
        "拈花湾的门票价格是多少 免费开放"
    )
    assert _normalize_search_query("泰山门票价格") == "泰山门票价格 免费开放"
    # 付费票价询问不触发（避免拉偏付费票价检索）；目标词已有则不重复
    assert _normalize_search_query("灵山胜境成人票多少钱") == "灵山胜境成人票多少钱"
    assert _normalize_search_query("门票价格 免费开放") == "门票价格 免费开放"


def test_first_hit_rank_computes_candidate_position():
    """MRR 位次：any 模式取首个含任一关键词的候选位次；无命中/negative 为 None。"""
    case = {
        "type": "spot",
        "answer_keywords": ["88m"],
        "keyword_mode": "any",
    }
    results = [
        {"content": "LS-001 灵山大照壁 39.8m"},
        {"content": "LS-011 灵山大佛 高88m"},
    ]
    assert _first_hit_rank(case, results) == 2
    assert _first_hit_rank(case, [{"content": "无关内容"}]) is None
    assert _first_hit_rank({**case, "type": "negative"}, results) is None
    # all 模式：需同一候选含全部关键词
    case_all = {"type": "spot", "answer_keywords": ["88m", "725吨"], "keyword_mode": "all"}
    assert _first_hit_rank(case_all, results) is None
    assert _first_hit_rank(
        case_all,
        [{"content": "无关"}, {"content": "灵山大佛 高88m 耗铜725吨"}],
    ) == 2
