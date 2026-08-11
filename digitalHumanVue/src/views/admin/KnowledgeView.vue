<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { adminApiHeaders } from '@/utils/adminHeaders'
import { API_ADMIN_KNOWLEDGE, API_ADMIN_KNOWLEDGE_IMPORT } from '@/api'
import {
  createScenicRoute,
  createScenicSpot,
  deleteScenicRoute,
  deleteScenicSpot,
  fetchScenicRoutes,
  fetchScenicSpots,
  updateScenicRoute,
  updateScenicSpot,
} from '@/api/scenic'
import type { KnowledgeItem, KnowledgeListResponse, KnowledgeFormData } from '@/types/knowledge'
import type { ScenicRoute, ScenicSpot } from '@/types/scenic'
import { SCENIC_DIFFICULTIES } from '@/types/scenic'

const CATEGORIES = ['讲解词', 'FAQ', '景点资料']
const PAGE_SIZE = 10

/** 页签：知识文档（knowledge_base 表）/ 景点数据 / 路线数据（app/data JSON） */
type ScenicTab = 'knowledge' | 'spots' | 'routes'
const activeTab = ref<ScenicTab>('knowledge')

// ── 知识文档（既有 CRUD，knowledge_base 表） ──
const items = ref<KnowledgeItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(PAGE_SIZE)
const isLoading = ref(false)
const error = ref<string | null>(null)

const keyword = ref('')
const selectedCategory = ref('')
const debouncedKeyword = ref('')

const showEditor = ref(false)
const editingItem = ref<KnowledgeItem | null>(null)
const form = ref<KnowledgeFormData>({
  title: '',
  category: '',
  content: '',
  tags: '',
})
const isSaving = ref(false)
const editorError = ref<string | null>(null)
const activeTabEditor = ref<'write' | 'preview'>('write')

const showImport = ref(false)
const importFile = ref<File | null>(null)
const importCategory = ref('景点资料')
const isImporting = ref(false)
const importError = ref<string | null>(null)
const notice = ref<string | null>(null)

const showDelete = ref(false)
const deletingItem = ref<KnowledgeItem | null>(null)

let debounceTimer: ReturnType<typeof setTimeout> | null = null

// ── 景点数据（app/data/spots.json，agent search_spots 单一来源） ──
const spots = ref<ScenicSpot[]>([])
const spotsLoading = ref(false)
const spotsError = ref<string | null>(null)

const showSpotEditor = ref(false)
const editingSpot = ref<ScenicSpot | null>(null)
const spotForm = ref({
  name: '',
  description: '',
  tagsText: '',
  durationMinutes: 60,
  difficulty: '轻松' as string,
  location: '',
  highlightsText: '',
})
const spotSaving = ref(false)
const spotEditorError = ref<string | null>(null)

const showSpotDelete = ref(false)
const deletingSpot = ref<ScenicSpot | null>(null)

// ── 路线数据（app/data/routes.json，agent search_routes 单一来源） ──
const routes = ref<ScenicRoute[]>([])
const routesLoading = ref(false)
const routesError = ref<string | null>(null)

const showRouteEditor = ref(false)
const editingRoute = ref<ScenicRoute | null>(null)
const routeForm = ref({
  name: '',
  description: '',
  spotIds: [] as string[],
  tagsText: '',
  suitableForText: '',
  totalDurationMinutes: 180,
  difficulty: '轻松' as string,
  recommendReason: '',
})
const routeSaving = ref(false)
const routeEditorError = ref<string | null>(null)

const showRouteDelete = ref(false)
const deletingRoute = ref<ScenicRoute | null>(null)

const scenicNotice = ref<string | null>(null)

// ── Computed ──
const totalPages = computed(() => Math.ceil(total.value / pageSize.value))

const renderedPreview = computed(() => {
  const raw = marked.parse(form.value.content || '', { async: false }) as string
  return DOMPurify.sanitize(raw)
})

/** 路线编辑弹窗中可勾选的景点（按 id 索引，供勾选还原） */
const spotsById = computed(() => new Map(spots.value.map((s) => [s.id, s])))

// ── Watchers ──
watch(keyword, (value) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    debouncedKeyword.value = value
    page.value = 1
    fetchList()
  }, 300)
})

watch(selectedCategory, () => {
  page.value = 1
  fetchList()
})

// ── 通用工具 ──
function getHeaders(): Record<string, string> {
  return adminApiHeaders()
}

function splitList(text: string): string[] {
  return text
    .split(/[,，]/)
    .map((t) => t.trim())
    .filter(Boolean)
}

