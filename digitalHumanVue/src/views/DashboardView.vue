<script setup lang="ts">
import { computed, onMounted } from 'vue'
import VChart from 'vue-echarts'
import { registerECharts } from '@/composables/useECharts'
import { useApi } from '@/composables/useApi'
import { API_ADMIN_DASHBOARD } from '@/api'
import EmptyStateGuide from '@/components/EmptyStateGuide.vue'
import type { DashboardResponse } from '@/types/dashboard'

registerECharts()

const { data, isLoading, error, execute: fetchDashboard } = useApi<DashboardResponse>(API_ADMIN_DASHBOARD)

const hasData = computed(() =>
  data.value !== null && (
    data.value?.sentiment_trend?.some(t => t.positive > 0 || t.negative > 0 || t.neutral > 0) ||
    Object.keys(data.value?.satisfaction_distribution ?? {}).length > 0 ||
    (data.value?.hot_issues?.length ?? 0) > 0 ||
    (data.value?.spot_heatmap?.length ?? 0) > 0
  )
)

onMounted(() => {
  fetchDashboard()
})

// ── KPI Computations ──
const totalConversations = computed(() => {
  const trend = data.value?.sentiment_trend ?? []
  return trend.reduce((sum, t) => sum + (t.positive ?? 0) + (t.negative ?? 0) + (t.neutral ?? 0), 0)
})

const satisfactionRate = computed(() => {
  const dist = data.value?.satisfaction_distribution ?? {}
  const total = Object.values(dist).reduce((sum, v) => sum + v, 0)
  if (total === 0) return 0
  // 加权平均分（假设 key 是分数 1-5）
  const weightedSum = Object.entries(dist).reduce((sum, [score, count]) => sum + Number(score) * count, 0)
  return Math.round((weightedSum / total) * 10) / 10
})

const hotSpotCount = computed(() => {
  return (data.value?.spot_heatmap ?? []).length
})

const sentimentTrend = computed(() => {
  const trend = data.value?.sentiment_trend ?? []
  const total = trend.reduce((sum, t) => sum + t.positive + t.negative + t.neutral, 0)
  if (total === 0) return { positive: 0, direction: '—' }
  const positive = trend.reduce((sum, t) => sum + t.positive, 0)
  const pct = Math.round((positive / total) * 100)
  // 检查最近趋势（比较最后两天的正面占比）
  if (trend.length >= 2) {
    const recent = trend.slice(-2)
    const a = recent[0]!
    const b = recent[1]!
    const pct1 = a.positive / (a.positive + a.negative + a.neutral || 1)
    const pct2 = b.positive / (b.positive + b.negative + b.neutral || 1)
    return { positive: pct, direction: pct2 > pct1 ? '↑' : pct2 < pct1 ? '↓' : '→' }
  }
  return { positive: pct, direction: '—' }
})

// ── Chart Options ──
const BRAND_COLORS = {
  positive: '#6B9E6D',
  negative: '#C45C26',
  neutral: '#D4C5B9',
  accent: '#C45C26',
  ink: '#1A1A1A',
  inkSecondary: '#5C5C5C',
  paper: '#F7F5F0',
  border: '#E5E2DB',
}

const chartTextStyle = {
  color: BRAND_COLORS.inkSecondary,
  fontFamily: '"Noto Sans SC", sans-serif',
  fontSize: 12,
}

