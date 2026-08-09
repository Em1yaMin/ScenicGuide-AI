<script setup lang="ts">
/**
 * 管理端成本仪表盘（批次7 C5 前端）：LLM token 用量报表。
 * 参考 GitHub 用量报表风格：趋势图 + 维度聚合 + 明细表 + CSV 导出。
 * 后端契约见 digitalHumanApi/doc/usage-tracking.md（/api/admin/usage）。
 */
import { computed, onMounted, ref } from 'vue'
import VChart from 'vue-echarts'
import { registerECharts } from '@/composables/useECharts'
import { exportUsageCsv, fetchUsageDetails, fetchUsageOverview } from '@/api/usage'
import type { UsageDetailResponse, UsageGranularity, UsageOverviewResponse } from '@/types/usage'

registerECharts()

// ── 常量 ──
const TOKEN_KEY = 'admin_api_token'
const PAGE_SIZE = 20
const DETAIL_MAX_PAGES = 1000 // 兜底上限，防止异常 total 撑爆分页渲染

/** 时间范围预设（与需求对应：今天/近7天/近30天/自定义） */
const RANGE_PRESETS = [
  { key: 'today', label: '今天' },
  { key: '7d', label: '近7天' },
  { key: '30d', label: '近30天' },
  { key: 'custom', label: '自定义' },
] as const
type RangePresetKey = (typeof RANGE_PRESETS)[number]['key']

const GRANULARITY_OPTIONS: { key: UsageGranularity; label: string }[] = [
  { key: 'day', label: '按日' },
  { key: 'week', label: '按周' },
  { key: 'month', label: '按月' },
]

/** 图表主题色对齐设计体系色板（doc/redesign-v1.md §2.1，与数据大屏/DashboardView 一致） */
const BRAND = {
  accent: '#2F6B5B', // jade
  ink: '#1E2A26', // ink
  inkSecondary: '#5C6B63', // slate
  border: '#E4E9E5', // mist
}
const chartTextStyle = {
  color: BRAND.inkSecondary,
  fontFamily: '"Noto Sans SC", sans-serif',
  fontSize: 12,
}

// ── State：时间范围 ──
const rangePreset = ref<RangePresetKey>('7d')
const customStart = ref('') // YYYY-MM-DD
const customEnd = ref('')
const rangeError = ref<string | null>(null)

// ── State：总览 / 明细 ──
const granularity = ref<UsageGranularity>('day')
const overview = ref<UsageOverviewResponse | null>(null)
const details = ref<UsageDetailResponse | null>(null)
const isLoadingOverview = ref(false)
const isLoadingDetails = ref(false)
const overviewError = ref<string | null>(null)
const detailsError = ref<string | null>(null)

// ── State：明细筛选 / 分页 ──
const modelFilter = ref('') // '' = 全部模型
const chainFilter = ref('') // '' = 全部链路
const page = ref(1)

// ── State：CSV 导出 ──
const isExporting = ref(false)
const exportMessage = ref<string | null>(null)
let exportMessageTimer: ReturnType<typeof setTimeout> | null = null

// ── State：Token 设置 ──
const showToken = ref(false)
const adminToken = ref(localStorage.getItem(TOKEN_KEY) ?? '')
const tokenInput = ref('')

// ── 时间工具 ──
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

