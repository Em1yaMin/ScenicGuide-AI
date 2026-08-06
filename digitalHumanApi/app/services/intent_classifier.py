"""意图分类层（阶段3 T1 双模式：简单问答 / 复杂规划）。

船长决策 2026-08-06 方案B：对话入口先识别任务复杂度——

- **简单**（单工具可答，如「开放时间」「在哪」）→ 快速路径
  （现有 create_agent 流程，零延迟增加）
- **复杂**（多步骤意图，如「规划灵山一日游，带老人小孩，喜欢拍照」）
  → 规划路径（LangGraph 规划图：规划 → 多步执行 → 总结）
- **分类失败默认走快速路径**（安全降级：任何异常 / 未匹配规则一律按简单处理，
  对话不中断、不额外调用 LLM）

实现选择说明：采用**轻量规则分类**（船长决策「建议轻量规则或 LLM 单次分类」，
选前者）——

1. **快速路径零延迟**：分类发生在每次对话入口，若走 LLM 单次分类会给每条
   简单消息引入一次额外 LLM 往返（数百 ms 级延迟），违背「简单问答延迟无
   显著增加」的验收标准；规则分类为纯字符串匹配，微秒级
2. **可解释、可测试**：词表与打分规则显式可审，无需 mock LLM 即可覆盖
   简单/复杂/失败降级全部分支
3. **安全降级天然成立**：未命中任何词表（未知问题）默认简单，走已验证的
   快速路径，不会因分类器缺陷引入新故障面

词表设计注意（与既有测试查询兼容，避免回归）：

- 「拍照」「推荐」「推荐路线」等**偏好表达**不算复杂信号——它们本身是
  单轮可答的偏好陈述（快速路径的 recommend_routes 工具即可处理）；
  仅「路线推荐」「推荐一条」等明确多步规划表述计入复杂信号
- 复杂信号聚焦**多步规划意图**：规划/安排/行程/攻略/一日游/带老人小孩等
- 简单信号聚焦**单事实查询**：开放时间/在哪/怎么去/门票/价格等
- 同一查询同时命中复杂与简单信号时按命中数比较（多步意图优先于单事实，
  规划路径仍能正确回答单事实子问题）
"""
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

# 分类结果常量（与任务契约一致）
SIMPLE = "simple"
COMPLEX = "complex"

# ── 规则词表 ──────────────────────────────────────────────────────────────
#
# 复杂信号：多步规划意图（规划行程/带人群偏好/路线组合等）。
# 注意：不含「拍照」「推荐」「推荐路线」等单轮偏好表达（见模块头设计说明）。
_COMPLEX_KEYWORDS: Tuple[str, ...] = (
    "规划",
    "安排",
    "行程",
    "攻略",
    "一日游",
    "半日游",
    "两日游",
    "三日游",
    "多日游",
    "怎么玩",
    "怎么安排",
    "怎么走",
    "路线推荐",
    "推荐一条",
    "带老人",
    "带小孩",
    "老人小孩",
    "带娃",
    "亲子游",
    "情侣游",
    "全家游",
    "家庭游",
    "预算",
    "制定行程",
    "帮我计划",
    "帮我规划",
)

# 简单信号：单事实查询（一问一答，快速路径即可）。
_SIMPLE_KEYWORDS: Tuple[str, ...] = (
    "开放时间",
    "营业时间",
    "几点",
    "在哪",
    "在哪里",
    "怎么去",
    "门票",
    "价格",
    "多少钱",
    "电话",
    "地址",
    "历史",
    "故事",
    "讲解",
    "传说",
    "简介",
    "介绍",
    "开放",
    "停车",
    "公交",
    "地铁",
    "自驾",
    "多久",
    "多长时间",
    "难不难",
    "适合吗",
    "可以带",
    "能带",
    "天气",
    "厕所",
    "餐厅",
)


def _match_keywords(query: str, keywords: Tuple[str, ...]) -> List[str]:
    """返回 query 中命中的关键词列表（去重，保持词表顺序）。"""
    hits = []
    for kw in keywords:
        if kw in query:
            hits.append(kw)
    return hits


def classify_intent_detail(query: str) -> Tuple[str, List[str], List[str]]:
    """分类并返回命中的复杂/简单关键词（供日志与测试断言）。

    Returns:
        (intent, complex_hits, simple_hits)：intent ∈ {"simple", "complex"}
    """
    q = (query or "").strip()
    if not q:
        # 空消息：无规则可命中，按简单处理（防御）
        return SIMPLE, [], []
    try:
        complex_hits = _match_keywords(q, _COMPLEX_KEYWORDS)
        simple_hits = _match_keywords(q, _SIMPLE_KEYWORDS)
    except Exception:
        # 分类失败安全降级：默认简单（快速路径），绝不阻断对话
        logger.exception("意图分类异常（安全降级为简单）: query=%r", q)
        return SIMPLE, [], []

    # 多步意图信号优先：有复杂命中且不比简单命中少 → 复杂；否则简单
    if complex_hits and len(complex_hits) >= len(simple_hits):
        return COMPLEX, complex_hits, simple_hits
    return SIMPLE, complex_hits, simple_hits


def classify_intent(query: str) -> str:
    """意图分类入口：返回 "simple" | "complex"（失败默认 simple）。"""
    intent, complex_hits, simple_hits = classify_intent_detail(query)
    if complex_hits or simple_hits:
        logger.debug(
            "意图分类: intent=%s complex_hits=%s simple_hits=%s",
            intent,
            complex_hits,
            simple_hits,
        )
    return intent
