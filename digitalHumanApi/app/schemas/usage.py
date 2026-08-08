"""LLM token 用量报表（批次7 C5）的 Pydantic 请求/响应模型（管理端 X-Admin-Token 鉴权）。

契约权威文档：doc/usage-tracking.md；变更 schema 必须同步该文档。
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class UsageTrendItem(BaseModel):
    """时间序列趋势点（按粒度聚合）。"""

    bucket: str  # 粒度桶标签：日 YYYY-MM-DD / 周 YYYY-Www / 月 YYYY-MM
    calls: int = 0  # 调用次数
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    avg_duration_ms: int = 0  # 平均耗时（毫秒，无调用为 0）
    cost: Optional[float] = None  # 估算费用（元，未配置单价表为 None）


class UsageGroupItem(BaseModel):
    """按模型/按链路的聚合行（排序：total_tokens 降序）。"""

    name: str  # 模型名或链路名
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    avg_duration_ms: int = 0
    cost: Optional[float] = None


class UsageTotals(BaseModel):
    """总览合计。"""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    avg_duration_ms: int = 0
    cost: Optional[float] = None


class UsageOverviewResponse(BaseModel):
    """用量报表总览（趋势图 + 按模型/按链路明细，参考 GitHub 用量报表风格）。"""

    start: str  # 实际生效的时间范围起点（ISO）
    end: str  # 实际生效的时间范围终点（ISO）
    granularity: str  # day / week / month
    totals: UsageTotals
    series: List[UsageTrendItem] = Field(default_factory=list)  # 趋势序列
    by_model: List[UsageGroupItem] = Field(default_factory=list)  # 按模型聚合
    by_chain: List[UsageGroupItem] = Field(default_factory=list)  # 按链路聚合


class UsageDetailItem(BaseModel):
    """单条用量记录（明细查询）。"""

    id: int
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_ms: int
    chain: str
    user: Optional[str] = None
    cost: Optional[float] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UsageDetailResponse(BaseModel):
    """明细查询（分页）。"""

    total: int  # 筛选条件下的总条数（分页用）
    page: int
    page_size: int
    items: List[UsageDetailItem]