/** Date → 本地时区 ISO datetime（YYYY-MM-DDTHH:mm:ss±HH:MM，后端按 ±tz 转 UTC） */
function toLocalISO(d: Date): string {
  const offsetMin = -d.getTimezoneOffset()
  const sign = offsetMin >= 0 ? '+' : '-'
  const abs = Math.abs(offsetMin)
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` +
    `T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}` +
    `${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)}`
  )
}

/** 按当前预设计算时间范围（ISO datetime）；自定义缺日期/非法时返回 null 并置 rangeError */
function buildRange(): { start: string; end: string } | null {
  if (rangePreset.value === 'custom') {
    if (!customStart.value || !customEnd.value) {
      rangeError.value = '请选择自定义时间范围的起止日期'
      return null
    }
    if (customStart.value > customEnd.value) {
      rangeError.value = '开始日期不能晚于结束日期'
      return null
    }
    const start = new Date(`${customStart.value}T00:00:00`)
    const end = new Date(`${customEnd.value}T00:00:00`)
    end.setDate(end.getDate() + 1) // 后端 end 为开区间，+1 天含结束日全天
    rangeError.value = null
    return { start: toLocalISO(start), end: toLocalISO(end) }
  }
  rangeError.value = null
  // 默认取到「明日 00:00」：开区间语义下覆盖今天全天
  const end = new Date()
  end.setHours(0, 0, 0, 0)
  end.setDate(end.getDate() + 1)
  const start = new Date(end)
  if (rangePreset.value === 'today') start.setDate(start.getDate() - 1)
  else if (rangePreset.value === '7d') start.setDate(start.getDate() - 7)
  else start.setDate(start.getDate() - 30)
  return { start: toLocalISO(start), end: toLocalISO(end) }
}

// ── 数据加载 ──
async function loadOverview() {
  const range = buildRange()
  if (!range) return
  isLoadingOverview.value = true
  overviewError.value = null
  try {
    overview.value = await fetchUsageOverview({
      ...range,
      granularity: granularity.value,
    })
  } catch (e) {
    overviewError.value = e instanceof Error ? e.message : '总览加载失败'
    overview.value = null
  } finally {
    isLoadingOverview.value = false
  }
}

async function loadDetails() {
  const range = buildRange()
  if (!range) return
  isLoadingDetails.value = true
  detailsError.value = null
  try {
    details.value = await fetchUsageDetails({
      ...range,
      model: modelFilter.value || undefined,
      chain: chainFilter.value || undefined,
      page: page.value,
      page_size: PAGE_SIZE,
    })
  } catch (e) {
    detailsError.value = e instanceof Error ? e.message : '明细加载失败'
    details.value = null
  } finally {
    isLoadingDetails.value = false
  }
}

async function loadAll() {
  await Promise.all([loadOverview(), loadDetails()])
}

/** 应用时间范围预设（点击预设按钮 / 自定义查询） */
function selectPreset(key: RangePresetKey) {
  rangePreset.value = key
  applyRange()
}

function applyRange() {
  page.value = 1
  loadAll()
}

function applyGranularity(g: UsageGranularity) {
  granularity.value = g
  loadOverview()
}

/** 模型/链路筛选变更：重置到第一页并重拉明细 */
function applyDetailFilter() {
  page.value = 1
  loadDetails()
}

function goPage(p: number) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  loadDetails()
}

// ── Computed ──
const hasOverviewData = computed(() => (overview.value?.totals.calls ?? 0) > 0)
const totalTokens = computed(() => overview.value?.totals.total_tokens ?? 0)
const totalPages = computed(() => {
  const total = details.value?.total ?? 0
  return Math.min(DETAIL_MAX_PAGES, Math.max(1, Math.ceil(total / PAGE_SIZE)))
})
const modelOptions = computed(() => overview.value?.by_model.map((m) => m.name) ?? [])
const chainOptions = computed(() => overview.value?.by_chain.map((c) => c.name) ?? [])
const granularityLabel = computed(
  () => GRANULARITY_OPTIONS.find((g) => g.key === granularity.value)?.label ?? '按日',
)

// ── 格式化工具 ──
/** 整数千分位（zh-CN 分组） */
function fmtInt(n: number): string {
  return n.toLocaleString('zh-CN')
}

/** 耗时：<1s 显示毫秒，否则显示秒 */
function fmtDuration(ms: number): string {
  if (ms <= 0) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

/** 费用（元）：null（未配置单价）显示 — */
function fmtCost(cost: number | null): string {
  return cost === null ? '—' : `¥${cost.toFixed(2)}`
}

/** 占比（保留 1 位小数） */
function sharePct(value: number, total: number): number {
  return total > 0 ? Math.round((value / total) * 1000) / 10 : 0
}

/** 后端时间为 UTC naive（无时区后缀），补 Z 转本地时区展示 */
function fmtDateTime(iso: string): string {
  const d = new Date(iso.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

// ── 趋势图（ECharts：Token 用量柱状 + 调用次数折线，双 Y 轴） ──
const trendOption = computed(() => {
  const series = overview.value?.series ?? []
  const buckets = series.map((s) => s.bucket)
  return {
    tooltip: { trigger: 'axis' as const, textStyle: { fontSize: 13 } },
    legend: {
      bottom: 0,
      textStyle: { ...chartTextStyle, fontSize: 13 },
      itemWidth: 12,
      itemHeight: 12,
      itemGap: 24,
    },
    grid: { left: 8, right: 8, top: 24, bottom: 44, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: buckets,
      axisLabel: { ...chartTextStyle, rotate: buckets.length > 14 ? 30 : 0 },
      axisLine: { lineStyle: { color: BRAND.border } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: 'Token 用量',
        nameTextStyle: chartTextStyle,
        axisLabel: {
          ...chartTextStyle,
          formatter: (v: number) => (v >= 10000 ? `${v / 10000}万` : String(v)),
        },
        splitLine: { lineStyle: { color: BRAND.border, type: 'dashed' as const } },
      },
      {
        type: 'value' as const,
        name: '调用次数',
        nameTextStyle: chartTextStyle,
        axisLabel: chartTextStyle,
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: 'Token 用量',
        type: 'bar' as const,
        data: series.map((s) => s.total_tokens),
        itemStyle: { color: BRAND.accent, borderRadius: [3, 3, 0, 0] },
        barMaxWidth: 28,
      },
      {
        name: '调用次数',
        type: 'line' as const,
        yAxisIndex: 1,
        data: series.map((s) => s.calls),
        itemStyle: { color: BRAND.ink },
        lineStyle: { width: 2 },
        symbolSize: 6,
      },
    ],
  }
})

// ── CSV 导出 ──
async function downloadCsv() {
  const range = buildRange()
  if (!range) return
  isExporting.value = true
  exportMessage.value = null
  try {
    const blob = await exportUsageCsv({
      ...range,
      model: modelFilter.value || undefined,
      chain: chainFilter.value || undefined,
    })
    // Blob URL + 隐藏 <a> 触发浏览器下载（不跳页）
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `llm-usage-${range.start.slice(0, 10)}_${range.end.slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    exportMessage.value = '已导出 CSV'
  } catch (e) {
    exportMessage.value = e instanceof Error ? `导出失败：${e.message}` : '导出失败'
  } finally {
    isExporting.value = false
    if (exportMessageTimer) clearTimeout(exportMessageTimer)
    exportMessageTimer = setTimeout(() => (exportMessage.value = null), 5000)
  }
}

