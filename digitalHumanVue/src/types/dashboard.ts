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

// ── AI 运营建议 ──

export interface HotQuestionItem {
  question: string
  count: number
  category: string
}

export interface SatisfactionTrendItem {
  date: string
  avg_score: number
  count: number
}

export interface UnansweredQuestionItem {
  question: string
  reason: string
}

export interface PeakHourItem {
  hour: number
  count: number
  label: string
}

export interface AIOverviewResponse {
  hot_questions: HotQuestionItem[]
  satisfaction_trend: SatisfactionTrendItem[]
  satisfaction_direction: string
  satisfaction_summary: string
  unanswered_questions: UnansweredQuestionItem[]
  peak_hours: PeakHourItem[]
  suggestions: string[]
  ai_summary: string
  total_dialogues: number
  analysis_timestamp: string
}

export interface DailyCountItem {
  date: string
  count: number
}

export interface DailyScoreItem {
  date: string
  avg_score: number
}

export interface TrendsResponse {
  daily_conversations: DailyCountItem[]
  daily_satisfaction: DailyScoreItem[]
}
