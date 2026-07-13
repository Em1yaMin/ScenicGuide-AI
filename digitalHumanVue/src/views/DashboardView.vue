<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import VChart from 'vue-echarts'
import { registerECharts } from '@/composables/useECharts'
import { API_ADMIN_DASHBOARD } from '@/api'
import type { DashboardResponse } from '@/types/dashboard'

registerECharts()

const data = ref<DashboardResponse | null>(null)
const isLoading = ref(true)
const error = ref<string | null>(null)
const hasData = computed(() =>
  data.value !== null &&
  data.value.sentiment_trend.some(t => t.positive > 0 || t.negative > 0 || t.neutral > 0)
)

async function fetchDashboard() {
  isLoading.value = true
  error.value = null
  try {
    const response = await fetch(API_ADMIN_DASHBOARD)
    const result = await response.json()
    if (result.code) {
      error.value = result.message || '服务器错误'
      return
    }
    data.value = result
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally {
    isLoading.value = false
  }
}

onMounted(() => {
  fetchDashboard()
})

const sentimentTrendOption = computed(() => {
  const trend = data.value?.sentiment_trend ?? []
  return {
    title: { text: '情感趋势' },
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0 },
    xAxis: { type: 'category', data: trend.map(t => t.date) },
    yAxis: { type: 'value' },
    series: [
      { name: '正面', type: 'bar', data: trend.map(t => t.positive), itemStyle: { color: '#52c41a' } },
      { name: '负面', type: 'bar', data: trend.map(t => t.negative), itemStyle: { color: '#ff4d4f' } },
      { name: '中性', type: 'bar', data: trend.map(t => t.neutral), itemStyle: { color: '#faad14' } },
    ],
  }
})

const satisfactionOption = computed(() => {
  const dist = data.value?.satisfaction_distribution ?? {}
  const chartData = Object.entries(dist).map(([score, count]) => ({
    name: `${score}分`,
    value: count,
  }))
  return {
    title: { text: '满意度分布' },
    tooltip: { trigger: 'item' },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      data: chartData,
    }],
  }
})

const hotIssuesOption = computed(() => {
  const issues = data.value?.hot_issues ?? []
  return {
    title: { text: '热门关注点' },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: issues.map((i) => i.concern),
      axisLabel: { width: 120, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      data: issues.map((i) => i.count),
      itemStyle: { color: '#C45C26' },
    }],
  }
})

const spotHeatmapOption = computed(() => {
  const spots = data.value?.spot_heatmap ?? []
  return {
    title: { text: '景点热度' },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'value' },
    yAxis: {
      type: 'category',
      data: spots.map((s) => s.spot_name),
      axisLabel: { width: 120, overflow: 'truncate' },
    },
    series: [{
      type: 'bar',
      data: spots.map((s) => s.mention_count),
      itemStyle: { color: '#1A1A1A' },
    }],
  }
})
</script>

<template>
  <div class="page">
    <div class="page-header">
      <h1>数据大屏</h1>
      <button
        class="refresh-btn"
        @click="fetchDashboard"
        :disabled="isLoading"
      >
        {{ isLoading ? '加载中...' : '刷新数据' }}
      </button>
    </div>

    <div v-if="error" class="error-state">
      <p>加载失败: {{ error }}</p>
      <button class="refresh-btn" @click="fetchDashboard">重试</button>
    </div>

    <div v-else-if="!hasData && !isLoading" class="empty-state">
      <p>暂无数据，请先与数字人对话以生成数据</p>
      <RouterLink to="/visitor/chat" class="guide-btn">前往数字人对话</RouterLink>
    </div>

    <div v-else class="charts-grid">
      <div class="chart-card">
        <VChart :option="sentimentTrendOption" :loading="isLoading" autoresize />
      </div>
      <div class="chart-card">
        <VChart :option="satisfactionOption" :loading="isLoading" autoresize />
      </div>
      <div class="chart-card">
        <VChart :option="hotIssuesOption" :loading="isLoading" autoresize />
      </div>
      <div class="chart-card">
        <VChart :option="spotHeatmapOption" :loading="isLoading" autoresize />
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: var(--space-lg);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
}

.page-header h1 {
  margin: 0;
}

.refresh-btn {
  padding: var(--space-xs) var(--space-md);
  background: var(--color-accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: var(--text-small);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.guide-btn {
  display: inline-block;
  padding: var(--space-xs) var(--space-md);
  background: var(--color-accent);
  color: #fff;
  border-radius: 4px;
  text-decoration: none;
  font-size: var(--text-small);
}

.error-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-3xl);
  color: var(--color-ink-secondary);
  gap: var(--space-md);
}

.charts-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-lg);
}

.chart-card {
  width: 100%;
  min-height: 320px;
  padding: var(--space-md);
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  position: relative;
}
</style>
