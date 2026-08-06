"""
AI 运营建议模块 — LangChain 聚合分析链。

从对话数据中提取：
  - 热门问题 TOP10
  - 游客满意度趋势
  - 未回答好的问题
  - 高峰时段分析

使用 LangChain + OpenAI 构建分析链，生成结构化运营报告。
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dialogue import Dialogue, DialogueRole
from app.models.evaluation import Evaluation
from app.schemas.analysis import (
    AIOverviewResponse,
    DailyCountItem,
    DailyScoreItem,
    HotQuestionItem,
    PeakHourItem,
    SatisfactionTrendItem,
    TrendsResponse,
    UnansweredQuestionItem,
)

logger = logging.getLogger(__name__)

# ── LLM 超时参数 ──
# 真实环境实测（2026-08-06，opencode-go + deepseek-v4-flash）：
# 运营分析 prompt 单次调用约 55-90s（端点延迟高且波动大），
# 原 request_timeout=60 / wait_for(90) 会稳定超时导致频繁回退 DB 兜底。
# 故 httpx 超时给到 180s，总等待上限 240s（需大于 httpx 超时，
# 使底层超时错误正常上抛给降级链捕获）。
LLM_REQUEST_TIMEOUT = 180
LLM_ANALYSIS_TIMEOUT = 240

# ── LangChain 初始化（延迟加载，避免启动时无网络/无 key 崩溃） ──

_llm = None
_chain = None


def _get_llm():
    """延迟初始化 ChatOpenAI 实例。"""
    global _llm
    if _llm is None:
        from langchain_openai import ChatOpenAI

        _llm = ChatOpenAI(
            model=settings.OPENAI_MODEL,
            temperature=0.3,
            openai_api_key=settings.OPENAI_API_KEY,
            openai_api_base=settings.OPENAI_API_BASE or None,
            max_tokens=2048,
            request_timeout=LLM_REQUEST_TIMEOUT,
        )
    return _llm


def _get_analysis_chain():
    """构建 LangChain 分析链：格式化对话数据 → LLM 分析 → JSON 解析。"""
    global _chain
    if _chain is not None:
        return _chain

    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    prompt = ChatPromptTemplate.from_messages([
        ("system", """你是一位资深的景区运营数据分析师。请根据提供的游客对话数据，生成一份运营分析报告。

**分析要求：**
1. **热门问题 TOP10**：统计游客最常问的问题类型（合并相似问题），按频次降序列出前10个。每个问题包含：问题主题(question)、出现次数(count)、问题分类(category，如：路线/票务/设施/餐饮/其他)。
2. **满意度趋势分析**：根据对话中的情感倾向和已有的评分数据，总结游客满意度变化趋势，指出趋势是「上升」「下降」还是「平稳」。
3. **未回答好的问题**：识别出AI助手回答不够好或游客反复追问的问题，列出最多5个。每个包含：问题内容(question)、为什么判断回答不佳(reason)。
4. **高峰时段分析**：分析对话的时间分布，识别出高峰时段（按小时聚合）。列出top活跃时段。
5. **运营建议**：基于以上分析，给出3-5条具体可执行的运营优化建议。

**重要：请严格按以下JSON格式输出，不要包含markdown代码块标记或其他文字：**
{{
  "hot_questions": [
    {{"question": "...", "count": 数字, "category": "路线/票务/设施/餐饮/其他"}}
  ],
  "satisfaction_summary": "整体趋势描述...",
  "satisfaction_direction": "上升/下降/平稳",
  "unanswered_questions": [
    {{"question": "...", "reason": "..."}}
  ],
  "peak_hours": [
    {{"hour": 数字(0-23), "count": 数字, "label": "如 上午9点"}}
  ],
  "suggestions": ["建议1", "建议2", ...]
}}"""),
        ("user", "以下是最近{days}天的游客对话数据（已脱敏，仅保留对话内容、角色和时间）：\n\n{data_summary}\n\n请开始分析。"),
    ])

    _chain = prompt | _get_llm() | StrOutputParser()
    return _chain


# ── 数据加载 ──

async def _count_dialogues(db: AsyncSession, days: int) -> int:
    """统计指定日期范围内的对话总数（避免全量加载）。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(func.count(Dialogue.id)).where(Dialogue.timestamp >= since)
    )
    return result.scalar() or 0


