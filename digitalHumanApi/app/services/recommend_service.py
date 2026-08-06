"""推荐问句服务：聚合 Dashboard 数据 + 本地 LLM 生成推荐问句。

阶段3 T3（船长决策 2026-08-06 方案B）：推荐问句生成**不再调用 Dify Workflow**，
改为本地 LLM（`app/core/llm.py` 工厂，`LLM_API_*` 配置 opencode-go）直接生成；
不留 Dify fallback（一步退场，为阶段4 Dify 全面退场铺路）。

输出契约不变：仍产出 questions 数组（前端消费格式不变，推荐问句语义不变：
景区相关可提问句）。输入保持聚合 Dashboard 数据（对话统计/热门景点等）作为
LLM 上下文，保证问句与真实数据相关。

失败降级：LLM 未配置（LLMConfigError）/调用异常/解析失败均返回 None（不抛错、
不阻断页面），由调用方（tasks/recommend_questions.refresh_cycle）保持
stale-while-revalidate 缓存（旧缓存兜底）。
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.llm import LLMConfigError, get_chat_llm
from app.models.dialogue import Dialogue
from app.models.evaluation import Evaluation

logger = logging.getLogger(__name__)

_RECOMMEND_MAX_TOKENS = 512
_RECOMMEND_TIMEOUT = 60
_RECOMMEND_TEMPERATURE = 0.5

# 推荐问句生成 System 提示词（本地 LLM 版，语义对齐原 Dify 推荐工作流，
# 见 doc/dify配置/recommend-workflow-推荐工作流.md——该文档现为历史存档）
_RECOMMEND_SYSTEM_PROMPT = """\
你是一个景区导览推荐问句生成专家。你的任务是根据景区近7天的游客关注热点数据，生成5-8条游客可能感兴趣的推荐问句，展示在数字人对话界面的引导区。

# 数据解读指南
你会收到以下4个维度的数据，请先理解数据含义再生成问句：
1. 热门关注点 (concern_tags)：JSON数组，每项含 tag(关注标签) 和 count(出现次数)，反映游客最近咨询的话题方向
2. 热门景点 (mentioned_spots)：JSON数组，每项含 spot(景点名称) 和 count(提及次数)，反映游客最感兴趣的景点
3. 情感汇总 (sentiment_summary)：格式 positive:N, negative:N, neutral:N，反映游客整体情绪分布
4. 总对话数 (total_dialogues)：7天内对话总量，仅作为生成数量参考

# 问句生成规则
1. 每个问句必须是游客能自然说出的口语短句，不是关键词堆砌或标题
2. 不能包含引导性前缀（禁止"请问""我想知道""帮我查一下""你能告诉我"等），直接给出问句主体
3. 只能使用疑问句或祈使请求句，不能是陈述句或命令句
4. 问句长度 5-20 字，适合语音输入和快速点击
5. 避免与 concern_tags 标签文字直接重复，用自然语言改写
6. 问句之间覆盖不同话题维度（景点介绍/实用信息/游玩攻略/服务互动）
7. 对话量很少（<10）时适当减少问句数量