// ── 知识文档 CRUD（既有） ──
async function fetchList() {
  isLoading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.append('page', String(page.value))
    params.append('page_size', String(pageSize.value))
    if (selectedCategory.value) params.append('category', selectedCategory.value)
    if (debouncedKeyword.value) params.append('keyword', debouncedKeyword.value)

    const data = await fetchWithCodeEnvelope<KnowledgeListResponse>(
      `${API_ADMIN_KNOWLEDGE}?${params.toString()}`,
      { headers: getHeaders() },
    )
    items.value = data.items
    total.value = data.total
    page.value = data.page
    pageSize.value = data.page_size
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    isLoading.value = false
  }
}

function openCreate() {
  editingItem.value = null
  form.value = { title: '', category: '讲解词', content: '', tags: '' }
  activeTabEditor.value = 'write'
  editorError.value = null
  showEditor.value = true
}

function openEdit(item: KnowledgeItem) {
  editingItem.value = item
  form.value = {
    title: item.title,
    category: item.category,
    content: item.content,
    tags: (item.tags ?? []).join(', '),
  }
  activeTabEditor.value = 'write'
  editorError.value = null
  showEditor.value = true
}

function validateForm(): boolean {
  if (!form.value.title.trim()) {
    editorError.value = '请输入标题'
    return false
  }
  if (!form.value.category) {
    editorError.value = '请选择分类'
    return false
  }
  if (!form.value.content.trim()) {
    editorError.value = '请输入内容'
    return false
  }
  editorError.value = null
  return true
}

async function saveForm() {
  if (!validateForm()) return
  isSaving.value = true
  editorError.value = null

  const payload = {
    title: form.value.title.trim(),
    category: form.value.category,
    content: form.value.content.trim(),
    tags: splitList(form.value.tags),
  }

  try {
    if (editingItem.value) {
      await fetchWithCodeEnvelope<KnowledgeItem>(`${API_ADMIN_KNOWLEDGE}/${editingItem.value.id}`, {
        method: 'PUT',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      })
    } else {
      await fetchWithCodeEnvelope<KnowledgeItem>(API_ADMIN_KNOWLEDGE, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      })
    }
    showEditor.value = false
    await fetchList()
    notice.value = '保存成功，已同步前台数字人知识检索'
  } catch (e) {
    editorError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    isSaving.value = false
  }
}

function confirmDelete(item: KnowledgeItem) {
  deletingItem.value = item
  showDelete.value = true
}

async function doDelete() {
  if (!deletingItem.value) return
  try {
    await fetchWithCodeEnvelope(`${API_ADMIN_KNOWLEDGE}/${deletingItem.value.id}`, {
      method: 'DELETE',
      headers: adminApiHeaders(false),
    })
    showDelete.value = false
    await fetchList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
    showDelete.value = false
  }
}

function onFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  importFile.value = target.files?.[0] ?? null
}

async function doImport() {
  if (!importFile.value) {
    importError.value = '请选择文件'
    return
  }
  isImporting.value = true
  importError.value = null
  try {
    const body = new FormData()
    body.append('file', importFile.value)
    body.append('default_category', importCategory.value)
    const data = await fetchWithCodeEnvelope<{ imported_count: number; skipped_count: number }>(
      API_ADMIN_KNOWLEDGE_IMPORT,
      {
        method: 'POST',
        headers: adminApiHeaders(false),
        body,
      },
    )
    showImport.value = false
    importFile.value = null
    let msg = `成功导入 ${data.imported_count} 条`
    if (data.skipped_count > 0) {
      msg += `，跳过 ${data.skipped_count} 条无效数据`
    }
    msg += '，已同步前台数字人知识检索'
    notice.value = msg
    await fetchList()
  } catch (e) {
    importError.value = e instanceof Error ? e.message : '导入失败'
  } finally {
    isImporting.value = false
  }
}

function goPage(p: number) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  fetchList()
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// ── 景点数据 CRUD（app/data/spots.json） ──
async function fetchSpots() {
  spotsLoading.value = true
  spotsError.value = null
  try {
    spots.value = await fetchScenicSpots()
  } catch (e) {
    spotsError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    spotsLoading.value = false
  }
}

function openSpotCreate() {
  editingSpot.value = null
  spotForm.value = {
    name: '',
    description: '',
    tagsText: '',
    durationMinutes: 60,
    difficulty: '轻松',
    location: '',
    highlightsText: '',
  }
  spotEditorError.value = null
  showSpotEditor.value = true
}

function openSpotEdit(spot: ScenicSpot) {
  editingSpot.value = spot
  spotForm.value = {
    name: spot.name,
    description: spot.description,
    tagsText: spot.tags.join(', '),
    durationMinutes: spot.duration_minutes,
    difficulty: spot.difficulty,
    location: spot.location,
    highlightsText: spot.highlights.join(', '),
  }
  spotEditorError.value = null
  showSpotEditor.value = true
}

