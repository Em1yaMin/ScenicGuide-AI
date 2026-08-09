<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { API_ADMIN_ANNOUNCEMENTS } from '@/api'
import type {
  AnnouncementFormData,
  AnnouncementItem,
  AnnouncementListResponse,
  AnnouncementStatus,
} from '@/types/announcement'

const PAGE_SIZE = 10
const TOKEN_KEY = 'admin_api_token'
const STATUS_LABELS: Record<AnnouncementStatus, string> = {
  published: '已发布',
  draft: '草稿',
}

// ── State ──
const items = ref<AnnouncementItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(PAGE_SIZE)
const isLoading = ref(false)
const error = ref<string | null>(null)

const selectedStatus = ref('')
const keyword = ref('')
const debouncedKeyword = ref('')

const showEditor = ref(false)
const editingItem = ref<AnnouncementItem | null>(null)
const form = ref<AnnouncementFormData>({
  title: '',
  content: '',
  status: 'draft',
  pinned: false,
})
const isSaving = ref(false)
const editorError = ref<string | null>(null)

const showDelete = ref(false)
const deletingItem = ref<AnnouncementItem | null>(null)

const showToken = ref(false)
const adminToken = ref(localStorage.getItem(TOKEN_KEY) ?? '')
const tokenInput = ref('')

const actionBusyId = ref<number | null>(null)

let debounceTimer: ReturnType<typeof setTimeout> | null = null

// ── Computed ──
const totalPages = computed(() => Math.ceil(total.value / pageSize.value))

// ── Watchers ──
watch(keyword, (value) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    debouncedKeyword.value = value
    page.value = 1
    fetchList()
  }, 300)
})

watch(selectedStatus, () => {
  page.value = 1
  fetchList()
})

// ── API helpers ──
function getHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY) ?? ''
  return {
    'Content-Type': 'application/json',
    'X-Admin-Token': token,
  }
}

async function fetchList() {
  isLoading.value = true
  error.value = null
  try {
    const params = new URLSearchParams()
    params.append('page', String(page.value))
    params.append('page_size', String(pageSize.value))
    if (selectedStatus.value) params.append('status', selectedStatus.value)
    if (debouncedKeyword.value) params.append('keyword', debouncedKeyword.value)

    const data = await fetchWithCodeEnvelope<AnnouncementListResponse>(
      `${API_ADMIN_ANNOUNCEMENTS}?${params.toString()}`,
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
  form.value = { title: '', content: '', status: 'draft', pinned: false }
  editorError.value = null
  showEditor.value = true
}

function openEdit(item: AnnouncementItem) {
  editingItem.value = item
  form.value = {
    title: item.title,
    content: item.content,
    status: item.status,
    pinned: item.pinned,
  }
  editorError.value = null
  showEditor.value = true
}

function validateForm(): boolean {
  if (!form.value.title.trim()) {
    editorError.value = '请输入标题'
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
    content: form.value.content.trim(),
    status: form.value.status,
    pinned: form.value.pinned,
  }

  try {
    if (editingItem.value) {
      await fetchWithCodeEnvelope<AnnouncementItem>(
        `${API_ADMIN_ANNOUNCEMENTS}/${editingItem.value.id}`,
        {
          method: 'PUT',
          headers: getHeaders(),
          body: JSON.stringify(payload),
        },
      )
    } else {
      await fetchWithCodeEnvelope<AnnouncementItem>(API_ADMIN_ANNOUNCEMENTS, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      })
    }
    showEditor.value = false
    await fetchList()
  } catch (e) {
    editorError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    isSaving.value = false
  }
}

/** 发布 / 下架切换（PATCH /{id}/status） */
async function toggleStatus(item: AnnouncementItem) {
  const next: AnnouncementStatus = item.status === 'published' ? 'draft' : 'published'
  actionBusyId.value = item.id
  error.value = null
  try {
    await fetchWithCodeEnvelope<AnnouncementItem>(`${API_ADMIN_ANNOUNCEMENTS}/${item.id}/status`, {
      method: 'PATCH',
      headers: getHeaders(),
      body: JSON.stringify({ status: next }),
    })
    await fetchList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '状态切换失败'
  } finally {
    actionBusyId.value = null
  }
}

/** 置顶切换（PUT /{id} 部分更新 pinned） */
async function togglePinned(item: AnnouncementItem) {
  actionBusyId.value = item.id
  error.value = null
  try {
    await fetchWithCodeEnvelope<AnnouncementItem>(`${API_ADMIN_ANNOUNCEMENTS}/${item.id}`, {
      method: 'PUT',
      headers: getHeaders(),
      body: JSON.stringify({ pinned: !item.pinned }),
    })
    await fetchList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '置顶切换失败'
  } finally {
    actionBusyId.value = null
  }
}

function confirmDelete(item: AnnouncementItem) {
  deletingItem.value = item
  showDelete.value = true
}

async function doDelete() {
  if (!deletingItem.value) return
  try {
    await fetchWithCodeEnvelope(`${API_ADMIN_ANNOUNCEMENTS}/${deletingItem.value.id}`, {
      method: 'DELETE',
      headers: { 'X-Admin-Token': localStorage.getItem(TOKEN_KEY) ?? '' },
    })
    showDelete.value = false
    await fetchList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
    showDelete.value = false
  }
}

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
}

function goPage(p: number) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  fetchList()
}

function formatDate(iso: string) {
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  fetchList()
})
</script>

