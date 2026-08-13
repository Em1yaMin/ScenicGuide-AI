<script setup lang="ts">
import { computed, onMounted } from 'vue'
import VChart from 'vue-echarts'
import { registerECharts } from '@/composables/useECharts'
import { useApi } from '@/composables/useApi'
import { API_ADMIN_REPORT } from '@/api'
import EmptyStateGuide from '@/components/EmptyStateGuide.vue'

registerECharts()

interface SentimentPieItem {
  label: string
  count: number
}

interface ConcernRankItem {
  concern: string
  count: number
}

interface ReportData {
  sentiment_stats: SentimentPieItem[]
  concern_ranking: ConcernRankItem[]
  suggestions: string[]
}

const { data, isLoading, error, execute: fetchReport } = useApi<ReportData>(API_ADMIN_REPORT)

const hasData = computed(
  () =>
    data.value !== null &&
    ((data.value?.sentiment_stats?.length ?? 0) > 0 ||
      (data.value?.concern_ranking?.length ?? 0) > 0 ||
      (data.value?.suggestions?.length ?? 0) > 0),
)

onMounted(() => {
  fetchReport()
})

const today = computed(() => {
  return new Date().toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
})

const dominantSentiment = computed(() => {
  const stats = data.value?.sentiment_stats ?? []
  if (stats.length === 0) return null
  const sorted = [...stats].sort((a, b) => b.count - a.count)
  return sorted[0]
})

const totalAnalyses = computed(() => {
  return (data.value?.sentiment_stats ?? []).reduce((sum, s) => sum + s.count, 0)
})

const topConcern = computed(() => {
  return (data.value?.concern_ranking ?? [])[0] ?? null
})

const SENTIMENT_COLORS: Record<string, string> = {
  positive: '#2F6B5B',
  negative: '#C1412F',
  neutral: '#C8D2CC',
}

// 后端枚举（positive/negative/neutral）仅存于数据层，展示层统一映射中文
// （与 DashboardView 大屏正面/负面/中性口径一致）；未知名保持原样
const SENTIMENT_LABELS: Record<string, string> = {
  positive: '正面',
  negative: '负面',
  neutral: '中性',
}

function sentimentLabel(label: string): string {
  return SENTIMENT_LABELS[label.toLowerCase()] ?? label
}

function sentimentColor(label: string): string {
  return SENTIMENT_COLORS[label.toLowerCase()] ?? '#9CB3A8'
}

const sentimentPieOption = computed(() => {
  return {
    tooltip: { trigger: 'item' as const, textStyle: { fontSize: 13 } },
    series: [
      {
        type: 'pie' as const,
        radius: ['50%', '72%'],
        center: ['40%', '50%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 2,
          borderColor: '#F7F8F6',
          borderWidth: 2,
        },
        label: {
          show: true,
          position: 'right' as const,
          fontFamily: '"Noto Sans SC", sans-serif',
          fontSize: 13,
          color: '#5C6B63',
          formatter: '{b}\n{d}%',
        },
        labelLine: {
          lineStyle: { color: '#E4E9E5' },
        },
        data: (data.value?.sentiment_stats ?? []).map((item) => ({
          name: sentimentLabel(item.label),
          value: item.count,
          itemStyle: { color: sentimentColor(item.label) },
        })),
      },
    ],
  }
})
</script>

<template>
  <div class="report">
    <!-- Page Header -->
    <div class="page-header">
      <div class="header-text">
        <h1>游客感受度报告</h1>
        <p class="header-date">{{ today }}</p>
      </div>
      <button class="refresh-btn" @click="fetchReport" :disabled="isLoading">
        {{ isLoading ? '加载中…' : '刷新数据' }}
      </button>
    </div>

    <!-- Error State -->
    <div v-if="error" class="error-state">
      <p>加载失败：{{ error }}</p>
      <button class="refresh-btn" @click="fetchReport">重试</button>
    </div>

    <!-- Empty State -->
    <EmptyStateGuide
      v-else-if="!hasData && !isLoading"
      message="暂无报告数据，请先与数字人对话以生成评估报告"
      to="/visitor/chat"
    />

    <article v-else class="report-body">
      <!-- Key Finding -->
      <div class="key-finding" v-if="dominantSentiment">
        <span class="finding-label">本期关键发现</span>
        <blockquote class="finding-quote">
          在 {{ totalAnalyses }} 条分析中，访客整体呈「{{
            sentimentLabel(dominantSentiment.label)
          }}」情绪
          <template v-if="topConcern">，最关注的方面为「{{ topConcern.concern }}」</template>
        </blockquote>
      </div>

      <!-- Sentiment Distribution -->
      <section class="report-section">
        <h2 class="section-title">情感分布</h2>
        <div class="sentiment-layout">
          <div class="sentiment-chart">
            <VChart
              :option="sentimentPieOption"
              :loading="isLoading"
              autoresize
              style="height: 320px"
            />
          </div>
          <div class="sentiment-summary">
            <div v-for="(item, idx) in data?.sentiment_stats" :key="idx" class="summary-item">
              <span class="summary-dot" :style="{ background: sentimentColor(item.label) }" />
              <span class="summary-label">{{ sentimentLabel(item.label) }}</span>
              <span class="summary-count">{{ item.count }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- Concern Ranking -->
      <section class="report-section">
        <h2 class="section-title">游客关注点排名</h2>
        <div v-if="data?.concern_ranking?.length" class="ranking-list">
          <div v-for="(item, idx) in data.concern_ranking" :key="idx" class="ranking-item">
            <div class="ranking-left">
              <span class="ranking-num">{{ String(idx + 1).padStart(2, '0') }}</span>
              <span class="ranking-name">{{ item.concern }}</span>
            </div>
            <div class="ranking-right">
              <div
                class="ranking-bar"
                :style="{
                  width: `${(item.count / (data.concern_ranking[0]?.count || 1)) * 100}%`,
                }"
              />
              <span class="ranking-count">{{ item.count }} 次</span>
            </div>
          </div>
        </div>
        <p v-else class="no-data">暂无关注点数据</p>
      </section>

      <!-- Improvement Suggestions -->
      <section class="report-section">
        <h2 class="section-title">改进建议</h2>
        <ul v-if="data?.suggestions?.length" class="suggestion-list">
          <li v-for="(s, idx) in data.suggestions" :key="idx" class="suggestion-item">
            <span class="suggestion-bullet" />
            <span>{{ s }}</span>
          </li>
        </ul>
        <p v-else class="no-data">暂无改进建议</p>
      </section>
    </article>
  </div>
