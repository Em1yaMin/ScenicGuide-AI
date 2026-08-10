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

无评估数据降级（2026-08 船长反馈：调试/演示环境空库时推荐问句接口返回空，
对话页浮层只剩空态提示无可点击问句）：`_aggregate_dashboard_7d` 在 7 天窗口
内无评估数据（concern_tags/mentioned_spots 均空）时返回 None，`fetch_`
改为降级基于本地景点/路线数据（app/data/spots.json、routes.json）模板构造
推荐问句（不依赖 LLM），保证演示环境空库也有可点击问句；本地数据也缺失时
才返回 None（保持 None 契约，调用方保留旧缓存）。有评估数据时主路径行为不变。
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
from app.services.home_service import _load_json
from app.services.usage_tracker import CHAIN_RECOMMEND, usage_context

logger = logging.getLogger(__name__)

_RECOMMEND_MAX_TOKENS = 512
_RECOMMEND_TIMEOUT = 60
_RECOMMEND_TEMPERATURE = 0.5

# 无评估数据降级生成的推荐问句模板（本地景点/路线数据构造，不依赖 LLM）
_FALLBACK_MAX_QUESTIONS = 8
_FALLBACK_MIN_QUESTIONS = 3
_FALLBACK_SPOT_LIMIT = 3
_SPOT_QUESTION_TEMPLATE = "{name}有什么特色？"
_FALLBACK_GENERIC_QUESTIONS = [
    "有哪些值得去的景点？",
    "推荐一条经典游玩路线",
]
# 路线标签 → 推荐问句（顺序即优先级；任一标签命中即产出该问句）
_FALLBACK_ROUTE_TAG_QUESTIONS = [
    (("半日游",), "帮我规划一条半日游路线"),
    (("一日游",), "帮我规划一条一日游路线"),
    (("拍照打卡", "摄影"), "适合拍照的路线推荐"),
    (("亲子",), "带小孩适合走哪条路线？"),
    (("浪漫", "情侣"), "有什么适合情侣的路线？"),
    (("休闲", "养生"), "推荐一条休闲放松的路线"),
    (("登山", "徒步", "挑战"), "想爬山走哪条路线好？"),
]


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


def _invoke_recommend_llm(llm: Any, messages: List[Any]) -> Any:
    """推荐问句生成 LLM 调用（线程内执行，标注记账链路）。"""
    with usage_context(CHAIN_RECOMMEND):
        return llm.invoke(messages)


def _build_fallback_questions(
    spots: Optional[List[Dict[str, Any]]] = None,
    routes: Optional[List[Dict[str, Any]]] = None,
    max_questions: int = _FALLBACK_MAX_QUESTIONS,
) -> Optional[List[str]]:
    """无评估数据时降级生成推荐问句（本地景点/路线数据模板构造，不依赖 LLM）。

    空库/新部署环境没有 Evaluation 数据时，直接基于 app/data/ 的景点/路线
    数据构造通用推荐问句（如「翠微峰有什么特色？」「帮我规划一条半日游路线」
    「适合拍照的路线推荐」），保证演示环境对话页底部浮层始终有可点击问句、
    不显示空态。

    Args:
        spots/routes: 可注入的景点/路线数据（测试用）；默认从 app/data/ 读取
        max_questions: 问句条数上限（默认 8）

    Returns:
        非空问句列表（兜底通用问句 → 景点问句 → 路线标签问句，去重、上限截断）；
        本地数据缺失/为空（连 _FALLBACK_MIN_QUESTIONS 条通用问句都凑不齐）时
        返回 None（保持 None 契约，调用方走 stale-while-revalidate）。
    """
    if spots is None:
        spots = _load_json("spots.json")
    if routes is None:
        routes = _load_json("routes.json")

    questions: List[str] = []
    seen: set = set()

    def _add(q: str) -> None:
        if len(questions) >= max_questions or not q or q in seen:
            return
        questions.append(q)
        seen.add(q)

    # 1. 兜底通用问句（优先保证基础可点问句始终存在）
    for question in _FALLBACK_GENERIC_QUESTIONS:
        _add(question)

    # 2. 景点问句（取前 _FALLBACK_SPOT_LIMIT 个热门景点，问句与真实数据相关）
    for spot in spots[:_FALLBACK_SPOT_LIMIT]:
        name = (spot or {}).get("name") if isinstance(spot, dict) else None
        if name:
            _add(_SPOT_QUESTION_TEMPLATE.format(name=name))

    # 3. 路线标签问句（命中任一标签即产出对应问句，覆盖游玩攻略维度）
    route_tags = {
        tag
        for route in routes
        if isinstance(route, dict)
        for tag in (route.get("tags") or [])
    }
    for tag_set, question in _FALLBACK_ROUTE_TAG_QUESTIONS:
        if route_tags.intersection(tag_set):
            _add(question)

    if len(questions) < _FALLBACK_MIN_QUESTIONS:
        logger.debug(
            "降级推荐问句不足（spots=%d routes=%d），放弃生成",
            len(spots),
            len(routes),
        )
        return None
    return questions


async def fetch_recommended_questions(
    llm: Optional[Any] = None,
) -> Optional[List[str]]:
    """Full cycle: aggregate 7-day data → local LLM generate → parse → questions.

    Args:
        llm: 可注入的 ChatOpenAI 实例（测试用）；默认走 llm.py 工厂
            （get_chat_llm，LLM_API_* 配置 opencode-go，max_tokens 经 extra_body 注入）

    Returns:
        List of question strings on success, None on any failure (LLM unconfigured,
        LLM exception, empty output, parse error, or no evaluation data AND local
        scenic data missing). Caller implements stale-while-revalidate.

    无评估数据（_aggregate_dashboard_7d 返回 None）时降级基于本地景点/路线数据
    模板生成推荐问句（不依赖 LLM），保证演示/调试环境空库不返回空；有评估数据
    时主路径行为不变（LLM 生成，失败仍返回 None 保留旧缓存）。
    """
    # Phase 1: Aggregate dashboard data
    async with async_session_factory() as db:
        inputs = await _aggregate_dashboard_7d(db)

    if inputs is None:
        # 无评估数据（空库/新部署）→ 降级生成：本地景点/路线数据模板构造，
        # 不依赖 LLM，保证演示环境浮层有可点击问句而非空态。
        fallback = _build_fallback_questions()
        if fallback:
            logger.info(
                "无评估数据，降级生成 %d 条推荐问句", len(fallback)
            )
            return fallback
        logger.debug("降级生成失败（本地景点/路线数据缺失），保持旧缓存")
        return None

    # Phase 2: Local LLM generation (app/core/llm.py factory, LLM_API_* config)
    try:
        llm = llm or get_chat_llm(
            max_tokens=_RECOMMEND_MAX_TOKENS,
            temperature=_RECOMMEND_TEMPERATURE,
            timeout=_RECOMMEND_TIMEOUT,
        )

        response = await asyncio.to_thread(
            _invoke_recommend_llm,
            llm,
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
