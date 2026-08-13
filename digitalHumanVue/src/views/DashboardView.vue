<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { registerECharts } from '@/composables/useECharts'
import { useApi } from '@/composables/useApi'
import { fetchWithCodeEnvelope, ANALYSIS_TIMEOUT_MS } from '@/utils/fetchWithCodeEnvelope'
import { API_ADMIN_DASHBOARD, API_ANALYSIS_OVERVIEW } from '@/api'
import EmptyStateGuide from '@/components/EmptyStateGuide.vue'
import type { DashboardResponse, AIOverviewResponse } from '@/types/dashboard'

registerECharts()

const {
  data,
  isLoading,
  error,
  execute: fetchDashboard,
} = useApi<DashboardResponse>(API_ADMIN_DASHBOARD)

// AI 运营建议（使用动态 URL 以支持 days 参数）
const aiData = ref<AIOverviewResponse | null>(null)
const aiLoading = ref(false)
const aiError = ref<string | null>(null)
const aiDays = ref(30)
// 分析等待时长（秒）：LLM 分析可能耗时 1~2 分钟，实时展示已等待时间减少「一直分析中」困惑
const aiElapsed = ref(0)
let aiTimer: ReturnType<typeof setInterval> | null = null

function startAiElapsedTimer(): void {
  aiElapsed.value = 0
  aiTimer = setInterval(() => {
    aiElapsed.value += 1
  }, 1000)
}

function stopAiElapsedTimer(): void {
  if (aiTimer) {
    clearInterval(aiTimer)
    aiTimer = null
  }
}

async function fetchAIOverview(): Promise<void> {
  aiLoading.value = true
  aiError.value = null
  startAiElapsedTimer()
  try {
    aiData.value = await fetchWithCodeEnvelope<AIOverviewResponse>(
      `${API_ANALYSIS_OVERVIEW}?days=${aiDays.value}`,
      // AI 分析调 LLM（后端预算 180s/240s），必须覆盖默认 15s 超时，否则 LLM 慢时被掐断
      { timeoutMs: ANALYSIS_TIMEOUT_MS },
    )
  } catch (e) {
    aiError.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    aiLoading.value = false
    stopAiElapsedTimer()
  }
}

const hasData = computed(
  () =>
    data.value !== null &&
    (data.value?.sentiment_trend?.some((t) => t.positive > 0 || t.negative > 0 || t.neutral > 0) ||
      Object.keys(data.value?.satisfaction_distribution ?? {}).length > 0 ||
      (data.value?.hot_issues?.length ?? 0) > 0 ||
      (data.value?.spot_heatmap?.length ?? 0) > 0),
)

onMounted(() => {
  fetchDashboard()
  fetchAIOverview()
})

// ── KPI Computations ──
const totalConversations = computed(() => data.value?.total_conversations ?? 0)

const satisfactionRate = computed(() => {
  const dist = data.value?.satisfaction_distribution ?? {}
  const total = Object.values(dist).reduce((sum, v) => sum + v, 0)
  if (total === 0) return 0
  // 加权平均分（假设 key 是分数 1-5）
  const weightedSum = Object.entries(dist).reduce(
    (sum, [score, count]) => sum + Number(score) * count,
    0,
  )
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
// 图表主题色对齐设计体系色板（doc/redesign-v1.md §2.1）：
// 正面=青玉 / 负面=朱砂 / 中性=山岚深 / 强调=青玉 / 边框=山岚
const BRAND_COLORS = {
  positive: '#2F6B5B', // jade
  negative: '#C1412F', // cinnabar
  neutral: '#C8D2CC', // mist-strong
  accent: '#2F6B5B', // jade
  ink: '#1E2A26', // ink
  inkSecondary: '#5C6B63', // slate
  paper: '#F7F8F6', // paper
  border: '#E4E9E5', // mist
}

const chartTextStyle = {
  color: BRAND_COLORS.inkSecondary,
  fontFamily: '"Noto Sans SC", sans-serif',
  fontSize: 12,
}

const sentimentTrendOption = computed(() => {
  const trend = data.value?.sentiment_trend ?? []
  // 全量日期序列可能较长，标签拥挤时旋转显示
  const rotateLabel = trend.length > 31 ? 45 : trend.length > 14 ? 30 : 0
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
      data: trend.map((t) => t.date),
      axisLine: { lineStyle: { color: BRAND_COLORS.border } },
      axisTick: { show: false },
      axisLabel: { ...chartTextStyle, rotate: rotateLabel },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: BRAND_COLORS.border, type: 'dashed' } },
      axisLabel: { ...chartTextStyle },
    },
    series: [
      {
        name: '正面',
        type: 'bar',
        stack: 'sentiment',
        data: trend.map((t) => t.positive ?? 0),
        itemStyle: { color: BRAND_COLORS.positive, borderRadius: [0, 0, 0, 0] },
        barWidth: '50%',
      },
      {
        name: '负面',
        type: 'bar',
        stack: 'sentiment',
        data: trend.map((t) => t.negative ?? 0),
        itemStyle: { color: BRAND_COLORS.negative },
      },
      {
        name: '中性',
        type: 'bar',
        stack: 'sentiment',
        data: trend.map((t) => t.neutral ?? 0),
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

  // 满意度 1-5 分：低分朱砂 → 高分青玉 的渐变段（与色板同源）
  const satisfactionPalette = ['#2F6B5B', '#5E8A7B', '#9CB3A8', '#C89C8E', '#C1412F']

  return {
    tooltip: { trigger: 'item' as const, textStyle: { fontSize: 13 } },
    series: [
      {
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
          itemStyle: { color: satisfactionPalette[i % satisfactionPalette.length] },
        })),
      },
    ],
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
      data: issues.map((i) => i.concern),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { ...chartTextStyle, fontSize: 13, width: 130, overflow: 'truncate' },
    },
    series: [
      {
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
      },
    ],
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
      data: spots.map((s) => s.spot_name),
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: { ...chartTextStyle, fontSize: 13, width: 130, overflow: 'truncate' },
    },
    series: [
      {
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
      },
    ],
  }
})