// ── Token 设置（与记忆管理页一致） ──
function openToken() {
  tokenInput.value = adminToken.value
  showToken.value = true
}

function saveToken() {
  const trimmed = tokenInput.value.trim()
  if (trimmed) {
    localStorage.setItem(TOKEN_KEY, trimmed)
    adminToken.value = trimmed
  }
  showToken.value = false
  // Token 可能刚生效/被修正，重拉数据
  loadAll()
}

onMounted(() => {
  loadAll()
})
</script>

<template>
  <div class="usage">
    <!-- 页头 -->
    <div class="page-header">
      <div class="header-text">
        <h1>成本仪表盘</h1>
        <p class="header-desc">LLM token 用量与费用报表：趋势 / 维度聚合 / 明细，支持 CSV 导出</p>
      </div>
      <div class="header-actions">
        <button
          class="secondary-btn"
          :disabled="isLoadingOverview || isLoadingDetails"
          @click="loadAll"
        >
          刷新
        </button>
        <button class="secondary-btn" :disabled="isExporting" @click="downloadCsv">
          {{ isExporting ? '导出中…' : '导出 CSV' }}
        </button>
        <span v-if="exportMessage" class="export-msg">{{ exportMessage }}</span>
        <button class="secondary-btn" @click="openToken">Token 设置</button>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar table-card">
      <div class="filter-group">
        <span class="filter-label">时间范围</span>
        <div class="preset-group">
          <button
            v-for="p in RANGE_PRESETS"
            :key="p.key"
            class="preset-btn"
            :class="{ active: rangePreset === p.key }"
            @click="selectPreset(p.key)"
          >
            {{ p.label }}
          </button>
        </div>
        <template v-if="rangePreset === 'custom'">
          <input v-model="customStart" class="date-input" type="date" />
          <span class="date-sep">至</span>
          <input v-model="customEnd" class="date-input" type="date" />
          <button class="primary-btn primary-btn-sm" @click="applyRange">查询</button>
        </template>
      </div>
      <div class="filter-group">
        <span class="filter-label">趋势粒度</span>
        <div class="preset-group">
          <button
            v-for="g in GRANULARITY_OPTIONS"
            :key="g.key"
            class="preset-btn"
            :class="{ active: granularity === g.key }"
            @click="applyGranularity(g.key)"
          >
            {{ g.label }}
          </button>
        </div>
      </div>
      <p v-if="rangeError" class="range-error">{{ rangeError }}</p>
    </div>

    <!-- 总览错误 -->
    <div v-if="overviewError" class="error-state table-card">
      <p>{{ overviewError }}</p>
      <button class="secondary-btn" @click="loadAll">重试</button>
    </div>

    <template v-else>
      <!-- KPI 汇总卡片 -->
      <div class="kpi-grid">
        <div class="kpi-card table-card">
          <span class="kpi-label">调用次数</span>
          <span class="kpi-value">{{ fmtInt(overview?.totals.calls ?? 0) }}</span>
        </div>
        <div class="kpi-card table-card">
          <span class="kpi-label">Token 总量</span>
          <span class="kpi-value">{{ fmtInt(overview?.totals.total_tokens ?? 0) }}</span>
          <span class="kpi-sub">
            输入 {{ fmtInt(overview?.totals.input_tokens ?? 0) }} · 输出
            {{ fmtInt(overview?.totals.output_tokens ?? 0) }}
          </span>
        </div>
        <div class="kpi-card table-card">
          <span class="kpi-label">平均耗时</span>
          <span class="kpi-value">{{ fmtDuration(overview?.totals.avg_duration_ms ?? 0) }}</span>
        </div>
        <div class="kpi-card table-card">
          <span class="kpi-label">估算费用</span>
          <span class="kpi-value">{{ fmtCost(overview?.totals.cost ?? null) }}</span>
          <span v-if="overview?.totals.cost === null" class="kpi-sub">未配置单价表</span>
        </div>
      </div>

      <!-- 无数据提示（空态优雅：各区块仍渲染，仅内容为空） -->
      <div v-if="!hasOverviewData && !isLoadingOverview" class="empty-banner table-card">
        <p class="empty-title">当前时间范围内暂无 LLM 调用记录</p>
        <p class="empty-hint">
          对话/推荐/评估等链路经埋点后自动记账；可切换时间范围或等待后续调用产生数据
        </p>
      </div>

      <!-- 趋势图 -->
      <div class="table-card chart-card">
        <div class="card-header">
          <h2>用量趋势（{{ granularityLabel }}粒度）</h2>
          <span v-if="overview" class="card-sub"
            >{{ overview.start }} ~ {{ overview.end }}（UTC）</span
          >
        </div>
        <div v-if="isLoadingOverview" class="chart-empty">加载中…</div>
        <VChart
          v-else-if="(overview?.series.length ?? 0) > 0"
          :option="trendOption"
          autoresize
          class="chart"
        />
        <div v-else class="chart-empty">暂无趋势数据</div>
      </div>

      <!-- 维度聚合：按模型 / 按链路 -->
      <div class="dim-grid">
        <div class="table-card dim-card">
          <div class="card-header">
            <h2>按模型</h2>
            <span class="card-sub">token 用量占比</span>
          </div>
          <table class="dim-table">
            <thead>
              <tr>
                <th>模型</th>
                <th class="num">调用</th>
                <th class="num">Token</th>
                <th>占比</th>
                <th class="num">耗时</th>
                <th class="num">费用</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="m in overview?.by_model ?? []" :key="m.name">
                <td class="dim-name">{{ m.name }}</td>
                <td class="num">{{ fmtInt(m.calls) }}</td>
                <td class="num">{{ fmtInt(m.total_tokens) }}</td>
                <td>
                  <div class="share-cell">
                    <div class="share-bar">
                      <div
                        class="share-fill"
                        :style="{ width: sharePct(m.total_tokens, totalTokens) + '%' }"
                      />
                    </div>
                    <span class="share-text">{{ sharePct(m.total_tokens, totalTokens) }}%</span>
                  </div>
                </td>
                <td class="num">{{ fmtDuration(m.avg_duration_ms) }}</td>
                <td class="num">{{ fmtCost(m.cost) }}</td>
              </tr>
              <tr v-if="(overview?.by_model.length ?? 0) === 0">
                <td colspan="6" class="empty-cell">暂无数据</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="table-card dim-card">
          <div class="card-header">
            <h2>按链路</h2>
            <span class="card-sub">对话 / 推荐 / 评估 / 压缩 / 管理员 agent 等</span>
          </div>
          <table class="dim-table">
            <thead>
              <tr>
                <th>链路</th>
                <th class="num">调用</th>
                <th class="num">Token</th>
                <th>占比</th>
                <th class="num">耗时</th>
                <th class="num">费用</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="c in overview?.by_chain ?? []" :key="c.name">
                <td class="dim-name">{{ c.name }}</td>
                <td class="num">{{ fmtInt(c.calls) }}</td>
                <td class="num">{{ fmtInt(c.total_tokens) }}</td>
                <td>
                  <div class="share-cell">
                    <div class="share-bar">
                      <div
                        class="share-fill"
                        :style="{ width: sharePct(c.total_tokens, totalTokens) + '%' }"
                      />
                    </div>
                    <span class="share-text">{{ sharePct(c.total_tokens, totalTokens) }}%</span>
                  </div>
                </td>
                <td class="num">{{ fmtDuration(c.avg_duration_ms) }}</td>
                <td class="num">{{ fmtCost(c.cost) }}</td>
              </tr>
              <tr v-if="(overview?.by_chain.length ?? 0) === 0">
                <td colspan="6" class="empty-cell">暂无数据</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 明细表 -->
      <div class="table-card detail-card">
        <div class="card-header detail-header">
          <div class="header-left">
            <h2>用量明细</h2>
            <span class="card-sub">共 {{ details?.total ?? 0 }} 条</span>
          </div>
          <div class="detail-filters">
            <select v-model="modelFilter" class="filter-select" @change="applyDetailFilter">
              <option value="">全部模型</option>
              <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
            </select>
            <select v-model="chainFilter" class="filter-select" @change="applyDetailFilter">
              <option value="">全部链路</option>
              <option v-for="c in chainOptions" :key="c" :value="c">{{ c }}</option>
            </select>
          </div>
        </div>

        <div v-if="detailsError" class="error-state">
          <p>{{ detailsError }}</p>
          <button class="secondary-btn" @click="loadDetails">重试</button>
        </div>

        <div v-else-if="isLoadingDetails && (details?.items.length ?? 0) === 0" class="empty-cell">
          加载中…
        </div>

        <div v-else-if="(details?.items.length ?? 0) === 0" class="empty-cell">
          <p class="empty-title">暂无明细数据</p>
          <p class="empty-hint">可调整时间范围或筛选条件后重试</p>
        </div>

        <table v-else class="detail-table">
          <thead>
            <tr>
              <th>时间（本地）</th>
              <th>模型</th>
              <th>链路</th>
              <th class="num">Input</th>
              <th class="num">Output</th>
              <th class="num">Total</th>
              <th class="num">耗时</th>
              <th>用户</th>
              <th class="num">费用</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in details?.items ?? []" :key="item.id">
              <td class="mono">{{ fmtDateTime(item.created_at) }}</td>
              <td>{{ item.model }}</td>
              <td>
                <span class="chain-badge">{{ item.chain }}</span>
              </td>
              <td class="num">{{ fmtInt(item.input_tokens) }}</td>
              <td class="num">{{ fmtInt(item.output_tokens) }}</td>
              <td class="num strong">{{ fmtInt(item.total_tokens) }}</td>
              <td class="num">{{ fmtDuration(item.duration_ms) }}</td>
              <td class="mono">{{ item.user ?? '—' }}</td>
              <td class="num">{{ fmtCost(item.cost) }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="(details?.total ?? 0) > 0" class="pagination">
          <button class="page-btn" :disabled="page === 1" @click="goPage(page - 1)">上一页</button>
          <span class="page-info">第 {{ page }} / {{ totalPages }} 页</span>
          <button class="page-btn" :disabled="page >= totalPages" @click="goPage(page + 1)">
            下一页
          </button>
        </div>
      </div>
    </template>

    <!-- Token 设置弹窗 -->
    <Teleport to="body">
      <div v-if="showToken" class="modal-overlay" @click.self="showToken = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>管理员 Token</h2>
            <button class="icon-btn" @click="showToken = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>X-Admin-Token</label>
              <input
                v-model="tokenInput"
                type="password"
                :placeholder="adminToken ? '已设置' : '请输入 Token'"
              />
              <p class="form-hint">Token 保存在浏览器本地，用于调用管理接口。</p>
            </div>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showToken = false">取消</button>
            <button class="primary-btn" @click="saveToken">保存</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.usage {
  padding: var(--space-xl) var(--space-2xl);
  max-width: 1400px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--space-xl);
  gap: var(--space-md);
  flex-wrap: wrap;
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

.header-actions {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.export-msg {
  font-size: var(--text-small);
  color: var(--jade);
}

.primary-btn,
.secondary-btn {
  padding: var(--space-sm) var(--space-lg);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.primary-btn {
  background: var(--ink);
  color: var(--surface);
}

.primary-btn:hover {
  background: var(--jade);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary-btn-sm {
  padding: var(--space-xs) var(--space-md);
}

.secondary-btn {
  background: transparent;
  color: var(--ink);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
}

.secondary-btn:hover {
  background: var(--overlay);
}

.secondary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 通用卡片 */
.table-card {
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-card);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--mist);
  gap: var(--space-md);
  flex-wrap: wrap;
}

.card-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--ink);
}