async def _load_dialogue_sample(
    db: AsyncSession, days: int, limit: int = 200
) -> List[Dict[str, Any]]:
    """加载对话随机样本（最多 limit 条），用于 LLM 分析。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(Dialogue)
        .where(Dialogue.timestamp >= since)
        .order_by(func.random())
        .limit(limit)
    )
    dialogues = result.scalars().all()

    data = []
    for d in dialogues:
        item = {
            "id": d.id,
            "role": d.role.value if isinstance(d.role, DialogueRole) else str(d.role),
            "content": d.content[:500],
            "timestamp": d.timestamp.isoformat() if d.timestamp else None,
            "hour": d.timestamp.hour if d.timestamp else None,
            "weekday": d.timestamp.weekday() if d.timestamp else None,
        }
        data.append(item)

    return data


async def _load_evaluation_summary(
    db: AsyncSession, days: int = 30
) -> Dict[str, Any]:
    """加载评估数据的统计摘要（用于补充 LLM 分析）。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 评分分布
    score_result = await db.execute(
        select(
            Evaluation.satisfaction_score,
            func.count(Evaluation.id).label("cnt"),
        )
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Dialogue.timestamp >= since,
            Evaluation.satisfaction_score.isnot(None),
        )
        .group_by(Evaluation.satisfaction_score)
    )
    score_dist = {str(row.satisfaction_score): row.cnt for row in score_result.all()}

    # 情感分布
    sentiment_result = await db.execute(
        select(
            Evaluation.sentiment_label,
            func.count(Evaluation.id).label("cnt"),
        )
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Dialogue.timestamp >= since,
            Evaluation.sentiment_label.isnot(None),
        )
        .group_by(Evaluation.sentiment_label)
    )
    sentiment_dist = {}
    for row in sentiment_result.all():
        sentiment_dist[row.sentiment_label or "unknown"] = row.cnt

    # 每日对话量
    daily_result = await db.execute(
        select(
            func.date(Dialogue.timestamp).label("day"),
            func.count(Dialogue.id).label("cnt"),
        )
        .where(Dialogue.timestamp >= since)
        .group_by(func.date(Dialogue.timestamp))
        .order_by(func.date(Dialogue.timestamp))
    )
    daily_counts = {str(row.day): row.cnt for row in daily_result.all()}

    return {
        "evaluation_count": sum(score_dist.values()),
        "score_distribution": score_dist,
        "sentiment_distribution": sentiment_dist,
        "daily_counts": daily_counts,
    }


def _build_data_summary(
    dialogues: List[Dict[str, Any]],
    eval_summary: Dict[str, Any],
    total_dialogues: int,
) -> str:
    """构建发送给 LLM 的数据摘要文本。"""
    lines = [
        f"总对话条数：{total_dialogues}（以下为 {len(dialogues)} 条抽样数据）",
        f"评估数据覆盖：{eval_summary.get('evaluation_count', 0)} 条",
        f"评分分布：{eval_summary.get('score_distribution', {})}",
        f"情感分布：{eval_summary.get('sentiment_distribution', {})}",
        f"每日对话量：{eval_summary.get('daily_counts', {})}",
        "",
        "--- 对话样本（用户: U, AI: A） ---",
    ]

    for d in dialogues:
        role_tag = "U" if d["role"] == "user" else "A"
        ts = d.get("timestamp", "")[:19]
        content = d.get("content", "").replace("\n", " ")
        lines.append(f"[{ts}] {role_tag}: {content}")

    return "\n".join(lines)


# ── JSON 解析 ──

def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 JSON 对象（容错多种格式）。"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 代码块
    match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试括号平衡匹配：找到第一个 { 及其匹配的 }
    start = text.find('{')
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    logger.warning("Failed to extract JSON from LLM output: %s", text[:500])
    return None


# ── 统计计算（不依赖 LLM 的确定性数据） ──

