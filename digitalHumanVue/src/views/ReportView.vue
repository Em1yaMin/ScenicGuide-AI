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
const hasData = computed(() =>
  data.value !== null && (
    (data.value?.sentiment_stats?.length ?? 0) > 0 ||
    (data.value?.concern_ranking?.length ?? 0) > 0 ||
    (data.value?.suggestions?.length ?? 0) > 0
  )
)

onMounted(() => {
  fetchReport()
})

const sentimentPieOption = computed(() => ({
  title: { text: '情感分析' },
  tooltip: { trigger: 'item' },
  series: [{
    type: 'pie',
    radius: '65%',
    data: (data.value?.sentiment_stats ?? []).map((item) => ({
      name: item.label,
      value: item.count,
    })),
    emphasis: {
      itemStyle: {
        shadowBlur: 10,
        shadowOffsetX: 0,
        shadowColor: 'rgba(0, 0, 0, 0.5)',
      },
    },
  }],
}))
</script>

<template>
  <div class="page">
    <h1>游客感受度报告</h1>

    <div v-if="error" class="error-state">
      <p>加载失败: {{ error }}</p>
      <button class="refresh-btn" @click="fetchReport">重试</button>
    </div>

    <EmptyStateGuide
      v-else-if="!hasData && !isLoading"
      message="暂无报告数据，请先与数字人对话以生成评估报告"
      to="/visitor/chat"
    />

    <div v-else class="report-content">
      <section class="report-section">
        <h2>情感分布</h2>
        <VChart
          :option="sentimentPieOption"
          :loading="isLoading"
          autoresize
          style="height: 360px"
        />
      </section>

      <section class="report-section">
        <h2>游客关注点</h2>
        <ul
          v-if="data?.concern_ranking?.length"
          class="ranking-list"
        >
          <li v-for="(item, idx) in data.concern_ranking" :key="idx">
            <span class="rank">{{ idx + 1 }}</span>
            <span class="name">{{ item.concern }}</span>
            <span class="count">{{ item.count }} 次</span>
          </li>
        </ul>
        <p v-else class="no-data">暂无关注点数据</p>
      </section>

      <section class="report-section">
        <h2>改进建议</h2>
        <ul
          v-if="data?.suggestions?.length"
          class="suggestion-list"
        >
          <li v-for="(s, idx) in data.suggestions" :key="idx">
            {{ s }}
          </li>
        </ul>
        <p v-else class="no-data">暂无改进建议</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: var(--space-lg);
}

h1 {
  margin-bottom: var(--space-lg);
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

.error-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-3xl);
  color: var(--color-ink-secondary);
  gap: var(--space-md);
}

.report-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-xl);
}

.report-section {
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: var(--space-lg);
}

.report-section h2 {
  font-size: var(--text-subtitle);
  margin: 0 0 var(--space-md);
}

.ranking-list,
.suggestion-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.ranking-list li {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-xs) 0;
  border-bottom: 1px solid var(--color-border);
}

.rank {
  color: var(--color-accent);
  font-weight: bold;
  min-width: 2em;
}

.name {
  flex: 1;
}

.count {
  color: var(--color-ink-secondary);
  font-size: var(--text-small);
}

.suggestion-list li {
  padding: var(--space-xs) 0;
  line-height: var(--line-height-body);
}

.suggestion-list li::before {
  content: '\2022';
  color: var(--color-accent);
  margin-right: var(--space-xs);
}

.no-data {
  color: var(--color-ink-secondary);
  font-size: var(--text-small);
}
</style>
