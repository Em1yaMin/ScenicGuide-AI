export interface SentimentTrendItem {
  date: string
  positive: number
  negative: number
  neutral: number
}

export interface HotIssueItem {
  concern: string
  count: number
}

export interface SpotHeatmapItem {
  spot_name: string
  mention_count: number
}

export interface DashboardResponse {
  sentiment_trend: SentimentTrendItem[]
  satisfaction_distribution: Record<string, number>
  hot_issues: HotIssueItem[]
  spot_heatmap: SpotHeatmapItem[]
}
