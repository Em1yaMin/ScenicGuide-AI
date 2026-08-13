"""RAG 景区知识问答评测引擎（离线可复跑，纯检索层评测）。

不依赖四服务/真实对话：加载 doc/景区知识库/ 文档 → 用 rag_service 既有
分块/向量化链路建索引（默认本地 TF-IDF，完全离线；可选远端 Embedding）→
对评测集每题检索 top_k → 关键词/拒答阈值判定 → 输出准确率报告。

评测集与判定规则见 doc/rag-eval/eval_set.json（meta.judge_rule）。

使用方式（CLI）:
    python -m scripts.rag_eval --eval-set doc/rag-eval/eval_set.json --out doc/rag-eval/report.md

测试入口: tests/test_rag_eval.py（pytest 门禁，断言准确率 ≥ 阈值）
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from app.core.rag import (
    DEFAULT_DOC_DIR,
    TFIDFEmbeddings,
    SimpleVectorStore,
    get_embedding_model,
)
from app.services.rag_service import (
    _load_documents,
    _normalize_search_query,
    _split_documents,
)

logger = logging.getLogger(__name__)

# 评测集默认路径（仓库根 doc/rag-eval/eval_set.json）
DEFAULT_EVAL_SET = (
    Path(__file__).resolve().parents[3] / "doc" / "rag-eval" / "eval_set.json"
)

# 拒答判定阈值默认值（top1 score 低于该值视为「无有效命中」= 正确拒答）。
# TF-IDF 与 API embedding 分数分布不同，分开配置；可用 --threshold 覆盖。
# api=0.75 为真实 Qwen embedding 校准值（正向最低 0.837 / 拒答最高 0.707，
# 见 doc/rag-eval/README.md「部署注意」）；tfidf=0.30 为本地确定性评测基线。
DEFAULT_REJECT_THRESHOLD = {"tfidf": 0.30, "api": 0.75}

# 判定用关键词归一化：去除空白/全角空格
_WS_RE = re.compile(r"[\s\u3000]+")


def _normalize(text: str) -> str:
    """归一化文本用于关键词子串匹配（去空白，保留标点以保区分度）。"""
    return _WS_RE.sub("", text or "")


def _build_embedding(backend: str):
    """按 backend 构建评测用 embedding：tfidf（离线默认）| api（settings 配置）。"""
    if backend == "tfidf":
        return TFIDFEmbeddings()
    if backend == "api":
        return get_embedding_model()
    raise ValueError(f"未知评测 backend: {backend!r}（可选 tfidf|api）")


def build_eval_index(
    doc_dir: Optional[Path] = None,
    persist_dir: Optional[Path] = None,
    backend: str = "tfidf",
) -> SimpleVectorStore:
    """加载文档 → 分块 → 向量化建索引（评测用独立持久化目录，不污染生产 rag_data）。

    Returns:
        已建好索引的 SimpleVectorStore（collection_name="eval"）
    """
    docs = _load_documents(doc_dir)
    if not docs:
        raise RuntimeError(f"文档目录无内容: {doc_dir or DEFAULT_DOC_DIR}")
    chunks = _split_documents(docs)
    if not chunks:
        raise RuntimeError("文档分块结果为空")

    # 评测必须每次从零建索引：默认用全新临时目录，避免复用上次评测的
    # 持久化（docs/vocab/vectors 会导致索引叠加、结果不可复现）。
    if persist_dir is None:
        persist_dir = Path(tempfile.mkdtemp(prefix="rag_eval_"))
    store = SimpleVectorStore(
        collection_name="eval",
        persist_dir=persist_dir,
        embedding=_build_embedding(backend),
    )
    store.add_documents(chunks)
    logger.info(
        "评测索引构建完成: %d 文档 → %d chunks（backend=%s）",
        len(docs),
        len(chunks),
        store.embedding_backend,
    )
    return store


def _first_hit_rank(case: dict, results: List[dict]):
    """返回首个命中答案关键词的候选位次（1-based）；无命中返回 None。

    any 模式：首个含任一关键词的候选位次（精确 MRR 语义）；
    all 模式：首个含全部关键词的候选位次（需同一候选含全部关键词，
    跨候选分散命中不通过，该题 MRR 计 0——评测集含 2 例 all 模式
    （confuse_002/005））。
    negative 题不计 MRR，返回 None。
    """
    if case.get("type") == "negative":
        return None
    keywords = [
        _normalize(k) for k in (case.get("answer_keywords") or [])
    ]
    if not keywords:
        return None
    mode = case.get("keyword_mode", "any")
    for rank, r in enumerate(results, 1):
        norm = _normalize(r["content"])
        if mode == "all":
            if all(k in norm for k in keywords):
                return rank
        elif any(k in norm for k in keywords):
            return rank
    return None


def judge_case(case: dict, results: List[dict], threshold: float) -> tuple:
    """判定单题是否通过。

    Args:
        case: 评测集条目（含 type/answer_keywords/neg_keywords 等）
        results: 检索结果 [{content, score}, ...]（按分数降序）
        threshold: 拒答判定分数阈值（negative 题用）

    Returns:
        (passed: bool, reason: str)
    """
    kind = case.get("type", "spot")

    if kind == "negative":
        neg_keywords = case.get("neg_keywords") or []
        for kw in neg_keywords:
            norm_kw = _normalize(kw)
            if any(norm_kw in _normalize(r["content"]) for r in results):
                return False, f"误命中库外内容关键词: {kw}"
        if not results:
            return True, "正确拒答（无检索结果）"
        top1 = float(results[0]["score"])
        if top1 < threshold:
            return True, f"正确拒答（top1 score={top1:.3f} < 阈值 {threshold}）"
        return False, f"库外问题给出高分答案（top1={top1:.3f} ≥ 阈值 {threshold}）"

    # 正向题（spot/ticket/show/location/route/history/tips/confuse）
    # 按候选独立判定（与 MRR 位次口径一致），关键词不跨候选拼接
    keywords = case.get("answer_keywords") or []
    if not keywords:
        return False, "评测集缺少 answer_keywords"
    mode = case.get("keyword_mode", "any")
    norm_keywords = [_normalize(k) for k in keywords]
    norm_contents = [_normalize(r["content"]) for r in results]
    if mode == "all":
        hit_candidates = [
            r for r, nc in zip(results, norm_contents)
            if all(k in nc for k in norm_keywords)
        ]
    else:
        hit_candidates = [
            r for r, nc in zip(results, norm_contents)
            if any(k in nc for k in norm_keywords)
        ]
    if hit_candidates:
        hit_list = [
            kw for kw, nk in zip(keywords, norm_keywords)
            if any(nk in nc for nc in norm_contents)
        ]
        return True, f"关键词命中: {hit_list}"
    missed = [
        kw for kw, nk in zip(keywords, norm_keywords)
        if not any(nk in nc for nc in norm_contents)
    ]
    return False, f"关键词未命中: {missed}"


def run_eval(
    cases: List[dict],
    top_k: int = 3,
    threshold: Optional[float] = None,
    backend: str = "tfidf",
    doc_dir: Optional[Path] = None,
    persist_dir: Optional[Path] = None,
) -> dict:
    """对评测集逐题检索并判定，返回结构化报告。

    Returns:
        {
          "meta": {...},
          "top_k": int, "backend": str, "threshold": float,
          "totals": {"total", "passed", "failed", "accuracy"},
          "by_type": {type: {"total", "passed", "accuracy", "failed_ids": [...]}},
          "mrr": float,          # top1 命中率（正向题）
          "failed": [ {id, type, query, expect, reason, top1_score, top1_content, top_k_content} ],
          "duration_sec": float,
        }
    """
    if threshold is None:
        threshold = DEFAULT_REJECT_THRESHOLD.get(
            backend, DEFAULT_REJECT_THRESHOLD["tfidf"]
        )
    start = time.perf_counter()
    store = build_eval_index(
        doc_dir=doc_dir, persist_dir=persist_dir, backend=backend
    )

    results_by_case: List[dict] = []
    for case in cases:
        query = _normalize_search_query(case.get("query", ""))
        hits = store.similarity_search_with_score(query, k=top_k)
        results = [
            {"content": d.page_content, "score": float(s)} for d, s in hits
        ]
        passed, reason = judge_case(case, results, threshold)
        results_by_case.append(
            {
                "id": case.get("id"),
                "type": case.get("type", "spot"),
                "query": query,
                "expect": case.get("expect", ""),
                "passed": passed,
                "reason": reason,
                "top1_score": float(results[0]["score"]) if results else 0.0,
                "top1_content": results[0]["content"] if results else "",
                "top_k_content": "\n".join(r["content"] for r in results),
                # 原始候选（供 MRR 位次计算，不进报告）
                "_retrieved": results,
            }
        )

    total = len(results_by_case)
    passed_total = sum(1 for r in results_by_case if r["passed"])
    by_type: dict = {}
    for r in results_by_case:
        t = r["type"]
        bucket = by_type.setdefault(
            t, {"total": 0, "passed": 0, "accuracy": 0.0, "failed_ids": []}
        )
        bucket["total"] += 1
        if r["passed"]:
            bucket["passed"] += 1
        else:
            bucket["failed_ids"].append(r["id"])
    for bucket in by_type.values():
        bucket["accuracy"] = (
            round(bucket["passed"] / bucket["total"], 4) if bucket["total"] else 0.0
        )

    # MRR@top_k：正向题首个命中关键词候选位次的倒数均值（无命中计 0）
    mrr_sum = 0.0
    mrr_n = 0
    for case, r in zip(cases, results_by_case):
        if case.get("type") == "negative":
            continue
        mrr_n += 1
        rank = _first_hit_rank(case, [
            {"content": x["content"]} for x in r["_retrieved"]
        ])
        if rank is not None:
            mrr_sum += 1.0 / rank
    mrr = round(mrr_sum / mrr_n, 4) if mrr_n else 0.0

    failed = [
        {
            "id": r["id"],
            "type": r["type"],
            "query": r["query"],
            "expect": r["expect"],
            "reason": r["reason"],
            "top1_score": r["top1_score"],
            "top1_content": r["top1_content"][:200],
        }
        for r in results_by_case
        if not r["passed"]
    ]

    duration = round(time.perf_counter() - start, 1)
    return {
        "meta": {
            "cases": total,
            "top_k": top_k,
            "backend": backend,
            "threshold": threshold,
            "embedding_backend": store.embedding_backend,
            "degraded": store.degraded,
            "duration_sec": duration,
        },
        "totals": {
            "total": total,
            "passed": passed_total,
            "failed": total - passed_total,
            "accuracy": round(passed_total / total, 4) if total else 0.0,
        },
        "by_type": by_type,
        "mrr": mrr,
        "failed": failed,
    }


def load_eval_set(path: Optional[Path] = None) -> dict:
    """加载评测集 JSON。"""
    path = Path(path) if path else DEFAULT_EVAL_SET
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not data.get("cases"):
        raise ValueError(f"评测集为空: {path}")
    return data


def render_markdown_report(report: dict) -> str:
    """把结构化评测报告渲染为 Markdown（供留存/人工阅读）。"""
    lines = [
        "# RAG 景区知识问答评测报告",
        "",
        f"- 评测集: {report['meta']['cases']} 题",
        f"- 检索 top_k: {report['meta']['top_k']}",
        f"- embedding 后端: {report['meta']['embedding_backend']}"
        + ("（降级 TF-IDF）" if report["meta"]["degraded"] else ""),
        f"- 拒答阈值: {report['meta']['threshold']}",
        f"- 耗时: {report['meta']['duration_sec']}s",
        "",
        "## 总体成绩",
        "",
        f"**准确率: {report['totals']['accuracy'] * 100:.1f}%**"
        f"（{report['totals']['passed']}/{report['totals']['total']} 通过，"
        f"{report['totals']['failed']} 失败）；正向题 MRR@top_k≈{report['mrr']:.3f}"
        "（首命中关键词候选位次的倒数均值）",
        "",
        "## 按题型统计",
        "",
        "| 题型 | 通过/总数 | 准确率 | 失败用例 |",
        "| --- | --- | --- | --- |",
    ]
    for t, bucket in sorted(report["by_type"].items()):
        lines.append(
            f"| {t} | {bucket['passed']}/{bucket['total']} | "
            f"{bucket['accuracy'] * 100:.1f}% | {', '.join(bucket['failed_ids']) or '-'} |"
        )
    lines += ["", "## 失败用例明细", ""]
    if not report["failed"]:
        lines.append("（无失败用例）")
    for item in report["failed"]:
        lines += [
            f"### {item['id']} [{item['type']}] {item['query']}",
            "",
            f"- 预期: {item['expect']}",
            f"- 判定: {item['reason']}",
            f"- top1 score: {item['top1_score']:.3f}",
            f"- top1 内容: {item['top1_content'][:160]}",
            "",
        ]
    return "\n".join(lines)