function validateSpotForm(): boolean {
  if (!spotForm.value.name.trim()) {
    spotEditorError.value = '请输入景点名称'
    return false
  }
  if (!spotForm.value.description.trim()) {
    spotEditorError.value = '请输入景点介绍'
    return false
  }
  if (!spotForm.value.durationMinutes || spotForm.value.durationMinutes < 1) {
    spotEditorError.value = '游览时长需为正整数'
    return false
  }
  spotEditorError.value = null
  return true
}

async function saveSpot() {
  if (!validateSpotForm()) return
  spotSaving.value = true
  spotEditorError.value = null
  const payload = {
    name: spotForm.value.name.trim(),
    description: spotForm.value.description.trim(),
    tags: splitList(spotForm.value.tagsText),
    duration_minutes: Number(spotForm.value.durationMinutes),
    difficulty: spotForm.value.difficulty,
    location: spotForm.value.location.trim(),
    highlights: splitList(spotForm.value.highlightsText),
  }
  try {
    if (editingSpot.value) {
      await updateScenicSpot(editingSpot.value.id, payload)
    } else {
      await createScenicSpot(payload)
    }
    showSpotEditor.value = false
    await fetchSpots()
    scenicNotice.value = '景点已保存，数字人导览 Agent 实时生效'
  } catch (e) {
    spotEditorError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    spotSaving.value = false
  }
}

function confirmSpotDelete(spot: ScenicSpot) {
  deletingSpot.value = spot
  showSpotDelete.value = true
}

async function doSpotDelete() {
  if (!deletingSpot.value) return
  try {
    await deleteScenicSpot(deletingSpot.value.id)
    showSpotDelete.value = false
    await fetchSpots()
    scenicNotice.value = '景点已删除，数字人导览 Agent 实时生效'
  } catch (e) {
    spotsError.value = e instanceof Error ? e.message : '删除失败'
    showSpotDelete.value = false
  }
}

// ── 路线数据 CRUD（app/data/routes.json） ──
async function fetchRoutes() {
  routesLoading.value = true
  routesError.value = null
  try {
    routes.value = await fetchScenicRoutes()
  } catch (e) {
    routesError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    routesLoading.value = false
  }
}

function openRouteCreate() {
  editingRoute.value = null
  routeForm.value = {
    name: '',
    description: '',
    spotIds: [],
    tagsText: '',
    suitableForText: '',
    totalDurationMinutes: 180,
    difficulty: '轻松',
    recommendReason: '',
  }
  routeEditorError.value = null
  showRouteEditor.value = true
}

function openRouteEdit(route: ScenicRoute) {
  editingRoute.value = route
  routeForm.value = {
    name: route.name,
    description: route.description,
    spotIds: [...route.spot_ids],
    tagsText: route.tags.join(', '),
    suitableForText: route.suitable_for.join(', '),
    totalDurationMinutes: route.total_duration_minutes,
    difficulty: route.difficulty,
    recommendReason: route.recommend_reason,
  }
  routeEditorError.value = null
  showRouteEditor.value = true
}

function toggleRouteSpot(spotId: string) {
  const ids = routeForm.value.spotIds
  const idx = ids.indexOf(spotId)
  if (idx >= 0) {
    ids.splice(idx, 1)
  } else {
    ids.push(spotId)
  }
}

function validateRouteForm(): boolean {
  if (!routeForm.value.name.trim()) {
    routeEditorError.value = '请输入路线名称'
    return false
  }
  if (!routeForm.value.description.trim()) {
    routeEditorError.value = '请输入路线介绍'
    return false
  }
  if (!routeForm.value.totalDurationMinutes || routeForm.value.totalDurationMinutes < 1) {
    routeEditorError.value = '总时长需为正整数'
    return false
  }
  routeEditorError.value = null
  return true
}

async function saveRoute() {
  if (!validateRouteForm()) return
  routeSaving.value = true
  routeEditorError.value = null
  const payload = {
    name: routeForm.value.name.trim(),
    description: routeForm.value.description.trim(),
    spot_ids: routeForm.value.spotIds,
    tags: splitList(routeForm.value.tagsText),
    suitable_for: splitList(routeForm.value.suitableForText),
    total_duration_minutes: Number(routeForm.value.totalDurationMinutes),
    difficulty: routeForm.value.difficulty,
    recommend_reason: routeForm.value.recommendReason.trim(),
  }
  try {
    if (editingRoute.value) {
      await updateScenicRoute(editingRoute.value.id, payload)
    } else {
      await createScenicRoute(payload)
    }
    showRouteEditor.value = false
    await fetchRoutes()
    scenicNotice.value = '路线已保存，数字人导览 Agent 实时生效'
  } catch (e) {
    routeEditorError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    routeSaving.value = false
  }
}

function confirmRouteDelete(route: ScenicRoute) {
  deletingRoute.value = route
  showRouteDelete.value = true
}

