"""意图分类层（app/services/intent_classifier.py）单测。

覆盖：简单/复杂分类、简单信号优先、分类失败安全降级（默认简单）、
空消息防御、与既有对话测试查询的兼容性（不误分类为复杂）。
"""

import pytest

from app.services.intent_classifier import (
    COMPLEX,
    SIMPLE,
    classify_intent,
    classify_intent_detail,
)


# ── 复杂（多步骤意图） ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "规划灵山一日游，带老人小孩，喜欢拍照",
        "帮我安排一下明天的行程",
        "有什么好玩的路线推荐？",
        "帮我规划两日游，带娃，预算充足",
        "灵山和拈花湾怎么安排比较好？",
        "有没有亲子游攻略？",
        "制定行程：早上看花海，下午逛小镇",
        "推荐一条适合情侣的半日游路线",
        "带老人小孩一日游怎么玩？",
    ],
)
def test_classify_complex_multi_step(query):
    """多步骤规划意图 → complex。"""
    assert classify_intent(query) == COMPLEX


# ── 简单（单工具可答） ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "景区几点开门？",
        "开放时间是什么时候？",
        "灵山在哪？",
        "门票多少钱？",
        "怎么去拈花湾？",
        "讲解一下灵山大佛的历史",
        "你好",
        "谢谢",
        "有哪些自然风光景点？",
        "讲个讲解词",
        "我喜欢拍照",
        "推荐路线",
        "推荐适合拍照的路线",
        "有什么推荐？",
        "第一问",
        "第二问",
        "景区适合带孩子吗",
        "这里难不难走？",
        "",
        "   ",
        # 船长决策 2026-08-06（nm-f1-wordlist 方案B）：纯人群/导航单轮查询
        # 不判复杂（走快速路径，避免规划路径延迟增量）
        "灵山怎么走？",
        "适合带老人去吗",
        "带小孩适合玩什么？",
        "带老人小孩去合适吗",
        "带小孩可以玩什么项目",
    ],
)
def test_classify_simple_single_fact(query):
    """单事实查询 / 无规划信号 → simple（含既有测试查询回归与收窄词表回归）。"""
    assert classify_intent(query) == SIMPLE


def test_captain_wordlist_narrowing_keeps_compound_planning_complex():
    """船长决策收窄词表后：组合规划类查询（人群偏好 + 规划词）仍判复杂。"""
    for q in [
        "规划灵山一日游，带老人小孩，喜欢拍照",
        "带老人小孩一日游怎么玩？",
        "帮我规划两日游，带娃，预算充足",
        "推荐一条适合情侣的半日游路线",
        "制定行程：早上看花海，下午逛小镇",
    ]:
        assert classify_intent(q) == COMPLEX, q


def test_simple_signal_outweighs_single_complex_word():
    """单事实信号多于复杂信号时按简单处理。"""
    # 「开放时间/开放/门票/多少钱」4 个简单信号 > 「安排/怎么安排」2 个复杂信号
    assert classify_intent("开放时间怎么安排？门票多少钱") == SIMPLE
    # 复杂信号不少于简单信号 → 复杂（多意图优先，规划路径仍能回答）
    assert classify_intent("开放时间怎么安排") == COMPLEX
    # 几点开门 + 路线推荐：1 复杂 ≥ 1 简单 → 复杂
    assert classify_intent("几点开门？顺便推荐一条路线") == COMPLEX


# ── 失败安全降级 ───────────────────────────────────────────────────────────


def test_classify_failure_degrades_to_simple(monkeypatch):
    """分类内部异常 → 默认 simple（安全降级，快速路径不中断）。"""
    def _boom(query, keywords):
        raise RuntimeError("分类器内部故障")

    monkeypatch.setattr(
        "app.services.intent_classifier._match_keywords", _boom
    )
    # 异常被捕获并降级
    assert classify_intent("规划一日游") == SIMPLE
    intent, complex_hits, simple_hits = classify_intent_detail("规划一日游")
    assert intent == SIMPLE
    assert complex_hits == []
    assert simple_hits == []


def test_classify_detail_reports_hits():
    """detail 接口返回命中的复杂/简单关键词（供日志与断言）。"""
    intent, complex_hits, simple_hits = classify_intent_detail(
        "规划一日游，顺便给个路线推荐"
    )
    assert intent == COMPLEX
    assert "规划" in complex_hits
    assert "一日游" in complex_hits
    assert "路线推荐" in complex_hits
    assert simple_hits == []
