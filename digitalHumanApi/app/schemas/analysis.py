from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnalysisResultItem(BaseModel):
    dialogue_id: int
    sentiment_label: Optional[str] = None
    satisfaction_score: Optional[int] = None
    concern_tags: Optional[List[str]] = None
    mentioned_spots: Optional[List[str]] = None
    raw_result: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


# ── AI 运营建议 ──

class HotQuestionItem(BaseModel):
    """热门问题项"""
    question: str
    count: int = 0
    category: str = ""


class SatisfactionTrendItem(BaseModel):
    """满意度趋势每日数据"""
    date: str
    avg_score: float = 0.0
    count: int = 0


class UnansweredQuestionItem(BaseModel):
    """未回答好的问题"""
    question: str
    reason: str = ""


class PeakHourItem(BaseModel):
    """高峰时段项"""
    hour: int = 0
    count: int = 0
    label: str = ""


class AIOverviewResponse(BaseModel):
    """AI 运营概览响应"""
    hot_questions: List[HotQuestionItem] = []
    satisfaction_trend: List[SatisfactionTrendItem] = []
    satisfaction_direction: str = "平稳"    # 上升/下降/平稳
    satisfaction_summary: str = ""
    unanswered_questions: List[UnansweredQuestionItem] = []
    peak_hours: List[PeakHourItem] = []
    suggestions: List[str] = []
    ai_summary: str = ""
    total_dialogues: int = 0
    # 降级标记：true=LLM 分析产出结果；false=回退到纯 DB 统计（LLM 未配置/失败/解析失败）
    # 供前端/管理后台识别当前报告的数据来源
    llm_analysis: bool = False
    analysis_timestamp: str = ""
    # 缓存命中标记：true=本次响应来自进程内 TTL 缓存（analysis_timestamp 为实际分析时间，非本次请求时间）
    cached: bool = False

    model_config = {"from_attributes": True}


# ── 趋势 API ──

class DailyCountItem(BaseModel):
    """每日数量"""
    date: str
    count: int = 0


class DailyScoreItem(BaseModel):
    """每日评分"""
    date: str
    avg_score: float = 0.0


class TrendsResponse(BaseModel):
    """趋势数据响应"""
    daily_conversations: List[DailyCountItem] = []
    daily_satisfaction: List[DailyScoreItem] = []

    model_config = {"from_attributes": True}