async def _compute_peak_hours(
    db: AsyncSession, days: int
) -> List[PeakHourItem]:
    """从数据库直接计算高峰时段分布。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 使用 strftime 兼容 SQLite（%H: 00-24 小时）
    hour_expr = func.strftime("%H", Dialogue.timestamp)
    result = await db.execute(
        select(
            hour_expr.label("hour"),
            func.count(Dialogue.id).label("cnt"),
        )
        .where(Dialogue.timestamp >= since)
        .group_by(hour_expr)
        .order_by(hour_expr)
    )
    hours = []
    for row in result.all():
        h = int(row.hour)
        hours.append(PeakHourItem(
            hour=h,
            count=row.cnt,
            label=f"{h}:00-{h}:59",
        ))
    return sorted(hours, key=lambda x: x.count, reverse=True)


async def _compute_hot_questions_from_db(
    db: AsyncSession, days: int
) -> List[HotQuestionItem]:
    """从评估数据的 concern_tags 统计热门问题。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(Evaluation.concern_tags)
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Dialogue.timestamp >= since,
            Evaluation.concern_tags.isnot(None),
        )
    )
    counter: Dict[str, int] = {}
    for row in result.all():
        tags = row.concern_tags
        if isinstance(tags, list):
            for tag in tags:
                counter[tag] = counter.get(tag, 0) + 1

    return [
        HotQuestionItem(question=tag, count=c, category="")
        for tag, c in sorted(counter.items(), key=lambda x: x[1], reverse=True)[:10]
    ]


# ── DB 统计兜底 ──

def _build_db_fallback_summary(
    satisfaction_trend: List[SatisfactionTrendItem],
    hot_questions: List[HotQuestionItem],
    peak_hours: List[PeakHourItem],
    eval_summary: Dict[str, Any],
) -> tuple[str, str, List[str]]:
    """LLM 不可用时基于 DB 统计生成确定性摘要。

    返回 (satisfaction_direction, satisfaction_summary, suggestions)：
    - 满意度方向：对比满意度趋势前半段与后半段平均分（阈值 ±0.05）
    - 摘要：评估覆盖、高频咨询、高峰时段等纯统计描述
    - 建议：由热点问题/高峰时段推导的确定性运营建议
    """
    direction = "平稳"
    scored = [t for t in satisfaction_trend if t.count > 0]
    if len(scored) >= 2:
        mid = len(scored) // 2
        early = [t.avg_score for t in scored[:mid]]
        late = [t.avg_score for t in scored[mid:]]
        diff = sum(late) / len(late) - sum(early) / len(early)
        if diff > 0.05:
            direction = "上升"
        elif diff < -0.05:
            direction = "下降"

    parts: List[str] = []
    suggestions: List[str] = []

    score_dist = eval_summary.get("score_distribution", {}) or {}
    total_scores = sum(int(v) for v in score_dist.values())
    weighted = sum(
        int(k) * int(v) for k, v in score_dist.items() if str(k).lstrip("-").isdigit()
    )
    avg_score = round(weighted / total_scores, 1) if total_scores else 0.0
    eval_count = eval_summary.get("evaluation_count", 0) or 0
    if eval_count:
        parts.append(f"基于 {eval_count} 条游客评分数据（平均 {avg_score} 分）")
        suggestions.append("关注低分评价集中反馈的问题，优先优化对应服务环节")
    else:
        parts.append("暂无游客评分评估数据")

    if hot_questions:
        tops = "、".join(h.question for h in hot_questions[:3])
        parts.append(f"高频咨询：{tops}")
        suggestions.append(
            f"在官网/小程序增设高频问题（如：{tops}）的 FAQ 入口"
        )
    else:
        parts.append("暂无评估关联的高频关注点")

    if peak_hours:
        top_hour = peak_hours[0]
        parts.append(f"高峰时段：{top_hour.label}")
        suggestions.append(
            f"建议在高峰时段（{top_hour.label}）增配服务人员与疏导力量"
        )

    summary = (
        f"最近统计显示：{'；'.join(parts)}。"
        f"满意度趋势{direction}（数据库统计兜底）。"
    )
    return direction, summary, suggestions[:3]


# ── 公开 API ──

