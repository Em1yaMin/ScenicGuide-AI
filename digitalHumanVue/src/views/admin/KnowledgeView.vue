<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { API_ADMIN_KNOWLEDGE, API_ADMIN_KNOWLEDGE_IMPORT } from '@/api'
import type { KnowledgeItem, KnowledgeListResponse, KnowledgeFormData } from '@/types/knowledge'

const CATEGORIES = ['讲解词', 'FAQ', '景点资料']
const PAGE_SIZE = 10
const TOKEN_KEY = 'admin_api_token'

// ── State ──
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
const activeTab = ref<'write' | 'preview'>('write')

const showImport = ref(false)
const importFile = ref<File | null>(null)
const importCategory = ref('景点资料')
const isImporting = ref(false)
const importError = ref<string | null>(null)

const showToken = ref(false)
const adminToken = ref(localStorage.getItem(TOKEN_KEY) ?? '')
const tokenInput = ref('')

const showDelete = ref(false)
const deletingItem = ref<KnowledgeItem | null>(null)

let debounceTimer: ReturnType<typeof setTimeout> | null = null

// ── Computed ──
const totalPages = computed(() => Math.ceil(total.value / pageSize.value))

const renderedPreview = computed(() => {
  const raw = marked.parse(form.value.content || '', { async: false }) as string
  return DOMPurify.sanitize(raw)
})

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
  activeTab.value = 'write'
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
  activeTab.value = 'write'
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
    tags: form.value.tags
      ? form.value.tags.split(',').map((t) => t.trim()).filter(Boolean)
      : [],
  }

  try {
    if (editingItem.value) {
      await fetchWithCodeEnvelope<KnowledgeItem>(
        `${API_ADMIN_KNOWLEDGE}/${editingItem.value.id}`,
        {
          method: 'PUT',
          headers: getHeaders(),
          body: JSON.stringify(payload),
        },
      )
    } else {
      await fetchWithCodeEnvelope<KnowledgeItem>(API_ADMIN_KNOWLEDGE, {
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

function confirmDelete(item: KnowledgeItem) {
  deletingItem.value = item
  showDelete.value = true
}

async function doDelete() {
  if (!deletingItem.value) return
  try {
    await fetchWithCodeEnvelope(`${API_ADMIN_KNOWLEDGE}/${deletingItem.value.id}`, {
      method: 'DELETE',
      headers: { 'X-Admin-Token': localStorage.getItem(TOKEN_KEY) ?? '' },
    })
    showDelete.value = false
    await fetchList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '删除失败'
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
    await fetchWithCodeEnvelope(`${API_ADMIN_KNOWLEDGE_IMPORT}?default_category=${encodeURIComponent(importCategory.value)}`, {
      method: 'POST',
      headers: { 'X-Admin-Token': localStorage.getItem(TOKEN_KEY) ?? '' },
      body,
    })
    showImport.value = false
    importFile.value = null
    await fetchList()
  } catch (e) {
    importError.value = e instanceof Error ? e.message : '导入失败'
  } finally {
    isImporting.value = false
  }
}

function saveToken() {
  localStorage.setItem(TOKEN_KEY, tokenInput.value.trim())
  adminToken.value = tokenInput.value.trim()
  showToken.value = false
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

onMounted(() => {
  fetchList()
})
</script>

<template>
  <div class="knowledge">
    <div class="page-header">
      <div class="header-text">
        <h1>知识库管理</h1>
        <p class="header-desc">管理景区讲解词、FAQ 与景点资料</p>
      </div>
      <div class="header-actions">
        <button class="secondary-btn" @click="showToken = true">
          Token 设置
        </button>
        <button class="secondary-btn" @click="showImport = true">
          批量导入
        </button>
        <button class="primary-btn" @click="openCreate">
          + 新建条目
        </button>
      </div>
    </div>

    <!-- Filters -->
    <div class="filter-bar">
      <select v-model="selectedCategory" class="filter-select">
        <option value="">全部分类</option>
        <option v-for="cat in CATEGORIES" :key="cat" :value="cat">{{ cat }}</option>
      </select>
      <input
        v-model="keyword"
        class="filter-input"
        type="text"
        placeholder="搜索标题或内容…"
      />
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
        <button
          class="page-btn"
          :disabled="page === 1"
          @click="goPage(page - 1)"
        >
          上一页
        </button>
        <span class="page-info">第 {{ page }} / {{ totalPages }} 页，共 {{ total }} 条</span>
        <button
          class="page-btn"
          :disabled="page === totalPages"
          @click="goPage(page + 1)"
        >
          下一页
        </button>
      </div>
    </div>

    <!-- Editor Modal -->
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
                :class="['tab-btn', { active: activeTab === 'write' }]"
                @click="activeTab = 'write'"
              >
                编辑
              </button>
              <button
                :class="['tab-btn', { active: activeTab === 'preview' }]"
                @click="activeTab = 'preview'"
              >
                预览
              </button>
            </div>
            <div class="editor-content">
              <textarea
                v-if="activeTab === 'write'"
                v-model="form.content"
                placeholder="支持 Markdown 语法"
              />
              <div
                v-else
                class="markdown-preview"
                v-html="renderedPreview"
              />
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
              <input v-model="tokenInput" type="password" :placeholder="adminToken ? '已设置' : '请输入 Token'" />
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
            <p>确定要删除 <strong>{{ deletingItem?.title }}</strong> 吗？删除后无法恢复。</p>
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
.knowledge {
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
  color: var(--color-ink);
}

.header-desc {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
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
  border-radius: 6px;
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  transition:
    background var(--duration-fast) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}

.primary-btn {
  background: var(--color-ink);
  color: var(--color-paper);
}

.primary-btn:hover {
  background: var(--color-accent);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary-btn.danger {
  background: var(--color-accent);
}

.primary-btn.danger:hover {
  background: #a34b1e;
}

.secondary-btn {
  background: transparent;
  color: var(--color-ink);
  border: 1px solid var(--color-border);
}

.secondary-btn:hover {
  background: var(--color-overlay);
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
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: var(--text-body);
  background: var(--color-paper);
  color: var(--color-ink);
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
  color: var(--color-ink-secondary);
  gap: var(--space-md);
}

.table-card {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--color-paper);
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
  border-bottom: 1px solid var(--color-border);
}

.data-table th {
  font-weight: 600;
  color: var(--color-ink-secondary);
  background: var(--color-overlay);
}

.data-table tr:last-child td {
  border-bottom: none;
}

.col-title {
  width: 35%;
}

.col-category {
  width: 12%;
}

.col-tags {
  width: 25%;
}

.col-date {
  width: 15%;
}

.col-actions {
  width: 13%;
}

.cell-title {
  font-weight: 500;
  color: var(--color-ink);
}

.cell-date {
  color: var(--color-ink-secondary);
  font-family: var(--font-mono);
  font-size: var(--text-small);
}

.empty-cell {
  text-align: center;
  color: var(--color-ink-secondary);
  padding: var(--space-3xl);
}

.category-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: var(--text-caption);
  background: var(--color-accent-light);
  color: var(--color-accent);
  font-weight: 500;
}

.tag-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: var(--text-caption);
  background: var(--color-overlay);
  color: var(--color-ink-secondary);
  margin-right: var(--space-xs);
  margin-bottom: var(--space-xs);
}

.tag-empty {
  color: var(--color-ink-secondary);
}

.action-group {
  display: flex;
  gap: var(--space-sm);
}

.text-btn {
  background: transparent;
  border: none;
  color: var(--color-ink-secondary);
  cursor: pointer;
  font-size: var(--text-small);
  padding: 0;
}

.text-btn:hover {
  color: var(--color-ink);
  text-decoration: underline;
}

.text-btn.danger {
  color: var(--color-accent);
}

.text-btn.danger:hover {
  color: #a34b1e;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--color-border);
}

.page-btn {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  background: var(--color-paper);
  color: var(--color-ink);
  border-radius: 6px;
  cursor: pointer;
  font-size: var(--text-small);
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-btn:hover:not(:disabled) {
  background: var(--color-overlay);
}

.page-info {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-lg);
  z-index: 100;
}

.modal-panel {
  background: var(--color-paper);
  border-radius: 10px;
  width: 100%;
  max-width: 560px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15);
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
  border-bottom: 1px solid var(--color-border);
}

.modal-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--color-ink);
}