.card-sub {
  font-size: var(--text-caption);
  color: var(--slate);
  font-family: var(--font-mono);
}

/* 筛选栏 */
.filter-bar {
  padding: var(--space-md) var(--space-lg);
  margin-bottom: var(--space-lg);
  display: flex;
  align-items: center;
  gap: var(--space-xl);
  flex-wrap: wrap;
}

.filter-group {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.filter-label {
  font-size: var(--text-small);
  color: var(--slate);
}

.preset-group {
  display: flex;
  gap: 2px;
  background: var(--overlay);
  border-radius: var(--radius-sm);
  padding: 2px;
}

.preset-btn {
  padding: var(--space-xs) var(--space-md);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--slate);
  font-size: var(--text-small);
  cursor: pointer;
  transition:
    background var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}

.preset-btn:hover {
  color: var(--ink);
}

.preset-btn.active {
  background: var(--surface);
  color: var(--ink);
  font-weight: 500;
  box-shadow: 0 1px 2px rgba(30, 42, 38, 0.08);
}

.date-input {
  padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-small);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--font-body);
}

.date-sep {
  font-size: var(--text-small);
  color: var(--slate);
}

.range-error {
  flex-basis: 100%;
  margin: 0;
  color: var(--jade);
  font-size: var(--text-small);
}