const sentimentTrendOption = computed(() => {
  const trend = data.value?.sentiment_trend ?? []
  return {
    tooltip: { trigger: 'axis' as const, textStyle: { fontSize: 13 } },
    legend: {
      bottom: 0,
      textStyle: { ...chartTextStyle, fontSize: 13 },
      itemWidth: 12,
      itemHeight: 12,
      itemGap: 24,
    },
    grid: { left: '3%', right: '4%', top: '8%', bottom: '14%', containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: trend.map(t => t.date),
      axisLine: { lineStyle: { color: BRAND_COLORS.border } },
      axisTick: { show: false },
      axisLabel: { ...chartTextStyle },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: BRAND_COLORS.border, type: 'dashed' } },
      axisLabel: { ...chartTextStyle },
    },
    series: [
      {
        name: '正面', type: 'bar', stack: 'sentiment',
        data: trend.map(t => t.positive ?? 0),
        itemStyle: { color: BRAND_COLORS.positive, borderRadius: [0, 0, 0, 0] },
        barWidth: '50%',
      },
      {
        name: '负面', type: 'bar', stack: 'sentiment',
        data: trend.map(t => t.negative ?? 0),
        itemStyle: { color: BRAND_COLORS.negative },
      },
      {
        name: '中性', type: 'bar', stack: 'sentiment',
        data: trend.map(t => t.neutral ?? 0),
        itemStyle: { color: BRAND_COLORS.neutral, borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})

const satisfactionOption = computed(() => {
  const dist = data.value?.satisfaction_distribution ?? {}
  const chartData = Object.entries(dist)
    .sort(([a], [b]) => Number(b) - Number(a))
    .map(([score, count]) => ({ name: `${score}分`, value: count }))

  const warmPalette = ['#C45C26', '#D4855A', '#E0A88A', '#EAC9B5', '#F4E9E0']

  return {
    tooltip: { trigger: 'item' as const, textStyle: { fontSize: 13 } },
    series: [{
      type: 'pie' as const,
      radius: ['55%', '78%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      itemStyle: {
        borderRadius: 3,
        borderColor: BRAND_COLORS.paper,
        borderWidth: 3,
      },
      label: { show: false },
      emphasis: {
        label: {
          show: true,
          fontSize: 20,
          fontWeight: 'bold',
          fontFamily: '"Noto Serif SC", serif',
        },
        scaleSize: 8,
      },
      data: chartData.map((d, i) => ({
        ...d,
        itemStyle: { color: warmPalette[i % warmPalette.length] },
      })),
    }],
  }
})

const hotIssuesOption = computed(() => {
  const issues = data.value?.hot_issues ?? []
  return {
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
      textStyle: { fontSize: 13 },
    },
    grid: { left: '3%', right: '8%', top: '5%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: BRAND_COLORS.border, type: 'dashed' } },
      axisLabel: { ...chartTextStyle },
    },
    yAxis: {
      type: 'category' as const,
      data: issues.map(i => i.concern),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { ...chartTextStyle, fontSize: 13, width: 130, overflow: 'truncate' },
    },
    series: [{
      type: 'bar' as const,
      data: issues.map((i, idx) => ({
        value: i.count,
        itemStyle: {
          color: BRAND_COLORS.accent,
          borderRadius: [0, 4, 4, 0],
          opacity: 1 - idx * 0.08,
        },
      })),
      barWidth: '55%',
    }],
  }
})

const spotHeatmapOption = computed(() => {
  const spots = data.value?.spot_heatmap ?? []
  return {
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
      textStyle: { fontSize: 13 },
    },
    grid: { left: '3%', right: '8%', top: '5%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: BRAND_COLORS.border, type: 'dashed' } },
      axisLabel: { ...chartTextStyle },
    },
    yAxis: {
      type: 'category' as const,
      data: spots.map(s => s.spot_name),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { ...chartTextStyle, fontSize: 13, width: 130, overflow: 'truncate' },
    },
    series: [{
      type: 'bar' as const,
      data: spots.map((s, idx) => ({
        value: s.mention_count,
        itemStyle: {
          color: BRAND_COLORS.ink,
          borderRadius: [0, 4, 4, 0],
          opacity: 0.85 - idx * 0.06,
        },
      })),
      barWidth: '55%',
    }],
  }
})
</script>

