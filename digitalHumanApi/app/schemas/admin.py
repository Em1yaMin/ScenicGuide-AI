from typing import Dict, List

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    """管理员登录请求（账号密码来自 ADMIN_USERNAME / ADMIN_PASSWORD 环境变量）。"""

    username: str = Field(..., description="管理员账号")
    password: str = Field(..., description="管理员密码")


class AdminLoginResponse(BaseModel):
    """管理员登录成功响应：JWT（typ=admin，sub=账号）+ 账号。"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="token 有效期（秒）")
    username: str


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