/* KPI 卡片 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
}

.kpi-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-lg);
}

.kpi-label {
  font-size: var(--text-small);
  color: var(--slate);
}

.kpi-value {
  font-family: var(--font-mono);
  font-size: var(--text-title);
  font-weight: 600;
  color: var(--ink);
  word-break: break-all;
}

.kpi-sub {
  font-size: var(--text-caption);
  color: var(--slate);
}

/* 空数据提示 */
.empty-banner {
  padding: var(--space-lg);
  margin-bottom: var(--space-lg);
  text-align: center;
  color: var(--slate);
}

.empty-banner .empty-title {
  margin: 0 0 var(--space-xs);
  font-weight: 500;
  color: var(--ink);
}

.empty-banner .empty-hint {
  margin: 0;
  font-size: var(--text-small);
}

/* 趋势图 */
.chart-card {
  margin-bottom: var(--space-lg);
}

.chart {
  height: 320px;
  padding: var(--space-md) var(--space-lg) var(--space-xl);
}

.chart-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 320px;
  color: var(--slate);
  font-size: var(--text-body);
}

/* 维度聚合 */
.dim-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
}

.dim-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-small);
}

.dim-table th,
.dim-table td {
  padding: var(--space-sm) var(--space-md);
  text-align: left;
  border-bottom: 1px solid var(--mist);
  color: var(--ink);
}