<template>
  <div class="dashboard">
    <div class="page-header">
      <div class="header-text">
        <h1>数据大屏</h1>
        <p class="header-desc">景区 AI 导览运营数据概览</p>
      </div>
      <button
        class="refresh-btn"
        @click="fetchDashboard"
        :disabled="isLoading"
      >
        {{ isLoading ? '加载中…' : '刷新数据' }}
      </button>
    </div>

    <!-- Error State -->
    <div v-if="error" class="error-state">
      <p>加载失败：{{ error }}</p>
      <button class="refresh-btn" @click="fetchDashboard">重试</button>
    </div>

    <!-- Empty State -->
    <EmptyStateGuide
      v-else-if="!hasData && !isLoading"
      message="暂无数据，请先与数字人对话以生成数据"
      to="/visitor/chat"
    />

    <template v-else>
      <!-- KPI Cards -->
      <div class="kpi-row">
        <div class="kpi-card">
          <span class="kpi-label">总对话数</span>
          <span class="kpi-value">{{ totalConversations.toLocaleString() }}</span>
          <span class="kpi-note">累计访客互动</span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">满意度</span>
          <span class="kpi-value">
            {{ satisfactionRate }}
            <span class="kpi-unit">/5</span>
          </span>
          <span class="kpi-note">
            正面反馈占比 {{ sentimentTrend.positive }}%
            <span class="kpi-direction">{{ sentimentTrend.direction }}</span>
          </span>
        </div>
        <div class="kpi-card">
          <span class="kpi-label">热门景点</span>
          <span class="kpi-value">{{ hotSpotCount }}</span>
          <span class="kpi-note">个景点被提及</span>
        </div>
      </div>

      <!-- Charts Grid -->
      <div class="charts-grid">
        <!-- Sentiment Trend -->
        <div class="chart-card">
          <div class="chart-header">
            <h3>情感变化趋势</h3>
            <p class="chart-annotation" v-if="sentimentTrend.direction !== '—'">
              正面情绪 {{ sentimentTrend.direction === '↑' ? '上升' : '下降' }}中
            </p>
          </div>
          <VChart :option="sentimentTrendOption" :loading="isLoading" autoresize />
        </div>

        <!-- Satisfaction Distribution -->
        <div class="chart-card">
          <div class="chart-header">
            <h3>满意度分布</h3>
            <p class="chart-annotation">综合评分 {{ satisfactionRate }} / 5</p>
          </div>
          <VChart :option="satisfactionOption" :loading="isLoading" autoresize />
        </div>

        <!-- Hot Issues -->
        <div class="chart-card">
          <div class="chart-header">
            <h3>热门关注点</h3>
            <p class="chart-annotation">访客最关心的 {{ (data?.hot_issues?.length ?? 0) }} 个问题</p>
          </div>
          <VChart :option="hotIssuesOption" :loading="isLoading" autoresize />
        </div>

        <!-- Spot Heatmap -->
        <div class="chart-card">
          <div class="chart-header">
            <h3>景点热度</h3>
            <p class="chart-annotation">各景点提及次数排名</p>
          </div>
          <VChart :option="spotHeatmapOption" :loading="isLoading" autoresize />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dashboard {
  padding: var(--space-xl) var(--space-2xl);
  max-width: 1400px;
}

/* ── Page Header ── */
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--space-xl);
  gap: var(--space-md);
}

.header-text h1 {
  font-family: var(--font-display);
  font-size: var(--text-display);
  margin: 0 0 var(--space-xs);
  color: var(--color-ink);
}

.header-desc {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
  margin: 0;
}

.refresh-btn {
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-ink);
  color: var(--color-paper);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  white-space: nowrap;
  transition:
    background var(--duration-fast) var(--ease-out);
}

.refresh-btn:hover {
  background: var(--color-accent);
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
  color: var(--color-ink-secondary);
  gap: var(--space-md);
}

/* ── KPI Cards ── */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-lg);
  margin-bottom: var(--space-xl);
}

.kpi-card {
  padding: var(--space-lg) var(--space-xl);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: transparent;
}

.kpi-label {
  display: block;
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin-bottom: var(--space-sm);
}

.kpi-value {
  display: block;
  font-family: var(--font-mono);
  font-size: var(--text-display);
  font-weight: 600;
  color: var(--color-ink);
  line-height: 1.2;
}

.kpi-unit {
  font-size: var(--text-title);
  color: var(--color-ink-secondary);
  font-weight: 400;
}

.kpi-note {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  margin-top: var(--space-sm);
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

.kpi-direction {
  color: var(--color-accent);
  font-weight: 600;
}

/* ── Charts Grid ── */
.charts-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-lg);
}

.chart-card {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: var(--space-lg);
  min-height: 380px;
  display: flex;
  flex-direction: column;
  background: transparent;
  transition: border-color var(--duration-normal) var(--ease-out);
}

.chart-card:hover {
  border-color: var(--color-ink-secondary);
}

.chart-header {
  margin-bottom: var(--space-sm);
}

.chart-header h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  margin: 0 0 var(--space-xs);
  color: var(--color-ink);
}

.chart-annotation {
  font-family: var(--font-display);
  font-size: var(--text-small);
  font-style: italic;
  color: var(--color-accent);
  margin: 0;
  opacity: 0.8;
}

.chart-card :deep(.echarts) {
  flex: 1;
  min-height: 300px;
}

/* ── Responsive ── */
@media (max-width: 1024px) {
  .charts-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .dashboard {
    padding: var(--space-lg);
  }

  .kpi-row {
    grid-template-columns: 1fr;
  }

  .page-header {
    flex-direction: column;
  }
}
</style>
