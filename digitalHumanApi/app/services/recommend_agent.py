"""个性化推荐 Agent：基于 LangChain Tool Calling 模式，根据游客偏好推荐游览路线。

纯本地执行：工具读取本地 JSON 数据源，Agent 使用规则引擎 + 关键词匹配
进行推理和推荐，不依赖任何外部 API。
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool

from app.schemas.recommend import RecommendResponse, RouteRecommendation, SpotInfo
from app.services.rag_service import _search_documents_sync

logger = logging.getLogger(__name__)

# ── 数据加载 ──────────────────────────────────────────────────────────────

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_json(filename: str) -> List[Dict[str, Any]]:
    """从 app/data/ 加载 JSON 数据文件。"""
    path = _DATA_DIR / filename
    if not path.exists():
        logger.warning("数据文件不存在: %s", path)
        return []
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            logger.exception("数据文件 JSON 解析失败: %s", path)
            return []


# ── LangChain Tools ───────────────────────────────────────────────────────


# 问题式 query 常见疑问/停用词（搜索降级用）：
# 去词后为空串表示「泛查询」意图（如「有哪些值得去的景点」），此时返回全部数据；
# 去词后仍为非空且无命中表示具体名称/关键词确实不存在（如「黄鹤楼」），返回未找到。
_QUERY_STOPWORDS = (
    # 疑问词
    "有哪些", "有什么", "什么", "哪些", "哪里", "哪儿",
    "值得", "推荐", "建议",
    "想去", "想看", "想玩", "可以去", "可以", "能",
    "怎么玩", "怎么逛", "怎么", "如何", "怎么样", "怎样",
    "介绍一下", "介绍", "看看", "适合", "好玩", "好看",
    "规划", "安排", "计划", "制定", "帮忙",
    # 口语/礼貌词
    "帮我", "请问", "一下", "一条", "一个",
    # 领域泛指词
    "景区", "景点", "路线", "线路",
    "游览", "参观", "逛逛", "逛", "游玩", "去",
    # 语气词
    "吧", "呢", "吗", "呀", "啊", "哦", "了", "的",
)


def _strip_query_stopwords(query: str) -> str:
    """去掉 query 中的常见疑问/停用词，返回剩余查询串（可能为空串）。

    词表按长度降序替换，避免子词抢先命中破坏长词（如先去掉「有哪些」
    再处理「哪些」，避免「有哪些」被拆成「有」+「哪些」）。"""
    stripped = query.lower()
    for word in sorted(_QUERY_STOPWORDS, key=len, reverse=True):
        stripped = stripped.replace(word, "")
    return stripped.strip()


def _search_items(items: List[Dict[str, Any]], query: str) -> Optional[List[Dict[str, Any]]]:
    """按关键词搜索条目列表，含问题式 query 降级。

    匹配策略：
    1. 空 query → 返回全部数据；
    2. 整串子串匹配（现状，名称/标签/ID 精确匹配行为不变）；
    3. 无命中 → 去掉常见疑问/停用词后再匹配；
    4. 去词后为空串（纯泛查询，如「有哪些值得去的景点」）→ 返回全部数据，
       让 agent 自行筛选；
    5. 去词后仍非空且无命中（具体名称确实不存在，如「黄鹤楼」）→ 返回 None
       表示未找到，调用方构造「未找到」消息。
    """
    if not query:
        return items

    query_lower = query.lower()
    results = [
        item for item in items
        if query_lower in json.dumps(item, ensure_ascii=False).lower()
    ]
    if results:
        return results

    # 问题式 query 降级：去掉常见疑问/停用词后再匹配
    stripped = _strip_query_stopwords(query)
    if stripped:
        results = [
            item for item in items
            if stripped in json.dumps(item, ensure_ascii=False).lower()
        ]
        if results:
            return results
        # 具体名称/关键词确实不存在
        return None
    # 去词后为空串 → 泛查询意图，返回全部数据让 agent 自行筛选
    return items


@tool
def search_spots(query: str) -> str:
    """根据关键词搜索景点信息。query 可以是景点名称、标签（如'自然风光''亲子'）
    或位置（如'北部''中部'），支持模糊匹配。返回匹配景点的 JSON 列表，
    包含 id、名称、描述、标签、时长、难度等信息。问题式 query（如
    '有哪些值得去的景点'）无命中时自动降级返回全部景点。"""
    spots = _load_json("spots.json")
    results = _search_items(spots, query)
    if results is None:
        return json.dumps({"message": f"未找到与'{query}'匹配的景点"}, ensure_ascii=False)
    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def search_routes(query: str) -> str:
    """根据关键词搜索游览路线。query 可以是路线名称、标签（如'亲子''自然风光'）、
    适合人群（如'带老人''情侣'）或难度（如'轻松''挑战'），支持模糊匹配。
    返回匹配路线的 JSON 列表，包含 id、名称、描述、标签、适合人群、时长等信息。
    问题式 query（如'有什么路线'）无命中时自动降级返回全部路线。"""
    routes = _load_json("routes.json")
    results = _search_items(routes, query)
    if results is None:
        return json.dumps({"message": f"未找到与'{query}'匹配的路线"}, ensure_ascii=False)
    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def get_route_detail(route_id: str) -> str:
    """根据路线 ID 获取路线详细信息，包括完整的景点列表和各景点详情。
    route_id 形如 'route_001'。"""
    routes = _load_json("routes.json")
    spots = _load_json("spots.json")
    spots_by_id = {s["id"]: s for s in spots}

    for route in routes:
        if route["id"] == route_id:
            detail = dict(route)
            detail["spots_detail"] = [
                spots_by_id.get(sid, {"id": sid, "name": "未知景点"})
                for sid in route.get("spot_ids", [])
            ]
            return json.dumps(detail, ensure_ascii=False, indent=2)
    return json.dumps({"message": f"未找到路线: {route_id}"}, ensure_ascii=False)


@tool
def get_spot_detail(spot_id: str) -> str:
    """根据景点 ID 获取景点详细信息，包括亮点和游玩建议。
    spot_id 形如 'spot_001'。"""
    spots = _load_json("spots.json")
    for spot in spots:
        if spot["id"] == spot_id:
            return json.dumps(spot, ensure_ascii=False, indent=2)
    return json.dumps({"message": f"未找到景点: {spot_id}"}, ensure_ascii=False)


@tool
def filter_by_tags(tags: str) -> str:
    """根据标签筛选路线。tags 为逗号分隔的标签列表，如 '亲子,轻松,半日游'。
    匹配任一标签即返回。返回匹配路线的 JSON 列表。"""
    routes = _load_json("routes.json")
    tag_list = [t.strip().lower() for t in tags.split(",") if t.strip()]
    if not tag_list:
        return json.dumps(routes, ensure_ascii=False, indent=2)

    results = []
    for route in routes:
        route_tags = [t.lower() for t in route.get("tags", [])]
        route_suitable = [s.lower() for s in route.get("suitable_for", [])]
        route_all = route_tags + route_suitable
        if any(tag in route_all for tag in tag_list):
            results.append(route)
    if not results:
        return json.dumps({"message": f"没有匹配标签'{tags}'的路线"}, ensure_ascii=False)
    return json.dumps(results, ensure_ascii=False, indent=2)


@tool
def search_knowledge(query: str, top_k: int = 3) -> str:
    """检索本地知识库（讲解词/FAQ/景区资料），返回与 query 最相关的知识片段 JSON 列表。
    每条含 content、source、score、namespace 字段。
    用于补充结构化景点/路线数据之外的人文典故、开放时间等背景知识。"""
    results = _search_documents_sync(query=query, top_k=top_k)
    return json.dumps(results, ensure_ascii=False, indent=2)


# ── 偏好分析（规则引擎）───────────────────────────────────────────────────


# 关键词 → (标签/人群映射, 权重)
_KEYWORD_MAP: Dict[str, Tuple[List[str], int]] = {
    # 自然风光类
    "自然": (["自然风光"], 10),
    "风光": (["自然风光"], 10),
    "山水": (["自然风光"], 10),
    "风景": (["自然风光"], 8),
    "自然风光": (["自然风光"], 10),
    # 历史人文类
    "历史": (["历史人文"], 10),
    "文化": (["历史人文", "文化体验"], 10),
    "古迹": (["历史人文"], 10),
    "人文": (["历史人文"], 10),
    "古寺": (["历史人文"], 9),
    "寺庙": (["历史人文"], 9),
    # 亲子类
    "小孩": (["亲子", "带小孩"], 10),
    "孩子": (["亲子", "带小孩"], 10),
    "亲子": (["亲子", "带小孩"], 10),
    "宝宝": (["亲子", "带小孩"], 10),
    "儿童": (["亲子", "带小孩"], 10),
    "家庭": (["亲子", "亲子家庭", "全家出游"], 9),
    "全家": (["亲子", "全家出游"], 10),
    # 老人/银发
    "老人": (["老年人", "带老人", "轻松"], 10),
    "爸妈": (["老年人", "带老人", "轻松"], 10),
    "父母": (["老年人", "带老人", "轻松"], 10),
    "长辈": (["老年人", "带老人", "轻松"], 10),
    "银发": (["老年人", "轻松"], 10),
    "年纪大": (["老年人", "轻松"], 9),
    # 拍照/摄影
    "拍照": (["拍照打卡", "摄影"], 10),
    "摄影": (["拍照打卡", "摄影"], 10),
    "打卡": (["拍照打卡"], 10),
    "出片": (["拍照打卡", "摄影"], 10),
    "相机": (["拍照打卡", "摄影"], 9),
    "网红": (["拍照打卡", "网红打卡"], 10),
    # 休闲/轻松
    "休闲": (["休闲", "轻松"], 10),
    "轻松": (["休闲", "轻松"], 10),
    "慢": (["休闲", "轻松", "养生"], 8),
    "不累": (["轻松"], 10),
    "舒服": (["休闲", "轻松"], 8),
    # 情侣/浪漫
    "情侣": (["情侣", "浪漫"], 10),
    "浪漫": (["情侣", "浪漫"], 10),
    "约会": (["情侣", "浪漫"], 9),
    "恋人": (["情侣", "浪漫"], 10),
    "夫妻": (["情侣", "夫妻"], 9),
    # 冒险/挑战
    "刺激": (["冒险", "刺激体验", "挑战"], 10),
    "冒险": (["冒险", "挑战"], 10),
    "挑战": (["挑战"], 10),
    "户外": (["户外达人", "登山", "徒步"], 9),
    "登山": (["登山", "挑战"], 10),
    "徒步": (["徒步", "户外达人"], 10),
    "运动": (["运动爱好者", "户外达人"], 9),
    # 养生
    "养生": (["养生", "轻松", "休闲"], 10),
    "健康": (["养生", "轻松"], 8),
    # 一日游/半日游
    "一天": (["一日游"], 8),
    "一日游": (["一日游"], 10),
    "半天": (["半日游"], 10),
    "半日游": (["半日游"], 10),
    "周末": (["半日游", "亲子", "休闲"], 7),
    # 美食
    "美食": (["美食", "文化体验"], 9),
    "吃": (["美食"], 7),
}


def _extract_keywords(preference: str) -> Dict[str, int]:
    """从偏好文本中提取关键词并计算标签权重。

    返回 {tag: weight} 映射，weight 越高优先级越高。
    """
    tag_scores: Dict[str, int] = {}
    for keyword, (tags, weight) in _KEYWORD_MAP.items():
        if keyword.lower() in preference.lower():
            for tag in tags:
                tag_scores[tag] = max(tag_scores.get(tag, 0), weight)
    return tag_scores


# ── 推荐 Agent ────────────────────────────────────────────────────────────


def _score_route(route: Dict[str, Any], tag_scores: Dict[str, int]) -> int:
    """根据标签权重计算路线匹配分（0-100）。"""
    score = 0
    route_tags = [t.lower() for t in route.get("tags", [])]
    route_suitable = [s.lower() for s in route.get("suitable_for", [])]
    route_all = set(route_tags + route_suitable)

    for tag, weight in tag_scores.items():
        if tag.lower() in route_all:
            score += weight * 2

    # 归一化到 0-100
    max_possible = sum(tag_scores.values()) * 2 if tag_scores else 1
    normalized = min(100, int(score / max_possible * 100))
    return normalized


def _build_spot_info(spot: Dict[str, Any]) -> SpotInfo:
    """将原始景点数据转为 SpotInfo schema。"""
    return SpotInfo(
        id=spot["id"],
        name=spot["name"],
        description=spot["description"],
        tags=spot.get("tags", []),
        duration_minutes=spot.get("duration_minutes", 0),
        difficulty=spot.get("difficulty", "未知"),
    )


def _generate_summary(
    preference: str,
    recommendations: List[RouteRecommendation],
    tag_scores: Dict[str, int],
) -> str:
    """生成综合推荐说明。"""
    if not recommendations:
        return f"根据您「{preference}」的偏好，暂未找到完全匹配的路线。建议您调整偏好描述或联系客服获取人工推荐。"

    top = recommendations[0]
    top_tags = ", ".join(tag_scores.keys()) if tag_scores else "综合"
    return (
        f"根据您「{preference}」的偏好，我们重点匹配了{top_tags}等维度的需求，"
        f"为您精选了{len(recommendations)}条路线。"
        f"首推「{top.route_name}」——{top.recommend_reason}。"
    )


async def recommend(preference: str) -> RecommendResponse:
    """根据游客偏好描述推荐游览路线。

    执行流程：
    1. 关键词提取 → 标签权重
    2. 调用 filter_by_tags 工具筛选候选路线
    3. 逐路线评分排序
    4. 构建推荐响应

    Args:
        preference: 游客偏好描述，如 "喜欢自然风光，带老人小孩"

    Returns:
        RecommendResponse 包含排序后的路线推荐列表和综合说明
    """
    if not preference or not preference.strip():
        return RecommendResponse(
            preference="",
            recommendations=[],
            summary="请描述您的游览偏好，例如：喜欢自然风光、带小孩、想拍照打卡等。",
            total=0,
        )

    # Step 1: 提取关键词和标签权重
    tag_scores = _extract_keywords(preference)
    logger.debug("偏好分析 - 标签权重: %s", tag_scores)

    # Step 2: 决定调用哪些工具
    # 先通过标签筛选路线
    if tag_scores:
        tags_str = ", ".join(tag_scores.keys())
        routes_json = filter_by_tags.invoke({"tags": tags_str})
    else:
        # 无匹配关键词时用原始偏好文本搜索
        routes_json = search_routes.invoke({"query": preference})

    try:
        routes_data = json.loads(routes_json)
    except json.JSONDecodeError:
        logger.warning("工具返回无效 JSON")
        routes_data = []

    if isinstance(routes_data, dict) and "message" in routes_data:
        # 无匹配结果，尝试全量搜索
        routes_json = search_routes.invoke({"query": ""})
        try:
            routes_data = json.loads(routes_json)
        except json.JSONDecodeError:
            routes_data = []

    if not isinstance(routes_data, list):
        routes_data = []

    # Step 3: 评分排序
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for route in routes_data:
        score = _score_route(route, tag_scores)
        scored.append((score, route))

    scored.sort(key=lambda x: x[1].get("id", ""))  # 稳定排序
    scored.sort(key=lambda x: x[0], reverse=True)

    if scored and all(s[0] == 0 for s in scored):
        logger.info("推荐完成: preference='%s', 无有效匹配路线", preference)
        return RecommendResponse(
            preference=preference,
            recommendations=[],
            summary=_generate_summary(preference, [], tag_scores),
            total=0,
        )

    # Step 4: 构建响应
    spots_data = _load_json("spots.json")
    spots_by_id = {s["id"]: s for s in spots_data}

    recommendations: List[RouteRecommendation] = []
    for score, route in scored[:5]:  # 最多返回 Top 5
        spot_infos = [
            _build_spot_info(spots_by_id[sid])
            for sid in route.get("spot_ids", [])
            if sid in spots_by_id
        ]
        recommendations.append(
            RouteRecommendation(
                route_id=route["id"],
                route_name=route["name"],
                description=route["description"],
                spots=spot_infos,
                tags=route.get("tags", []),
                suitable_for=route.get("suitable_for", []),
                total_duration_minutes=route.get("total_duration_minutes", 0),
                difficulty=route.get("difficulty", "未知"),
                recommend_reason=route.get("recommend_reason", ""),
                match_score=score,
            )
        )

    summary = _generate_summary(preference, recommendations, tag_scores)

    logger.info(
        "推荐完成: preference='%s', 匹配 %d 条路线, Top1=%s (score=%d)",
        preference,
        len(recommendations),
        recommendations[0].route_name if recommendations else "无",
        recommendations[0].match_score if recommendations else 0,
    )

    return RecommendResponse(
        preference=preference,
        recommendations=recommendations,
        summary=summary,
        total=len(recommendations),
    )


# ── 工具函数（供对话流程集成使用）─────────────────────────────────────────


def detect_tour_preference(text: str) -> Optional[str]:
    """检测文本中是否包含游览偏好表达。

    通过关键词匹配判断用户是否在表达游览需求。
    返回提取到的偏好描述文本，若未检测到则返回 None。

    用于在对话流程中自动触发推荐。
    """
    trigger_keywords = [
        "推荐", "路线", "游览", "游玩", "想去", "想看",
        "喜欢", "偏好", "怎么逛", "怎么玩", "有什么", "哪些",
        "景点", "好玩", "好看", "适合", "建议", "规划",
        "一日游", "半日游", "带老人", "带孩子", "带娃", "带小孩",
        "老人", "小孩", "拍照",
    ]
    # 需要至少包含 2 个触发词才认为表达了游览偏好
    matched = [kw for kw in trigger_keywords if kw in text]
    if len(matched) >= 2:
        return text
    return None