.dim-table th {
  font-weight: 600;
  color: var(--ink);
  background: var(--jade-soft);
  white-space: nowrap;
}

.dim-table tr:last-child td {
  border-bottom: none;
}

.dim-table .num {
  text-align: right;
  font-family: var(--font-mono);
  white-space: nowrap;
}

.dim-name {
  font-weight: 500;
  word-break: break-all;
}

/* 占比条 */
.share-cell {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  min-width: 110px;
}

.share-bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: var(--overlay);
  overflow: hidden;
}

.share-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--jade);
}

.share-text {
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--slate);
  width: 42px;
  text-align: right;
}

/* 明细表 */
.detail-card {
  margin-bottom: var(--space-xl);
}

.detail-header {
  flex-wrap: wrap;
}

.header-left {
  display: flex;
  align-items: center;
  gap: var(--space-md);
}

.detail-filters {
  display: flex;
  gap: var(--space-sm);
}

.filter-select {
  padding: var(--space-xs) var(--space-sm);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-small);
  background: var(--surface);
  color: var(--ink);
  font-family: var(--font-body);
}

.detail-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-small);
}

.detail-table th,
.detail-table td {
  padding: var(--space-sm) var(--space-md);
  text-align: left;
  border-bottom: 1px solid var(--mist);
  color: var(--ink);
  white-space: nowrap;
}