// ── AI 运营建议图表 ──

const aiSatisfactionTrendOption = computed(() => {
  const trend = aiData.value?.satisfaction_trend ?? []
  return {
    tooltip: {
      trigger: 'axis' as const,
      textStyle: { fontSize: 13 },
      formatter: (params: unknown) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${p.axisValue}<br/>满意度均分: <strong>${p.value}</strong>`
      },
    },
    grid: { left: '3%', right: '4%', top: '8%', bottom: '10%', containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: trend.map((t) => t.date),
      axisLine: { lineStyle: { color: BRAND_COLORS.border } },
      axisTick: { show: false },
      axisLabel: { ...chartTextStyle },
    },
    yAxis: {
      type: 'value' as const,
      min: 0,
      max: 5,
      splitLine: { lineStyle: { color: BRAND_COLORS.border, type: 'dashed' } },
      axisLabel: { ...chartTextStyle },
    },
    series: [
      {
        type: 'line' as const,
        data: trend.map((t) => t.avg_score),
        smooth: true,
        lineStyle: { color: BRAND_COLORS.accent, width: 2 },
        itemStyle: { color: BRAND_COLORS.accent },
        areaStyle: {
          color: {
            type: 'linear' as const,
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(47, 107, 91, 0.15)' },
              { offset: 1, color: 'rgba(47, 107, 91, 0.02)' },
            ],
          },
        },
      },
    ],
  }
})

const aiPeakHoursOption = computed(() => {
  const peaks = aiData.value?.peak_hours ?? []
  const sorted = [...peaks].sort((a, b) => a.hour - b.hour)
  const hours = sorted.map((p) => p.label || `${p.hour}:00`)
  const values = sorted.map((p) => p.count)
  const maxVal = Math.max(...values, 1)

  return {
    tooltip: {
      trigger: 'axis' as const,
      textStyle: { fontSize: 13 },
      formatter: (params: unknown) => {
        const p = Array.isArray(params) ? params[0] : params
        return `${p.axisValue}<br/>对话量: <strong>${p.value}</strong>`
      },
    },
    grid: { left: '3%', right: '5%', top: '5%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: hours,
      axisLine: { lineStyle: { color: BRAND_COLORS.border } },
      axisTick: { show: false },
      axisLabel: { ...chartTextStyle, rotate: 45 },
    },
    yAxis: {
      type: 'value' as const,
      splitLine: { lineStyle: { color: BRAND_COLORS.border, type: 'dashed' } },
      axisLabel: { ...chartTextStyle },
    },
    series: [
      {
        type: 'bar' as const,
        data: values.map((v) => ({
          value: v,
          itemStyle: {
            color: v === maxVal ? BRAND_COLORS.accent : BRAND_COLORS.ink,
            borderRadius: [4, 4, 0, 0],
            opacity: 0.4 + (v / maxVal) * 0.6,
          },
        })),
        barWidth: '60%',
      },
    ],
  }
})

const hasAIData = computed(
  () =>
    aiData.value !== null &&
    ((aiData.value?.hot_questions?.length ?? 0) > 0 ||
      (aiData.value?.unanswered_questions?.length ?? 0) > 0 ||
      (aiData.value?.suggestions?.length ?? 0) > 0 ||
      aiData.value?.total_dialogues > 0),
)
</script>

<template>
  <div class="dashboard">
    <div class="page-header">
      <div class="header-text">
        <h1>数据大屏</h1>
        <p class="header-desc">景区 AI 导览运营数据概览</p>
      </div>
      <button class="refresh-btn" @click="fetchDashboard" :disabled="isLoading">
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
            <p class="chart-annotation">访客最关心的 {{ data?.hot_issues?.length ?? 0 }} 个问题</p>
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

      <!-- ═══ AI 运营建议 ═══ -->
      <div class="ai-section">
        <div class="ai-section-header">
          <div>
            <h2>AI 运营建议</h2>
            <p class="ai-section-desc">
              LangChain 聚合分析 · 最近 {{ aiDays }} 天 · {{ aiData?.total_dialogues ?? 0 }} 条对话
            </p>
          </div>
          <div class="ai-header-actions">
            <select v-model.number="aiDays" class="ai-days-select" @change="fetchAIOverview">
              <option :value="7">最近 7 天</option>
              <option :value="30">最近 30 天</option>
              <option :value="90">最近 90 天</option>
            </select>
            <button class="refresh-btn" @click="fetchAIOverview" :disabled="aiLoading">
              {{ aiLoading ? '分析中…' : '刷新分析' }}
            </button>
            <span v-if="aiLoading" class="ai-wait-hint">已等待 {{ aiElapsed }}s</span>
          </div>
        </div>

        <!-- AI Error -->
        <div v-if="aiError" class="ai-error-banner">
          <span>AI 分析暂时不可用：{{ aiError }}</span>
          <button class="text-btn" @click="fetchAIOverview">重试</button>
        </div>

        <template v-if="hasAIData">
          <!-- AI 摘要卡片 -->
          <div class="ai-summary-card">
            <div class="ai-summary-icon">🤖</div>
            <div class="ai-summary-text">
              <p class="ai-summary-title">分析摘要</p>
              <p class="ai-summary-body">{{ aiData?.ai_summary }}</p>
              <p class="ai-summary-direction">
                满意度趋势判定：
                <strong
                  :class="{
                    'trend-up': aiData?.satisfaction_direction === '上升',
                    'trend-down': aiData?.satisfaction_direction === '下降',
                  }"
                >
                  {{ aiData?.satisfaction_direction }}
                </strong>
              </p>
            </div>
          </div>

          <!-- AI 分析图表区 -->
          <div class="charts-grid">
            <!-- 满意度趋势折线图 -->
            <div class="chart-card">
              <div class="chart-header">
                <h3>满意度趋势</h3>
                <p class="chart-annotation">日均分 {{ aiData?.satisfaction_direction }}</p>
              </div>
              <VChart :option="aiSatisfactionTrendOption" :loading="aiLoading" autoresize />
            </div>

            <!-- 高峰时段热力图 -->
            <div class="chart-card">
              <div class="chart-header">
                <h3>高峰时段分布</h3>
                <p class="chart-annotation">24小时对话量分布</p>
              </div>
              <VChart :option="aiPeakHoursOption" :loading="aiLoading" autoresize />
            </div>
          </div>

          <!-- 热门问题 & 待优化问题 -->
          <div class="charts-grid">
            <!-- 热门问题 TOP10 -->
            <div class="chart-card ai-list-card">
              <div class="chart-header">
                <h3>热门问题 TOP10</h3>
                <p class="chart-annotation">游客最常问的问题</p>
              </div>
              <div class="ai-list">
                <div
                  v-for="(item, idx) in aiData?.hot_questions?.slice(0, 10)"
                  :key="idx"
                  class="ai-list-item"
                >
                  <span class="ai-list-rank" :class="{ top3: idx < 3 }">{{ idx + 1 }}</span>
                  <div class="ai-list-content">
                    <span class="ai-list-question">{{ item.question }}</span>
                    <span class="ai-list-meta">
                      <span class="ai-list-count">{{ item.count }} 次</span>
                      <span v-if="item.category" class="ai-list-category">{{ item.category }}</span>
                    </span>
                  </div>
                </div>
                <div v-if="!aiData?.hot_questions?.length" class="ai-list-empty">
                  暂无热门问题数据
                </div>
              </div>
            </div>

            <!-- 待优化问题 -->
            <div class="chart-card ai-list-card">
              <div class="chart-header">
                <h3>待优化问题</h3>
                <p class="chart-annotation">AI 回答不够好的问题</p>
              </div>
              <div class="ai-list">
                <div
                  v-for="(item, idx) in aiData?.unanswered_questions"
                  :key="idx"
                  class="ai-list-item unanswered"
                >
                  <span class="ai-list-rank warn">{{ idx + 1 }}</span>
                  <div class="ai-list-content">
                    <span class="ai-list-question">{{ item.question }}</span>
                    <span class="ai-list-reason">{{ item.reason }}</span>
                  </div>
                </div>
                <div v-if="!aiData?.unanswered_questions?.length" class="ai-list-empty">
                  暂无待优化问题
                </div>
              </div>
            </div>
          </div>

          <!-- 运营建议 -->
          <div v-if="aiData?.suggestions?.length" class="ai-suggestions-card">
            <div class="chart-header">
              <h3>💡 运营优化建议</h3>
              <p class="chart-annotation">基于 AI 分析生成的建议</p>
            </div>
            <div class="suggestions-list">
              <div v-for="(s, idx) in aiData.suggestions" :key="idx" class="suggestion-item">
                <span class="suggestion-num">{{ idx + 1 }}</span>
                <span class="suggestion-text">{{ s }}</span>
              </div>
            </div>
          </div>
        </template>

        <!-- AI 空态 -->
        <div v-else-if="!aiLoading && !aiError" class="ai-empty">
          <p>AI 分析暂无数据，请先积累对话记录</p>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 第二阶段视觉打磨：样式全部迁移到设计体系 token（doc/redesign-v1.md §2），
 * 卡片素白底 + 轻柔阴影 + 青玉强调（数据工具风，对比访客端沉浸叙事风）。 */
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
  color: var(--ink);
}

.header-desc {
  font-size: var(--text-body);
  color: var(--slate);
  margin: 0;
}

/* 主操作按钮：primary=jade（管理端按钮规范） */
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

/* ── KPI Cards ── */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-lg);
  margin-bottom: var(--space-xl);
}

.kpi-card {
  padding: var(--space-lg) var(--space-xl);
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-card);
}

.kpi-label {
  display: block;
  font-size: var(--text-small);
  color: var(--slate);
  margin-bottom: var(--space-sm);
}

.kpi-value {
  display: block;
  font-family: var(--font-mono);
  font-size: var(--text-display);
  font-weight: 600;
  color: var(--ink);
  line-height: 1.2;
}

.kpi-unit {
  font-size: var(--text-title);
  color: var(--slate);
  font-weight: 400;
}

.kpi-note {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  margin-top: var(--space-sm);
  font-size: var(--text-small);
  color: var(--slate);
}

.kpi-direction {
  color: var(--jade);
  font-weight: 600;
}

/* ── Charts Grid ── */
.charts-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-lg);
}

.chart-card {
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  padding: var(--space-lg);
  min-height: 380px;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  box-shadow: var(--shadow-card);
  transition: border-color var(--duration-normal) var(--ease-out);
}

/* 卡片 hover 只换边框（doc/redesign-v1.md §6.3：不做位移/浮起） */
.chart-card:hover {
  border-color: var(--mist-strong);
}

.chart-header {
  margin-bottom: var(--space-sm);
}

.chart-header h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

/* 题跋式注释：与访客端 section 题跋点同源 */
.chart-annotation {
  font-family: var(--font-display);
  font-size: var(--text-small);
  font-style: italic;
  color: var(--jade);
  margin: 0;
  opacity: 0.8;
}

.chart-card :deep(.echarts) {
  flex: 1;
  min-height: 300px;
}

/* ── AI 运营建议 ── */
.ai-section {
  margin-top: var(--space-2xl);
  padding-top: var(--space-xl);
  border-top: 2px solid var(--mist);
}

.ai-section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
  gap: var(--space-md);
}

.ai-section-header h2 {
  font-family: var(--font-display);
  font-size: var(--text-title);
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

.ai-section-desc {
  font-size: var(--text-small);
  color: var(--slate);
  margin: 0;
}

.ai-header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.ai-days-select {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: var(--text-small);
  color: var(--ink);
  cursor: pointer;
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out);
}

.ai-days-select:focus {
  border-color: var(--jade);
  box-shadow: 0 0 0 3px var(--jade-soft);
}

/* 分析等待时长提示：LLM 分析可能耗时 1~2 分钟，秒级倒计时减少「一直分析中」困惑 */
.ai-wait-hint {
  font-size: var(--text-small);
  color: var(--slate);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

/* 错误横幅：朱砂淡底（错误语义=朱砂，见色板角色） */
.ai-error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  margin-bottom: var(--space-lg);
  background: var(--cinnabar-soft);
  border: 1px solid rgba(193, 65, 47, 0.25);
  border-radius: var(--radius-md);
  font-size: var(--text-small);
  color: var(--cinnabar);
}

.text-btn {
  background: none;
  border: none;
  color: var(--jade);
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  text-decoration: underline;
}

/* ── AI 摘要卡片 ── */
.ai-summary-card {
  display: flex;
  gap: var(--space-lg);
  padding: var(--space-xl);
  margin-bottom: var(--space-xl);
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  background:
    linear-gradient(135deg, rgba(47, 107, 91, 0.04), rgba(193, 65, 47, 0.03)), var(--surface);
  box-shadow: var(--shadow-card);
}

.ai-summary-icon {
  font-size: 2rem;
  flex-shrink: 0;
}

.ai-summary-title {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  margin: 0 0 var(--space-sm);
  color: var(--ink);
}

.ai-summary-body {
  font-size: var(--text-body);
  color: var(--slate);
  line-height: 1.6;
  margin: 0 0 var(--space-sm);
}

.ai-summary-direction {
  font-size: var(--text-small);
  color: var(--slate);
  margin: 0;
}

.trend-up {
  color: var(--jade);
}

.trend-down {
  color: var(--cinnabar);
}

/* ── AI 列表卡片 ── */
.ai-list-card {
  min-height: auto;
}

.ai-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  max-height: 420px;
  overflow-y: auto;
}

.ai-list-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-md);
  padding: var(--space-sm) var(--space-md);
  border-radius: var(--radius-sm);
  transition: background var(--duration-fast) var(--ease-out);
}

.ai-list-item:hover {
  background: var(--overlay);
}

.ai-list-item.unanswered {
  border-left: 3px solid rgba(193, 65, 47, 0.3);
}

.ai-list-rank {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--mist);
  font-size: var(--text-small);
  font-weight: 600;
  color: var(--slate);
  flex-shrink: 0;
}

.ai-list-rank.top3 {
  background: var(--jade);
  color: var(--surface);
}

.ai-list-rank.warn {
  background: var(--cinnabar-soft);
  color: var(--cinnabar);
}

.ai-list-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.ai-list-question {
  font-size: var(--text-body);
  color: var(--ink);
  line-height: 1.4;
}

.ai-list-meta {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: var(--text-small);
}

.ai-list-count {
  color: var(--slate);
}

.ai-list-category {
  padding: 1px 8px;
  background: var(--jade-soft);
  color: var(--jade);
  border-radius: 10px;
  font-size: 11px;
}

.ai-list-reason {
  font-size: var(--text-small);
  color: var(--cinnabar);
  font-style: italic;
}

.ai-list-empty {
  padding: var(--space-xl);
  text-align: center;
  color: var(--slate);
  font-size: var(--text-small);
}

/* ── 运营建议卡片 ── */
.ai-suggestions-card {
  margin-top: var(--space-xl);
  padding: var(--space-xl);
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-card);
}

.suggestions-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
  margin-top: var(--space-md);
}

.suggestion-item {
  display: flex;
  align-items: flex-start;
  gap: var(--space-md);
  padding: var(--space-md);
  background: rgba(47, 107, 91, 0.05);
  border-radius: var(--radius-sm);
  border-left: 3px solid var(--jade);
}

.suggestion-num {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--jade);
  color: var(--surface);
  font-size: var(--text-small);
  font-weight: 600;
  flex-shrink: 0;
}

.suggestion-text {
  font-size: var(--text-body);
  color: var(--ink);
  line-height: 1.5;
  padding-top: 2px;
}

.ai-empty {
  padding: var(--space-3xl);
  text-align: center;
  color: var(--slate);
  font-size: var(--text-body);
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

  .ai-section-header {
    flex-direction: column;
  }

  .ai-header-actions {
    width: 100%;
  }

  .ai-summary-card {
    flex-direction: column;
  }
}
</style>