async function doRouteDelete() {
  if (!deletingRoute.value) return
  try {
    await deleteScenicRoute(deletingRoute.value.id)
    showRouteDelete.value = false
    await fetchRoutes()
    scenicNotice.value = '路线已删除，数字人导览 Agent 实时生效'
  } catch (e) {
    routesError.value = e instanceof Error ? e.message : '删除失败'
    showRouteDelete.value = false
  }
}

/** 路线途经景点名称展示（未找到的 id 原样透出，前端不崩） */
function routeSpotNames(route: ScenicRoute): string {
  const names = route.spot_ids.map((id) => spotsById.value.get(id)?.name ?? id)
  return names.join(' → ')
}

onMounted(() => {
  fetchList()
  fetchSpots()
  fetchRoutes()
})
</script>

<template>
  <div class="knowledge">
    <div class="page-header">
      <div class="header-text">
        <h1>知识库管理</h1>
        <p class="header-desc">管理景区讲解词、FAQ 与导览 Agent 的景点/路线数据</p>
      </div>
      <div class="header-actions">
        <template v-if="activeTab === 'knowledge'">
          <button class="secondary-btn" @click="showImport = true">批量导入</button>
          <button class="primary-btn" @click="openCreate">+ 新建条目</button>
        </template>
        <template v-else-if="activeTab === 'spots'">
          <button class="primary-btn" @click="openSpotCreate">+ 新建景点</button>
        </template>
        <template v-else>
          <button class="primary-btn" @click="openRouteCreate">+ 新建路线</button>
        </template>
      </div>
    </div>

    <!-- Tabs -->
    <div class="scenic-tabs">
      <button
        :class="['scenic-tab', { active: activeTab === 'knowledge' }]"
        @click="activeTab = 'knowledge'"
      >
        知识文档
      </button>
      <button
        :class="['scenic-tab', { active: activeTab === 'spots' }]"
        @click="activeTab = 'spots'"
      >
        景点数据
      </button>
      <button
        :class="['scenic-tab', { active: activeTab === 'routes' }]"
        @click="activeTab = 'routes'"
      >
        路线数据
      </button>
    </div>

    <!-- 通用提示条 -->
    <div v-if="scenicNotice" class="import-result">
      <span>{{ scenicNotice }}</span>
      <button class="icon-btn" @click="scenicNotice = null">✕</button>
    </div>

    <!-- ══════════ 知识文档页签 ══════════ -->
    <template v-if="activeTab === 'knowledge'">
      <!-- Filters -->
      <div class="filter-bar">
        <select v-model="selectedCategory" class="filter-select">
          <option value="">全部分类</option>
          <option v-for="cat in CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
        </select>
        <input v-model="keyword" class="filter-input" type="text" placeholder="搜索标题或内容…" />
      </div>

      <!-- Import Result -->
      <div v-if="notice" class="import-result">
        <span>{{ notice }}</span>
        <button class="icon-btn" @click="notice = null">✕</button>
      </div>

      <!-- Error -->
      <div v-if="error" class="error-state">
        <p>{{ error }}</p>
        <button class="secondary-btn" @click="fetchList">重试</button>
      </div>

      <!-- Table -->
      <div v-else class="table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th class="col-title">标题</th>
              <th class="col-category">分类</th>
              <th class="col-tags">标签</th>
              <th class="col-date">更新时间</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="isLoading && items.length === 0">
              <td colspan="5" class="empty-cell">加载中…</td>
            </tr>
            <tr v-else-if="items.length === 0">
              <td colspan="5" class="empty-cell">暂无知识库条目</td>
            </tr>
            <tr v-for="item in items" :key="item.id">
              <td class="cell-title">{{ item.title }}</td>
              <td>
                <span class="category-badge">{{ item.category }}</span>
              </td>
              <td>
                <span v-if="!item.tags?.length" class="tag-empty">—</span>
                <span v-for="tag in item.tags" :key="tag" class="tag-badge">{{ tag }}</span>
              </td>
              <td class="cell-date">{{ formatDate(item.updated_at) }}</td>
              <td>
                <div class="action-group">
                  <button class="text-btn" @click="openEdit(item)">编辑</button>
                  <button class="text-btn danger" @click="confirmDelete(item)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- Pagination -->
        <div v-if="totalPages > 1" class="pagination">
          <button class="page-btn" :disabled="page === 1" @click="goPage(page - 1)">上一页</button>
          <span class="page-info">第 {{ page }} / {{ totalPages }} 页，共 {{ total }} 条</span>
          <button class="page-btn" :disabled="page === totalPages" @click="goPage(page + 1)">
            下一页
          </button>
        </div>
      </div>
    </template>

    <!-- ══════════ 景点数据页签 ══════════ -->
    <template v-else-if="activeTab === 'spots'">
      <div v-if="spotsError" class="error-state">
        <p>{{ spotsError }}</p>
        <button class="secondary-btn" @click="fetchSpots">重试</button>
      </div>
      <div v-else class="table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th class="col-title">景点名称</th>
              <th class="col-tags">标签</th>
              <th class="col-duration">时长</th>
              <th class="col-difficulty">难度</th>
              <th class="col-location">位置</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="spotsLoading && spots.length === 0">
              <td colspan="6" class="empty-cell">加载中…</td>
            </tr>
            <tr v-else-if="spots.length === 0">
              <td colspan="6" class="empty-cell">暂无景点数据（数字人导览 Agent 将无法回答景点问题）</td>
            </tr>
            <tr v-for="spot in spots" :key="spot.id">
              <td class="cell-title">
                {{ spot.name }}
                <span class="cell-sub">{{ spot.id }}</span>
              </td>
              <td>
                <span v-for="tag in spot.tags" :key="tag" class="tag-badge">{{ tag }}</span>
              </td>
              <td class="cell-date">{{ Math.round(spot.duration_minutes / 60 * 10) / 10 }} 小时</td>
              <td>
                <span class="category-badge">{{ spot.difficulty }}</span>
              </td>
              <td class="cell-location">{{ spot.location || '—' }}</td>
              <td>
                <div class="action-group">
                  <button class="text-btn" @click="openSpotEdit(spot)">编辑</button>
                  <button class="text-btn danger" @click="confirmSpotDelete(spot)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- ══════════ 路线数据页签 ══════════ -->
    <template v-else>
      <div v-if="routesError" class="error-state">
        <p>{{ routesError }}</p>
        <button class="secondary-btn" @click="fetchRoutes">重试</button>
      </div>
      <div v-else class="table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th class="col-title">路线名称</th>
              <th class="col-tags">标签</th>
              <th class="col-suitable">适合人群</th>
              <th class="col-duration">总时长</th>
              <th class="col-difficulty">难度</th>
              <th class="col-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="routesLoading && routes.length === 0">
              <td colspan="6" class="empty-cell">加载中…</td>
            </tr>
            <tr v-else-if="routes.length === 0">
              <td colspan="6" class="empty-cell">暂无路线数据（数字人导览 Agent 将无法回答路线问题）</td>
            </tr>
            <tr v-for="route in routes" :key="route.id">
              <td class="cell-title">
                {{ route.name }}
                <span class="cell-sub">{{ routeSpotNames(route) }}</span>
              </td>
              <td>
                <span v-for="tag in route.tags" :key="tag" class="tag-badge">{{ tag }}</span>
              </td>
              <td class="cell-location">{{ route.suitable_for.join('、') || '—' }}</td>
              <td class="cell-date">{{ Math.round(route.total_duration_minutes / 60 * 10) / 10 }} 小时</td>
              <td>
                <span class="category-badge">{{ route.difficulty }}</span>
              </td>
              <td>
                <div class="action-group">
                  <button class="text-btn" @click="openRouteEdit(route)">编辑</button>
                  <button class="text-btn danger" @click="confirmRouteDelete(route)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <!-- Editor Modal（知识文档） -->
    <Teleport to="body">
      <div v-if="showEditor" class="modal-overlay" @click.self="showEditor = false">
        <div class="modal-panel modal-large">
          <div class="modal-header">
            <h2>{{ editingItem ? '编辑条目' : '新建条目' }}</h2>
            <button class="icon-btn" @click="showEditor = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>标题</label>
              <input v-model="form.title" type="text" placeholder="请输入标题" />
            </div>
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>分类</label>
                <select v-model="form.category">
                  <option v-for="cat in CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
                </select>
              </div>
              <div class="form-field">
                <label>标签（用逗号分隔）</label>
                <input v-model="form.tags" type="text" placeholder="例如：自然, 瀑布" />
              </div>
            </div>
            <div class="editor-tabs">
              <button
                :class="['tab-btn', { active: activeTabEditor === 'write' }]"
                @click="activeTabEditor = 'write'"
              >
                编辑
              </button>
              <button
                :class="['tab-btn', { active: activeTabEditor === 'preview' }]"
                @click="activeTabEditor = 'preview'"
              >
                预览
              </button>
            </div>
            <div class="editor-content">
              <textarea
                v-if="activeTabEditor === 'write'"
                v-model="form.content"
                placeholder="支持 Markdown 语法"
              />
              <div v-else class="markdown-preview" v-html="renderedPreview" />
            </div>
            <p v-if="editorError" class="form-error">{{ editorError }}</p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showEditor = false">取消</button>
            <button class="primary-btn" :disabled="isSaving" @click="saveForm">
              {{ isSaving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Spot Editor Modal -->
    <Teleport to="body">
      <div v-if="showSpotEditor" class="modal-overlay" @click.self="showSpotEditor = false">
        <div class="modal-panel modal-large">
          <div class="modal-header">
            <h2>{{ editingSpot ? '编辑景点' : '新建景点' }}</h2>
            <button class="icon-btn" @click="showSpotEditor = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>景点名称</label>
                <input v-model="spotForm.name" type="text" placeholder="如：灵山大佛" />
              </div>
              <div class="form-field">
                <label>难度</label>
                <select v-model="spotForm.difficulty">
                  <option v-for="d in SCENIC_DIFFICULTIES" :key="d" :value="d">{{ d }}</option>
                </select>
              </div>
            </div>
            <div class="form-row">
              <label>景点介绍</label>
              <textarea v-model="spotForm.description" placeholder="数字人导览 Agent 据此回答景点问题" />
            </div>
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>标签（用逗号分隔）</label>
                <input v-model="spotForm.tagsText" type="text" placeholder="如：历史人文, 祈福" />
              </div>
              <div class="form-field">
                <label>游览时长（分钟）</label>
                <input v-model.number="spotForm.durationMinutes" type="number" min="1" />
              </div>
            </div>
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>具体位置</label>
                <input v-model="spotForm.location" type="text" placeholder="如：景区中轴线核心" />
              </div>
              <div class="form-field">
                <label>游玩亮点（用逗号分隔）</label>
                <input v-model="spotForm.highlightsText" type="text" placeholder="如：抱佛脚, 登云道" />
              </div>
            </div>
            <p class="form-hint">保存后数字人导览 Agent 与首页活数据实时生效（同一份数据源）。</p>
            <p v-if="spotEditorError" class="form-error">{{ spotEditorError }}</p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showSpotEditor = false">取消</button>
            <button class="primary-btn" :disabled="spotSaving" @click="saveSpot">
              {{ spotSaving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Route Editor Modal -->
    <Teleport to="body">
      <div v-if="showRouteEditor" class="modal-overlay" @click.self="showRouteEditor = false">
        <div class="modal-panel modal-large">
          <div class="modal-header">
            <h2>{{ editingRoute ? '编辑路线' : '新建路线' }}</h2>
            <button class="icon-btn" @click="showRouteEditor = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>路线名称</label>
                <input v-model="routeForm.name" type="text" placeholder="如：历史文化深度游" />
              </div>
              <div class="form-field">
                <label>难度</label>
                <select v-model="routeForm.difficulty">
                  <option v-for="d in SCENIC_DIFFICULTIES" :key="d" :value="d">{{ d }}</option>
                </select>
              </div>
            </div>
            <div class="form-row">
              <label>路线介绍</label>
              <textarea v-model="routeForm.description" placeholder="数字人导览 Agent 据此回答路线问题" />
            </div>
            <div class="form-row">
              <label>途经景点（勾选，需先维护「景点数据」）</label>
              <div v-if="spots.length === 0" class="form-hint">暂无景点数据，请先在「景点数据」页签新建景点。</div>
              <div v-else class="spot-check-grid">
                <label v-for="spot in spots" :key="spot.id" class="spot-check-item">
                  <input
                    type="checkbox"
                    :checked="routeForm.spotIds.includes(spot.id)"
                    @change="toggleRouteSpot(spot.id)"
                  />
                  <span>{{ spot.name }}</span>
                </label>
              </div>
            </div>
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>标签（用逗号分隔）</label>
                <input v-model="routeForm.tagsText" type="text" placeholder="如：亲子, 轻松" />
              </div>
              <div class="form-field">
                <label>适合人群（用逗号分隔）</label>
                <input v-model="routeForm.suitableForText" type="text" placeholder="如：带小孩, 老年人" />
              </div>
            </div>
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>总时长（分钟）</label>
                <input v-model.number="routeForm.totalDurationMinutes" type="number" min="1" />
              </div>
              <div class="form-field">
                <label>推荐理由</label>
                <input v-model="routeForm.recommendReason" type="text" placeholder="一句话说明推荐理由" />
              </div>
            </div>
            <p class="form-hint">保存后数字人导览 Agent 与首页活数据实时生效（同一份数据源）。</p>
            <p v-if="routeEditorError" class="form-error">{{ routeEditorError }}</p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showRouteEditor = false">取消</button>
            <button class="primary-btn" :disabled="routeSaving" @click="saveRoute">
              {{ routeSaving ? '保存中…' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Import Modal -->
    <Teleport to="body">
      <div v-if="showImport" class="modal-overlay" @click.self="showImport = false">
        <div class="modal-panel">
          <div class="modal-header">
            <h2>批量导入</h2>
            <button class="icon-btn" @click="showImport = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>默认分类（Markdown 导入使用）</label>
              <select v-model="importCategory">
                <option v-for="cat in CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
              </select>
            </div>
            <div class="form-row">
              <label>选择文件</label>
              <input type="file" accept=".json,.md,.markdown" @change="onFileChange" />
              <p class="form-hint">支持 JSON 数组或 Markdown（按一级标题切分）</p>
            </div>
            <p v-if="importError" class="form-error">{{ importError }}</p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showImport = false">取消</button>
            <button class="primary-btn" :disabled="isImporting" @click="doImport">
              {{ isImporting ? '导入中…' : '开始导入' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete Modal（知识文档） -->
    <Teleport to="body">
      <div v-if="showDelete" class="modal-overlay" @click.self="showDelete = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>确认删除</h2>
            <button class="icon-btn" @click="showDelete = false">✕</button>
          </div>
          <div class="modal-body">
            <p>
              确定要删除 <strong>{{ deletingItem?.title }}</strong> 吗？删除后无法恢复。
            </p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showDelete = false">取消</button>
            <button class="primary-btn danger" @click="doDelete">删除</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete Modal（景点） -->
    <Teleport to="body">
      <div v-if="showSpotDelete" class="modal-overlay" @click.self="showSpotDelete = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>确认删除</h2>
            <button class="icon-btn" @click="showSpotDelete = false">✕</button>
          </div>
          <div class="modal-body">
            <p>
              确定要删除景点 <strong>{{ deletingSpot?.name }}</strong> 吗？删除后数字人导览 Agent
              将无法回答该景点问题。被路线引用时无法删除，请先在路线编辑中解除引用。
            </p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showSpotDelete = false">取消</button>
            <button class="primary-btn danger" @click="doSpotDelete">删除</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- Delete Modal（路线） -->
    <Teleport to="body">
      <div v-if="showRouteDelete" class="modal-overlay" @click.self="showRouteDelete = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>确认删除</h2>
            <button class="icon-btn" @click="showRouteDelete = false">✕</button>
          </div>
          <div class="modal-body">
            <p>
              确定要删除路线 <strong>{{ deletingRoute?.name }}</strong> 吗？删除后数字人导览 Agent
              将无法回答该路线问题。
            </p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showRouteDelete = false">取消</button>
            <button class="primary-btn danger" @click="doRouteDelete">删除</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.knowledge {
  padding: var(--space-xl) var(--space-2xl);
  max-width: 1400px;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: var(--space-lg);
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

.header-actions {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

/* Tabs */
.scenic-tabs {
  display: flex;
  gap: var(--space-xs);
  margin-bottom: var(--space-xl);
  border-bottom: 1px solid var(--mist);
}

.scenic-tab {
  padding: var(--space-sm) var(--space-lg);
  border: none;
  background: transparent;
  color: var(--slate);
  font-size: var(--text-body);
  font-weight: 500;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.scenic-tab:hover {
  color: var(--ink);
}

.scenic-tab.active {
  color: var(--jade);
  border-bottom-color: var(--jade);
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
  background: var(--jade);
  color: var(--surface);
}

.primary-btn:hover {
  background: var(--jade-strong);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary-btn.danger {
  background: var(--cinnabar);
}

.primary-btn.danger:hover {
  background: var(--cinnabar-strong);
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

.filter-bar {
  display: flex;
  gap: var(--space-md);
  margin-bottom: var(--space-lg);
  flex-wrap: wrap;
}

.filter-select,
.filter-input {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-body);
  background: var(--surface);
  color: var(--ink);
}

.filter-select {
  min-width: 140px;
}

.filter-input {
  flex: 1;
  min-width: 240px;
}

.import-result {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  margin-bottom: var(--space-lg);
  background: var(--jade-soft);
  border: 1px solid var(--jade);
  border-radius: var(--radius-md);
  font-size: var(--text-small);
  color: var(--jade);
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

.table-card {
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-card);
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-body);
}

.data-table th,
.data-table td {
  padding: var(--space-md) var(--space-lg);
  text-align: left;
  border-bottom: 1px solid var(--mist);
}

.data-table th {
  font-weight: 600;
  color: var(--ink);
  background: var(--jade-soft);
}

.data-table tr:last-child td {
  border-bottom: none;
}

.col-title {
  width: 30%;
}

.col-category {
  width: 12%;
}

.col-tags {
  width: 20%;
}

.col-date {
  width: 14%;
}

.col-duration {
  width: 10%;
}

.col-difficulty {
  width: 10%;
}

.col-location {
  width: 16%;
}

.col-suitable {
  width: 16%;
}

.col-actions {
  width: 13%;
}

.cell-title {
  font-weight: 500;
  color: var(--ink);
}

.cell-sub {
  display: block;
  font-size: var(--text-caption);
  color: var(--slate);
  font-weight: 400;
  margin-top: 2px;
}

.cell-location {
  color: var(--slate);
  font-size: var(--text-small);
}

.cell-date {
  color: var(--slate);
  font-family: var(--font-mono);
  font-size: var(--text-small);
}

.empty-cell {
  text-align: center;
  color: var(--slate);
  padding: var(--space-3xl);
}

.category-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--text-caption);
  background: var(--jade-soft);
  color: var(--jade);
  font-weight: 500;
}

.tag-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  font-size: var(--text-caption);
  background: var(--overlay);
  color: var(--slate);
  margin-right: var(--space-xs);
  margin-bottom: var(--space-xs);
}

.tag-empty {
  color: var(--slate);
}

.action-group {
  display: flex;
  gap: var(--space-sm);
}

.text-btn {
  background: transparent;
  border: none;
  color: var(--slate);
  cursor: pointer;
  font-size: var(--text-small);
  padding: 0;
}

.text-btn:hover {
  color: var(--ink);
  text-decoration: underline;
}

.text-btn.danger {
  color: var(--cinnabar);
}

.text-btn.danger:hover {
  color: var(--cinnabar-strong);
}

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

/* Modal */
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

.modal-large {
  max-width: 860px;
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

.form-row input,
.form-row select,
.form-row textarea {
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-body);
  background: var(--surface);
  color: var(--ink);
  box-sizing: border-box;
}

.form-row textarea {
  min-height: 88px;
  resize: vertical;
}

.form-row input:focus,
.form-row select:focus,
.form-row textarea:focus {
  outline: none;
  border-color: var(--jade);
  box-shadow: 0 0 0 3px var(--jade-soft);
}

.form-row-inline {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-md);
}

.form-field {
  display: flex;
  flex-direction: column;
}

.form-error {
  color: var(--cinnabar);
  font-size: var(--text-small);
  margin: var(--space-sm) 0 0;
}

.form-hint {
  color: var(--slate);
  font-size: var(--text-caption);
  margin: var(--space-xs) 0 0;
}

/* 路线编辑：途经景点勾选网格 */
.spot-check-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-xs);
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  padding: var(--space-sm);
}

.spot-check-item {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  font-size: var(--text-small);
  color: var(--ink);
  cursor: pointer;
}

.editor-tabs {
  display: flex;
  gap: 2px;
  margin-bottom: var(--space-sm);
}

.tab-btn {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  background: var(--surface);
  color: var(--slate);
  cursor: pointer;
  font-size: var(--text-small);
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}

.tab-btn.active {
  background: var(--jade);
  color: var(--surface);
  border-color: var(--jade);
}

.editor-content {
  border: 1px solid var(--mist);
  border-radius: 0 var(--radius-sm) var(--radius-sm) var(--radius-sm);
  min-height: 280px;
}

.editor-content textarea {
  width: 100%;
  min-height: 280px;
  padding: var(--space-md);
  border: none;
  border-radius: 0 var(--radius-sm) var(--radius-sm) var(--radius-sm);
  font-size: var(--text-body);
  line-height: 1.6;
  resize: vertical;
  box-sizing: border-box;
  font-family: var(--font-mono);
}

.editor-content textarea:focus {
  outline: none;
}

.markdown-preview {
  min-height: 280px;
  padding: var(--space-md);
  line-height: 1.7;
  color: var(--ink);
}

.markdown-preview :deep(h1),
.markdown-preview :deep(h2),
.markdown-preview :deep(h3) {
  margin-top: var(--space-md);
  margin-bottom: var(--space-sm);
  color: var(--ink);
}

.markdown-preview :deep(p) {
  margin-bottom: var(--space-sm);
}

.markdown-preview :deep(ul),
.markdown-preview :deep(ol) {
  padding-left: var(--space-xl);
  margin-bottom: var(--space-sm);
}

.markdown-preview :deep(code) {
  background: var(--overlay);
  padding: 2px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: var(--text-small);
}

.markdown-preview :deep(pre) {
  background: var(--overlay);
  padding: var(--space-md);
  border-radius: var(--radius-sm);
  overflow-x: auto;
}

.markdown-preview :deep(pre code) {
  background: transparent;
  padding: 0;
}

.markdown-preview :deep(blockquote) {
  border-left: 3px solid var(--jade);
  margin: 0 0 var(--space-sm);
  padding-left: var(--space-md);
  color: var(--slate);
}

@media (max-width: 768px) {
  .knowledge {
    padding: var(--space-lg);
  }

  .page-header {
    flex-direction: column;
  }

  .form-row-inline {
    grid-template-columns: 1fr;
  }

  .spot-check-grid {
    grid-template-columns: 1fr;
  }

  .data-table th,
  .data-table td {
    padding: var(--space-sm);
  }

  .col-tags,
  .col-date,
  .col-location,
  .col-suitable {
    display: none;
  }
}
</style>