</template>

<style scoped>
.report {
  padding: var(--space-xl) var(--space-2xl);
  max-width: 900px;
}

/* ── Page Header ── */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--space-2xl);
  gap: var(--space-md);
}

.header-text h1 {
  font-family: var(--font-display);
  font-size: var(--text-display);
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

.header-date {
  font-size: var(--text-small);
  color: var(--slate);
  margin: 0;
}

.refresh-btn {
  padding: var(--space-sm) var(--space-lg);
  background: var(--jade);
  color: var(--surface);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  white-space: nowrap;
  transition: background var(--duration-fast) var(--ease-out);
}

.refresh-btn:hover {
  background: var(--jade-strong);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-3xl);
  color: var(--slate);
  gap: var(--space-md);
}

/* ── Key Finding ── */
.key-finding {
  margin-bottom: var(--space-2xl);
  padding: var(--space-xl);
  border-left: 3px solid var(--jade);
  background: var(--jade-soft);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
}

.finding-label {
  display: block;
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--jade);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  margin-bottom: var(--space-sm);
}

.finding-quote {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  font-style: italic;
  color: var(--ink);
  line-height: 1.6;
  quotes: none;
}

/* ── Report Body ── */
.report-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2xl);
}

.report-section {
  border-top: 1px solid var(--mist);
  padding-top: var(--space-xl);
}

.section-title {
  font-family: var(--font-display);
  font-size: var(--text-title);
  margin: 0 0 var(--space-lg);
  color: var(--ink);
}

/* ── Sentiment Section ── */
.sentiment-layout {
  display: grid;
  grid-template-columns: 1fr 200px;
  gap: var(--space-xl);
  align-items: center;
}

.sentiment-chart {
  min-height: 320px;
}

.sentiment-summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.summary-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.summary-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.summary-label {
  flex: 1;
  font-size: var(--text-body);
  color: var(--ink);
}

.summary-count {
  font-family: var(--font-mono);
  font-size: var(--text-body);
  color: var(--slate);
}

/* ── Ranking ── */
.ranking-list {
  display: flex;
  flex-direction: column;
}

.ranking-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-lg);
  padding: var(--space-md) 0;
  border-bottom: 1px solid var(--mist);
}

.ranking-item:last-child {
  border-bottom: none;
}

.ranking-left {
  display: flex;
  align-items: center;
  gap: var(--space-lg);
  flex-shrink: 0;
}

.ranking-num {
  font-family: var(--font-mono);
  font-size: var(--text-title);
  font-weight: 600;
  color: var(--jade);
  min-width: 1.8em;
}

.ranking-name {
  font-size: var(--text-body);
  color: var(--ink);
  font-weight: 500;
}

.ranking-right {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--space-md);
  max-width: 300px;
}

.ranking-bar {
  height: 4px;
  background: var(--jade);
  opacity: 0.3;
  border-radius: 2px;
  min-width: 4px;
  transition: width 0.6s var(--ease-out);
}

.ranking-count {
  font-family: var(--font-mono);
  font-size: var(--text-small);
  color: var(--slate);
  white-space: nowrap;
}

/* ── Suggestions ── */
.suggestion-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.suggestion-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-sm);
  font-size: var(--text-body);
  line-height: 1.7;
  color: var(--ink);
}

.suggestion-bullet {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--jade);
  margin-top: 0.5em;
  flex-shrink: 0;
}

.no-data {
  color: var(--slate);
  font-size: var(--text-small);
}

/* ── Responsive ── */
@media (max-width: 768px) {
  .report {
    padding: var(--space-lg);
  }

  .sentiment-layout {
    grid-template-columns: 1fr;
  }

  .sentiment-summary {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .ranking-item {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-sm);
  }

  .ranking-right {
    max-width: 100%;
    width: 100%;
  }
}
</style>