async def get_ai_overview(
    db: AsyncSession, days: int = 30
) -> AIOverviewResponse:
    """运行 LangChain 分析链，返回 AI 运营概览。

    流程：
    1. 加载对话数据 + 评估摘要
    2. 构建数据摘要文本
    3. 调用 LangChain 链（LLM 分析）
    4. 解析 JSON 输出
    5. 补充数据库统计（高峰时段、热门问题兜底）
    """
    days = max(1, min(days, 365))  # 限制 1-365 天

    # 加载数据
    total_dialogues = await _count_dialogues(db, days)
    dialogues = await _load_dialogue_sample(db, days, limit=200)
    eval_summary = await _load_evaluation_summary(db, days)

    # 默认值
    hot_questions: List[HotQuestionItem] = []
    satisfaction_direction = "平稳"
    satisfaction_summary = "暂无足够数据进行分析"
    unanswered_questions: List[UnansweredQuestionItem] = []
    suggestions: List[str] = []
    peak_hours_from_ai: List[PeakHourItem] = []
    # 降级标记：true=LLM 分析产出结果，false=回退纯 DB 统计
    llm_analysis = False

    if total_dialogues == 0:
        logger.info("No dialogues found in last %d days, returning empty overview", days)
        # 即使没有对话，高峰时段也可以返回空
        return AIOverviewResponse(
            hot_questions=[],
            satisfaction_trend=[],
            satisfaction_direction=satisfaction_direction,
            satisfaction_summary=satisfaction_summary,
            unanswered_questions=[],
            peak_hours=[],
            suggestions=[],
            ai_summary=f"最近 {days} 天内暂无对话数据。",
            total_dialogues=0,
            analysis_timestamp=datetime.now(timezone.utc).isoformat(),
        )

    # 尝试 LLM 分析
    ai_raw = None
    if settings.OPENAI_API_KEY:
        try:
            chain = _get_analysis_chain()
            data_summary = _build_data_summary(dialogues, eval_summary, total_dialogues)
            llm_output = await asyncio.wait_for(
                chain.ainvoke({"days": days, "data_summary": data_summary}),
                timeout=LLM_ANALYSIS_TIMEOUT,
            )
            ai_raw = _extract_json(llm_output)
            if ai_raw:
                llm_analysis = True
                logger.info("LLM analysis succeeded, extracted JSON with keys: %s", list(ai_raw.keys()))
        except Exception:
            logger.exception("LLM analysis failed, falling back to DB-only stats")

    # 解析 AI 输出
    if ai_raw:
        try:
            # 热门问题
            raw_hot = ai_raw.get("hot_questions", [])
            if isinstance(raw_hot, list):
                for item in raw_hot:
                    if isinstance(item, dict):
                        hot_questions.append(HotQuestionItem(
                            question=str(item.get("question", "")),
                            count=int(item.get("count", 0)),
                            category=str(item.get("category", "")),
                        ))

            satisfaction_summary = str(ai_raw.get("satisfaction_summary", satisfaction_summary))
            satisfaction_direction = str(ai_raw.get("satisfaction_direction", "平稳"))

            # 未回答好的问题
            raw_unanswered = ai_raw.get("unanswered_questions", [])
            if isinstance(raw_unanswered, list):
                for item in raw_unanswered:
                    if isinstance(item, dict):
                        unanswered_questions.append(UnansweredQuestionItem(
                            question=str(item.get("question", "")),
                            reason=str(item.get("reason", "")),
                        ))

            # 高峰时段（AI 版本）
            raw_peaks = ai_raw.get("peak_hours", [])
            if isinstance(raw_peaks, list):
                for item in raw_peaks:
                    if isinstance(item, dict):
                        peak_hours_from_ai.append(PeakHourItem(
                            hour=int(item.get("hour", 0)),
                            count=int(item.get("count", 0)),
                            label=str(item.get("label", "")),
                        ))

            # 运营建议
            raw_suggestions = ai_raw.get("suggestions", [])
            if isinstance(raw_suggestions, list):
                suggestions = [str(s) for s in raw_suggestions if s]
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse LLM output fields: %s, falling back to DB-only stats", e)
            hot_questions.clear()
            peak_hours_from_ai.clear()
            unanswered_questions.clear()
            suggestions.clear()
            llm_analysis = False
            satisfaction_direction = "平稳"
            satisfaction_summary = "AI 分析结果解析异常，已回退到数据库统计"

    # 如果 LLM 热门问题为空，回退到 DB 统计
    if not hot_questions:
        hot_questions = await _compute_hot_questions_from_db(db, days)

    # 高峰时段：优先使用 DB 精确统计，AI 版本作为补充
    db_peak_hours = await _compute_peak_hours(db, days)
    if db_peak_hours:
        peak_hours = db_peak_hours
    else:
        peak_hours = peak_hours_from_ai

    # 构建满意度趋势（简化：使用现有 dashboard 逻辑）
    satisfaction_trend = await _compute_satisfaction_trend(db, days)

    # LLM 失败/未配置/输出不可解析 → DB 统计兜底（产出确定性摘要，不阻断）
    if not llm_analysis:
        (
            satisfaction_direction,
            satisfaction_summary,
            suggestions,
        ) = _build_db_fallback_summary(
            satisfaction_trend, hot_questions, peak_hours, eval_summary
        )
        logger.info(
            "LLM analysis unavailable, using DB-only stats fallback "
            "(direction=%s, hot=%d, peaks=%d)",
            satisfaction_direction, len(hot_questions), len(peak_hours),
        )

    mode_note = "" if llm_analysis else "（数据库统计模式，LLM 分析不可用）"
    return AIOverviewResponse(
        hot_questions=hot_questions[:10],
        satisfaction_trend=satisfaction_trend,
        satisfaction_direction=satisfaction_direction,
        satisfaction_summary=satisfaction_summary,
        unanswered_questions=unanswered_questions[:5],
        peak_hours=peak_hours[:12],
        suggestions=suggestions,
        ai_summary=(
            f"共分析 {total_dialogues} 条对话记录，"
            f"覆盖 {len(eval_summary.get('daily_counts', {}))} 天数据。"
            f"满意度趋势：{satisfaction_direction}。"
            f"识别到 {len(hot_questions)} 个热门问题、"
            f"{len(unanswered_questions)} 个待优化问题。{mode_note}"
        ),
        total_dialogues=total_dialogues,
        llm_analysis=llm_analysis,
        analysis_timestamp=datetime.now(timezone.utc).isoformat(),
    )


