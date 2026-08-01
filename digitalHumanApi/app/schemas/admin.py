from typing import Dict, List

from pydantic import BaseModel


class SentimentTrendItem(BaseModel):
    date: str
    positive: int
    negative: int
    neutral: int


class HotIssueItem(BaseModel):
    concern: str
    count: int


class SpotHeatmapItem(BaseModel):
    spot_name: str
    mention_count: int


class DashboardResponse(BaseModel):
    total_conversations: int
    sentiment_trend: List[SentimentTrendItem]
    satisfaction_distribution: Dict[str, int]
    hot_issues: List[HotIssueItem]
    spot_heatmap: List[SpotHeatmapItem]

    model_config = {"from_attributes": True}


class SentimentPieItem(BaseModel):
    label: str
    count: int


class ConcernRankItem(BaseModel):
    concern: str
    count: int


class ReportResponse(BaseModel):
    sentiment_stats: List[SentimentPieItem]
    concern_ranking: List[ConcernRankItem]
    suggestions: List[str]

    model_config = {"from_attributes": True}