# 输出格式
只输出一个 JSON 对象，不要有任何解释文字，不要包装在 ```json 或其他标记中。格式如下：
{"questions": ["问句1", "问句2", "问句3", "问句4", "问句5"]}
questions 数组长度 5-8 条（数据量很少时不少于 3 条），所有问句使用中文。
"""


def _parse_questions_output(text: Any) -> Optional[List[str]]:
    """解析 LLM 输出的 JSON 文本，提取 questions 数组（容错解析）。

    兼容三层防御（行为对齐旧 Dify 输出解析，已剥离 dify_helpers 依赖）：
    1. 剥离 <think>...</think> 推理标签
    2. 提取 ```json ... ``` 代码围栏内容
    3. json.loads 解码后取 questions 数组（别名 recommended_questions/suggestions 兜底）

    去重、去空白、过滤空串；任何失败返回 None（调用方降级，不抛错）。
    """
    if text is None:
        return None
    if isinstance(text, dict):
        parsed = text
    elif isinstance(text, str):
        cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        fence_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, flags=re.DOTALL
        )
        if fence_match:
            cleaned = fence_match.group(1).strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            logger.warning("推荐问句 LLM 输出非 JSON，忽略: %r", text[:200])
            return None
    else:
        return None

    if not isinstance(parsed, dict):
        return None
    questions = (
        parsed.get("questions")
        or parsed.get("recommended_questions")
        or parsed.get("suggestions")
    )
    if not isinstance(questions, list):
        return None
    cleaned: List[str] = []
    seen: set = set()
    for q in questions:
        if isinstance(q, str):
            stripped = q.strip()
            if stripped and stripped not in seen:
                cleaned.append(stripped)
                seen.add(stripped)
    return cleaned if cleaned else None


def _build_recommend_prompt(inputs: Dict[str, Any]) -> str:
    """构造推荐问句生成提示词（User 消息：4 个聚合维度数据）。"""
    return (
        "请根据以下景区近7天数据，生成推荐问句：\n\n"
        "热门关注点：\n"
        f"{inputs.get('concern_tags', '[]')}\n\n"
        "热门景点：\n"
        f"{inputs.get('mentioned_spots', '[]')}\n\n"
        "情感分布：\n"
        f"{inputs.get('sentiment_summary', '')}\n\n"
        f"7天总对话数：{inputs.get('total_dialogues', '0')}\n"
    )


async def _aggregate_dashboard_7d(db: AsyncSession) -> Optional[Dict[str, Any]]:
    """Aggregate 7-day dashboard data for LLM input.

    Returns:
        Dict with concern_tags (JSON str), mentioned_spots (JSON str),
        sentiment_summary (str), total_dialogues (int).
        Returns None if no evaluation data exists (skip LLM generation).
    """
    seven_days_ago = (
        datetime.now(timezone.utc).date() - timedelta(days=6)
    ).isoformat()

    # NOTE: func.date() prevents index usage on timestamp column.
    # For SQLite with small datasets this is negligible (~100 rows).
    # If data grows, rewrite as range query:
    #   Dialogue.timestamp >= seven_days_ago_start
    #   AND Dialogue.timestamp < today_start

    # 1. concern_tags — aggregate JSON arrays with 7-day filter
    concern_result = await db.execute(
        select(Evaluation.concern_tags)
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Evaluation.concern_tags.isnot(None),
            func.date(Dialogue.timestamp) >= seven_days_ago,
        )
    )
    concern_counter: Dict[str, int] = {}
    for row in concern_result.all():
        tags = row.concern_tags
        if isinstance(tags, list):
            for tag in tags:
                concern_counter[tag] = concern_counter.get(tag, 0) + 1
    concern_tags = [
        {"tag": tag, "count": count}
        for tag, count in sorted(
            concern_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # 2. mentioned_spots — aggregate JSON arrays with 7-day filter
    spot_result = await db.execute(
        select(Evaluation.mentioned_spots)
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Evaluation.mentioned_spots.isnot(None),
            func.date(Dialogue.timestamp) >= seven_days_ago,
        )
    )
    spot_counter: Dict[str, int] = {}
    for row in spot_result.all():
        spots = row.mentioned_spots
        if isinstance(spots, list):
            for spot in spots:
                spot_counter[spot] = spot_counter.get(spot, 0) + 1
    mentioned_spots = [
        {"spot": spot, "count": count}
        for spot, count in sorted(
            spot_counter.items(), key=lambda x: x[1], reverse=True
        )[:10]
    ]

    # 3. sentiment_summary — count labels in 7-day window
    sentiment_result = await db.execute(
        select(
            Evaluation.sentiment_label,
            func.count(Evaluation.id).label("cnt"),
        )
        .join(Dialogue, Evaluation.dialogue_id == Dialogue.id)
        .where(
            Evaluation.sentiment_label.isnot(None),
            func.date(Dialogue.timestamp) >= seven_days_ago,
        )
        .group_by(Evaluation.sentiment_label)
    )
    sentiment_map = {"positive": 0, "negative": 0, "neutral": 0}
    for row in sentiment_result.all():
        label = (row.sentiment_label or "").lower()
        if label in sentiment_map:
            sentiment_map[label] = row.cnt
    sentiment_summary = (
        f"positive:{sentiment_map['positive']}, "
        f"negative:{sentiment_map['negative']}, "
        f"neutral:{sentiment_map['neutral']}"
    )

    # 4. total_dialogues — count dialogues in 7-day window
    dialogue_count_result = await db.execute(
        select(func.count(Dialogue.id)).where(
            func.date(Dialogue.timestamp) >= seven_days_ago
        )
    )
    total_dialogues = dialogue_count_result.scalar() or 0

    # Safety: skip generation if no evaluation data exists.
    # We check evaluation-derived data (concern_tags + mentioned_spots) rather
    # than total_dialogues because polling_loop (30s) inserts dialogues before
    # analysis_loop (300s) runs evaluations — leading to total_dialogues>0 but
    # empty concern_tags/mentioned_spots, which would produce low-quality
    # recommendations from LLM.
    if not concern_tags and not mentioned_spots:
        logger.debug(
            "No evaluation data in 7-day window (dialogues=%d), skipping LLM generation",
            total_dialogues,
        )
        return None

    return {
        "concern_tags": json.dumps(concern_tags, ensure_ascii=True),
        "mentioned_spots": json.dumps(mentioned_spots, ensure_ascii=True),
        "sentiment_summary": sentiment_summary,
        "total_dialogues": str(total_dialogues),
    }


async def fetch_recommended_questions(
    llm: Optional[Any] = None,
) -> Optional[List[str]]:
    """Full cycle: aggregate 7-day data → local LLM generate → parse → questions.

    Args:
        llm: 可注入的 ChatOpenAI 实例（测试用）；默认走 llm.py 工厂
            （get_chat_llm，LLM_API_* 配置 opencode-go，max_tokens 经 extra_body 注入）

    Returns:
        List of question strings on success, None on any failure (no evaluation
        data, LLM unconfigured, LLM exception, empty output, parse error).
        Caller implements stale-while-revalidate.
    """
    # Phase 1: Aggregate dashboard data
    async with async_session_factory() as db:
        inputs = await _aggregate_dashboard_7d(db)

    if inputs is None:
        return None

    # Phase 2: Local LLM generation (app/core/llm.py factory, LLM_API_* config)
    try:
        llm = llm or get_chat_llm(
            max_tokens=_RECOMMEND_MAX_TOKENS,
            temperature=_RECOMMEND_TEMPERATURE,
            timeout=_RECOMMEND_TIMEOUT,
        )

        response = await asyncio.to_thread(
            llm.invoke,
            [
                SystemMessage(content=_RECOMMEND_SYSTEM_PROMPT),
                HumanMessage(content=_build_recommend_prompt(inputs)),
            ],
        )
        content = getattr(response, "content", response)
        if not isinstance(content, str):
            content = str(content or "")
        return _parse_questions_output(content)
    except LLMConfigError as e:
        logger.warning(
            "推荐问句生成：LLM 未配置（降级返回 None，保留旧缓存）: %s", e
        )
        return None
    except Exception as e:
        logger.warning(
            "推荐问句生成失败（降级返回 None，保留旧缓存）: %s", e
        )
        return None