async def _compute_satisfaction_trend(
    db: AsyncSession, days: int
) -> List[SatisfactionTrendItem]:
    """计算每日满意度均分趋势。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.date(Dialogue.timestamp).label("day"),
            func.avg(Evaluation.satisfaction_score).label("avg_score"),
            func.count(Evaluation.id).label("cnt"),
        )
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Dialogue.timestamp >= since,
            Evaluation.satisfaction_score.isnot(None),
        )
        .group_by(func.date(Dialogue.timestamp))
        .order_by(func.date(Dialogue.timestamp))
    )

    trends = []
    for row in result.all():
        score = round(float(row.avg_score), 2) if row.avg_score else 0.0
        trends.append(SatisfactionTrendItem(
            date=str(row.day),
            avg_score=score,
            count=row.cnt,
        ))

    return trends


async def get_trends(
    db: AsyncSession, days: int = 30
) -> TrendsResponse:
    """返回趋势数据：每日对话量、每日满意度均分。"""
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    # 每日对话量
    daily_result = await db.execute(
        select(
            func.date(Dialogue.timestamp).label("day"),
            func.count(Dialogue.id).label("cnt"),
        )
        .where(Dialogue.timestamp >= since)
        .group_by(func.date(Dialogue.timestamp))
        .order_by(func.date(Dialogue.timestamp))
    )
    daily_conversations = [
        DailyCountItem(date=str(row.day), count=row.cnt)
        for row in daily_result.all()
    ]

    # 每日满意度均分
    score_result = await db.execute(
        select(
            func.date(Dialogue.timestamp).label("day"),
            func.avg(Evaluation.satisfaction_score).label("avg_score"),
        )
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Dialogue.timestamp >= since,
            Evaluation.satisfaction_score.isnot(None),
        )
        .group_by(func.date(Dialogue.timestamp))
        .order_by(func.date(Dialogue.timestamp))
    )
    daily_satisfaction = [
        DailyScoreItem(
            date=str(row.day),
            avg_score=round(float(row.avg_score), 2) if row.avg_score else 0.0,
        )
        for row in score_result.all()
    ]

    return TrendsResponse(
        daily_conversations=daily_conversations,
        daily_satisfaction=daily_satisfaction,
    )
