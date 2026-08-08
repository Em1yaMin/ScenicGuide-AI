/**
 * 管理端 LLM 用量报表类型（对齐后端 PR #54 /api/admin/usage 契约，
 * 权威文档见 digitalHumanApi/doc/usage-tracking.md）。
 */

/** 趋势粒度：day=按日 / week=按周 / month=按月 */
export type UsageGranularity = 'day' | 'week' | 'month'

/** 时间序列趋势点（按粒度聚合，bucket 为桶标签如 2026-08-01 / 2026-W33 / 2026-08） */
export interface UsageTrendItem {
  bucket: string
  calls: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  avg_duration_ms: number
  cost: number | null // 估算费用（元）；未配置单价表为 null
}

/** 按模型/按链路的聚合行（后端按 total_tokens 降序返回） */
export interface UsageGroupItem {
  name: string
  calls: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  avg_duration_ms: number
  cost: number | null
}

/** 总览合计 */
export interface UsageTotals {
  calls: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  avg_duration_ms: number
  cost: number | null
}

/** 用量总览响应（趋势序列 + 按模型/按链路聚合 + 合计） */
export interface UsageOverviewResponse {
  start: string // 实际生效的时间范围起点（ISO）
  end: string
  granularity: UsageGranularity
  totals: UsageTotals
  series: UsageTrendItem[]
  by_model: UsageGroupItem[]
  by_chain: UsageGroupItem[]
}

/** 单条用量记录（明细查询） */
export interface UsageDetailItem {
  id: number
  model: string
  input_tokens: number
  output_tokens: number
  total_tokens: number
  duration_ms: number
  chain: string
  user: string | null // 后台任务（如量化评估）为 null
  cost: number | null
  created_at: string // UTC naive ISO datetime（后端存储口径）
}

/** 用量明细分页响应（created_at 倒序） */
export interface UsageDetailResponse {
  total: number
  page: number
  page_size: number
  items: UsageDetailItem[]
}

/** overview 查询参数（时间范围 + 趋势粒度） */
export interface UsageOverviewParams {
  start?: string // ISO 日期或 datetime（含本地时区偏移）
  end?: string
  granularity?: UsageGranularity
}

/** 明细/导出查询参数（时间范围 + 模型/链路/用户筛选 + 分页） */
export interface UsageDetailsParams extends UsageOverviewParams {
  model?: string
  chain?: string
  user?: string
  page?: number
  page_size?: number
}