<template>
  <div class="announcements">
    <div class="page-header">
      <div class="header-text">
        <h1>公告管理</h1>
        <p class="header-desc">发布景区通知、活动资讯，置顶公告优先展示在首页</p>
      </div>
      <div class="header-actions">
        <button class="secondary-btn" @click="openToken">Token 设置</button>
        <button class="primary-btn" @click="openCreate">+ 新建公告</button>
      </div>
    </div>

    <!-- Filters -->
    <div class="filter-bar">
      <select v-model="selectedStatus" class="filter-select">
        <option value="">全部状态</option>
        <option value="published">已发布</option>
        <option value="draft">草稿</option>
      </select>
      <input v-model="keyword" class="filter-input" type="text" placeholder="搜索标题或内容…" />
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
            <th class="col-status">状态</th>
            <th class="col-pinned">置顶</th>
            <th class="col-date">更新时间</th>
            <th class="col-actions">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="isLoading && items.length === 0">
            <td colspan="5" class="empty-cell">加载中…</td>
          </tr>
          <tr v-else-if="items.length === 0">
            <td colspan="5" class="empty-cell">暂无公告</td>
          </tr>
          <tr v-for="item in items" :key="item.id">
            <td class="cell-title">
              <span v-if="item.pinned" class="pin-badge">📌</span>
              {{ item.title }}
            </td>
            <td>
              <span :class="['status-badge', item.status]">
                {{ STATUS_LABELS[item.status] }}
              </span>
            </td>
            <td>
              <button
                class="pin-toggle"
                :class="{ active: item.pinned }"
                :disabled="actionBusyId === item.id"
                :title="item.pinned ? '取消置顶' : '置顶'"
                @click="togglePinned(item)"
              >
                {{ item.pinned ? '置顶中' : '未置顶' }}
              </button>
            </td>
            <td class="cell-date">{{ formatDate(item.updated_at) }}</td>
            <td>
              <div class="action-group">
                <button class="text-btn" @click="openEdit(item)">编辑</button>
                <button
                  class="text-btn"
                  :disabled="actionBusyId === item.id"
                  @click="toggleStatus(item)"
                >
                  {{ item.status === 'published' ? '下架' : '发布' }}
                </button>
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

    <!-- Editor Modal -->
    <Teleport to="body">
      <div v-if="showEditor" class="modal-overlay" @click.self="showEditor = false">
        <div class="modal-panel modal-large">
          <div class="modal-header">
            <h2>{{ editingItem ? '编辑公告' : '新建公告' }}</h2>
            <button class="icon-btn" @click="showEditor = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>标题</label>
              <input v-model="form.title" type="text" placeholder="请输入公告标题" />
            </div>
            <div class="form-row">
              <label>内容</label>
              <textarea v-model="form.content" rows="6" placeholder="请输入公告内容" />
            </div>
            <div class="form-row form-row-inline">
              <div class="form-field">
                <label>状态</label>
                <select v-model="form.status">
                  <option value="draft">草稿</option>
                  <option value="published">发布（访客首页可见）</option>
                </select>
              </div>
              <label class="check-field">
                <input v-model="form.pinned" type="checkbox" />
                <span>置顶（访客首页优先展示）</span>
              </label>
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

    <!-- Token Modal -->
    <Teleport to="body">
      <div v-if="showToken" class="modal-overlay" @click.self="showToken = false">
        <div class="modal-panel">
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

    <!-- Delete Modal -->
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
  </div>
</template>

<style scoped>
.announcements {
  padding: var(--space-xl) var(--space-2xl);
  max-width: 1400px;
}

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

.header-actions {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
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
  width: 38%;
}

.col-status {
  width: 12%;
}

.col-pinned {
  width: 12%;
}

.col-date {
  width: 18%;
}

.col-actions {
  width: 20%;
}

.cell-title {
  font-weight: 500;
  color: var(--ink);
}

.pin-badge {
  margin-right: var(--space-xs);
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

.status-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--text-caption);
  font-weight: 500;
}

.status-badge.published {
  background: var(--jade-soft);
  color: var(--jade);
}

.status-badge.draft {
  background: var(--overlay);
  color: var(--slate);
}

.pin-toggle {
  padding: 2px 10px;
  border: 1px solid var(--mist);
  border-radius: var(--radius-full);
  background: transparent;
  color: var(--slate);
  font-size: var(--text-caption);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.pin-toggle:hover:not(:disabled) {
  border-color: var(--jade);
  color: var(--jade);
}

.pin-toggle.active {
  background: var(--jade-soft);
  border-color: var(--jade);
  color: var(--jade);
  font-weight: 500;
}

.pin-toggle:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.action-group {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
}

.text-btn {
  background: transparent;
  border: none;
  color: var(--slate);
  cursor: pointer;
  font-size: var(--text-small);
  padding: 0;
}

.text-btn:hover:not(:disabled) {
  color: var(--ink);
  text-decoration: underline;
}

.text-btn.danger {
  color: var(--cinnabar);
}

.text-btn.danger:hover:not(:disabled) {
  color: var(--cinnabar-strong);
}

.text-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
  max-width: 720px;
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
  font-family: var(--font-body);
}

.form-row textarea {
  resize: vertical;
  line-height: 1.6;
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
  align-items: end;
}

.form-field {
  display: flex;
  flex-direction: column;
}

.check-field {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-small);
  color: var(--slate);
}

.check-field input {
  width: auto;
  accent-color: var(--jade);
}

.form-error {
  color: var(--jade);
  font-size: var(--text-small);
  margin: var(--space-sm) 0 0;
}

@media (max-width: 768px) {
  .announcements {
    padding: var(--space-lg);
  }

  .page-header {
    flex-direction: column;
  }

  .form-row-inline {
    grid-template-columns: 1fr;
  }

  .data-table th,
  .data-table td {
    padding: var(--space-sm);
  }

  .col-date {
    display: none;
  }
}
</style>