.icon-btn {
  background: transparent;
  border: none;
  font-size: var(--text-title);
  color: var(--color-ink-secondary);
  cursor: pointer;
  line-height: 1;
}

.icon-btn:hover {
  color: var(--color-ink);
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
  border-top: 1px solid var(--color-border);
}

.form-row {
  margin-bottom: var(--space-md);
}

.form-row label {
  display: block;
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin-bottom: var(--space-xs);
}

.form-row input,
.form-row select,
.form-row textarea {
  width: 100%;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: var(--text-body);
  background: var(--color-paper);
  color: var(--color-ink);
  box-sizing: border-box;
}

.form-row input:focus,
.form-row select:focus,
.form-row textarea:focus {
  outline: none;
  border-color: var(--color-ink-secondary);
}

.form-row-inline {
  display: grid;
  grid-template-columns: 1fr 2fr;
  gap: var(--space-md);
}

.form-field {
  display: flex;
  flex-direction: column;
}

.form-error {
  color: var(--color-accent);
  font-size: var(--text-small);
  margin: var(--space-sm) 0 0;
}

.form-hint {
  color: var(--color-ink-secondary);
  font-size: var(--text-caption);
  margin: var(--space-xs) 0 0;
}

.editor-tabs {
  display: flex;
  gap: 2px;
  margin-bottom: var(--space-sm);
}

.tab-btn {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  background: var(--color-paper);
  color: var(--color-ink-secondary);
  cursor: pointer;
  font-size: var(--text-small);
  border-radius: 6px 6px 0 0;
}

.tab-btn.active {
  background: var(--color-ink);
  color: var(--color-paper);
  border-color: var(--color-ink);
}

.editor-content {
  border: 1px solid var(--color-border);
  border-radius: 0 6px 6px 6px;
  min-height: 280px;
}

.editor-content textarea {
  width: 100%;
  min-height: 280px;
  padding: var(--space-md);
  border: none;
  border-radius: 0 6px 6px 6px;
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
  color: var(--color-ink);
}

.markdown-preview :deep(h1),
.markdown-preview :deep(h2),
.markdown-preview :deep(h3) {
  margin-top: var(--space-md);
  margin-bottom: var(--space-sm);
  color: var(--color-ink);
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
  background: var(--color-overlay);
  padding: 2px 4px;
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: var(--text-small);
}

.markdown-preview :deep(pre) {
  background: var(--color-overlay);
  padding: var(--space-md);
  border-radius: 6px;
  overflow-x: auto;
}

.markdown-preview :deep(pre code) {
  background: transparent;
  padding: 0;
}

.markdown-preview :deep(blockquote) {
  border-left: 3px solid var(--color-accent);
  margin: 0 0 var(--space-sm);
  padding-left: var(--space-md);
  color: var(--color-ink-secondary);
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

  .data-table th,
  .data-table td {
    padding: var(--space-sm);
  }

  .col-tags,
  .col-date {
    display: none;
  }
}
</style>