.detail-table th {
  font-weight: 600;
  color: var(--ink);
  background: var(--jade-soft);
}

.detail-table tr:last-child td {
  border-bottom: none;
}

.detail-table .num {
  text-align: right;
  font-family: var(--font-mono);
}

.detail-table .strong {
  font-weight: 600;
}

.mono {
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--slate);
}

.chain-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: var(--radius-md);
  font-size: var(--text-caption);
  background: var(--jade-soft);
  color: var(--jade);
}

/* 分页 */
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--mist);
}

.page-btn {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  background: var(--surface);
  color: var(--ink);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-small);
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-btn:hover:not(:disabled) {
  background: var(--overlay);
}

.page-info {
  font-size: var(--text-small);
  color: var(--slate);
}

/* 空态 / 错误态 */
.empty-cell {
  text-align: center;
  color: var(--slate);
  padding: var(--space-3xl);
}

.empty-title {
  margin: 0 0 var(--space-xs);
  font-weight: 500;
  color: var(--ink);
}

.empty-hint {
  margin: 0;
  font-size: var(--text-small);
  color: var(--slate);
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

/* Modal（与记忆管理页一致） */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(30, 42, 38, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-lg);
  z-index: 100;
}

.modal-panel {
  background: var(--surface);
  border-radius: var(--radius-md);
  width: 100%;
  max-width: 560px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-pop);
}

.modal-small {
  max-width: 420px;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-lg) var(--space-xl);
  border-bottom: 1px solid var(--mist);
}

.modal-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--ink);
}

.icon-btn {
  background: transparent;
  border: none;
  font-size: var(--text-title);
  color: var(--slate);
  cursor: pointer;
  line-height: 1;
}

.icon-btn:hover {
  color: var(--ink);
}

.modal-body {
  padding: var(--space-lg) var(--space-xl);
  overflow-y: auto;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-sm);
  padding: var(--space-lg) var(--space-xl);
  border-top: 1px solid var(--mist);
}

.form-row {
  margin-bottom: var(--space-md);
}

.form-row label {
  display: block;
  font-size: var(--text-small);
  color: var(--slate);
  margin-bottom: var(--space-xs);
}

.form-row input {
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-body);
  background: var(--surface);
  color: var(--ink);
  box-sizing: border-box;
  font-family: var(--font-body);
}

.form-row input:focus {
  outline: none;
  border-color: var(--slate);
}

.form-hint {
  font-size: var(--text-caption);
  color: var(--slate);
  margin: var(--space-xs) 0 0;
}

@media (max-width: 1080px) {
  .kpi-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .dim-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .usage {
    padding: var(--space-lg);
  }

  .page-header {
    flex-direction: column;
  }

  .kpi-grid {
    grid-template-columns: 1fr;
  }

  .detail-table {
    font-size: var(--text-caption);
  }
}
</style>
