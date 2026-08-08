<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  createMemory,
  deleteMemory,
  fetchMemoryUsers,
  fetchUserMemories,
  updateMemory,
} from '@/api/memory'
import type { AdminMemoryItem, AdminUserItem, MemoryFactType, MemoryFormData } from '@/types/memory'

const USER_PAGE_SIZE = 20
const TOKEN_KEY = 'admin_api_token'
/** 记忆内容长度上限（后端契约：1-200 字符） */
const CONTENT_MAX_LEN = 200

const FACT_TYPE_LABELS: Record<MemoryFactType, string> = {
  preference: '偏好',
  fact: '事实',
  habit: '习惯',
}

// ── State：用户列表 ──
const users = ref<AdminUserItem[]>([])
const userTotal = ref(0)
const userPage = ref(1)
const isLoadingUsers = ref(false)
const userError = ref<string | null>(null)
const keyword = ref('')
const debouncedKeyword = ref('')

// ── State：选中用户与其记忆 ──
const selectedUser = ref<AdminUserItem | null>(null)
const memories = ref<AdminMemoryItem[]>([])
const isLoadingMemories = ref(false)
const memoryError = ref<string | null>(null)

// ── State：编辑弹窗 ──
const showEditor = ref(false)
const editingMemory = ref<AdminMemoryItem | null>(null)
const form = ref<MemoryFormData>({ fact_type: 'fact', content: '' })
const isSaving = ref(false)
const editorError = ref<string | null>(null)

// ── State：删除确认 ──
const showDelete = ref(false)
const deletingMemory = ref<AdminMemoryItem | null>(null)
const isDeleting = ref(false)

// ── State：Token 设置 ──
const showToken = ref(false)
const adminToken = ref(localStorage.getItem(TOKEN_KEY) ?? '')
const tokenInput = ref('')

let debounceTimer: ReturnType<typeof setTimeout> | null = null
// 请求序号（latest-wins：防防抖/分页/刷新并发时旧响应乱序覆盖新结果）
let usersRequestSeq = 0
let memoriesRequestSeq = 0

// ── Computed ──
const userTotalPages = computed(() => Math.ceil(userTotal.value / USER_PAGE_SIZE))
const contentLength = computed(() => form.value.content.length)

// ── Watchers ──
watch(keyword, (value) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    debouncedKeyword.value = value
    userPage.value = 1
    fetchUsers()
  }, 300)
})

// ── 用户列表 ──
async function fetchUsers() {
  const seq = ++usersRequestSeq
  isLoadingUsers.value = true
  userError.value = null
  try {
    const data = await fetchMemoryUsers({
      keyword: debouncedKeyword.value,
      page: userPage.value,
      page_size: USER_PAGE_SIZE,
    })
    if (seq !== usersRequestSeq) return
    users.value = data.items
    userTotal.value = data.total
    // 列表刷新后校正选中用户（可能已不在当前页/被过滤）
    if (selectedUser.value) {
      const selected = selectedUser.value
      const stillThere = data.items.find((u) => u.id === selected.id)
      if (!stillThere) {
        // 保持选中但重拉记忆（含条数展示更新），选中用户仍有效
        loadMemories(selected)
      } else {
        selectedUser.value = stillThere
      }
    }
  } catch (e) {
    if (seq !== usersRequestSeq) return
    userError.value = e instanceof Error ? e.message : '用户列表加载失败'
  } finally {
    if (seq === usersRequestSeq) isLoadingUsers.value = false
  }
}

/** 选中用户并加载其记忆列表 */
function selectUser(user: AdminUserItem) {
  selectedUser.value = user
  loadMemories(user)
}

async function loadMemories(user: AdminUserItem) {
  const seq = ++memoriesRequestSeq
  isLoadingMemories.value = true
  memoryError.value = null
  try {
    const data = await fetchUserMemories(String(user.id))
    if (seq !== memoriesRequestSeq) return
    memories.value = data.items
  } catch (e) {
    if (seq !== memoriesRequestSeq) return
    memoryError.value = e instanceof Error ? e.message : '记忆列表加载失败'
    memories.value = []
  } finally {
    if (seq === memoriesRequestSeq) isLoadingMemories.value = false
  }
}

function goUserPage(p: number) {
  if (p < 1 || p > userTotalPages.value) return
  userPage.value = p
  fetchUsers()
}

// ── 新增 / 编辑记忆 ──
function openCreate() {
  if (!selectedUser.value) return
  editingMemory.value = null
  form.value = { fact_type: 'fact', content: '' }
  editorError.value = null
  showEditor.value = true
}

function openEdit(item: AdminMemoryItem) {
  editingMemory.value = item
  form.value = { fact_type: item.fact_type, content: item.content }
  editorError.value = null
  showEditor.value = true
}

function validateForm(): boolean {
  const content = form.value.content.trim()
  if (!content) {
    editorError.value = '请输入记忆内容'
    return false
  }
  if (content.length > CONTENT_MAX_LEN) {
    editorError.value = `内容不能超过 ${CONTENT_MAX_LEN} 字符`
    return false
  }
  editorError.value = null
  return true
}

async function saveForm() {
  if (!selectedUser.value || !validateForm()) return
  isSaving.value = true
  editorError.value = null

  try {
    if (editingMemory.value) {
      // 修改：删除后重建，memory_id 会变化，保存后整体刷新
      await updateMemory(editingMemory.value.memory_id, {
        fact_type: form.value.fact_type,
        content: form.value.content.trim(),
      })
    } else {
      await createMemory({
        user: String(selectedUser.value.id),
        fact_type: form.value.fact_type,
        content: form.value.content.trim(),
      })
    }
    showEditor.value = false
    await loadMemories(selectedUser.value)
    await fetchUsers() // 刷新条数统计
  } catch (e) {
    editorError.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    isSaving.value = false
  }
}

// ── 删除记忆（二次确认） ──
function confirmDelete(item: AdminMemoryItem) {
  deletingMemory.value = item
  showDelete.value = true
}

async function doDelete() {
  if (isDeleting.value || !deletingMemory.value || !selectedUser.value) return
  isDeleting.value = true
  try {
    await deleteMemory(deletingMemory.value.memory_id)
    showDelete.value = false
    await loadMemories(selectedUser.value)
    await fetchUsers() // 刷新条数统计
  } catch (e) {
    memoryError.value = e instanceof Error ? e.message : '删除失败'
    showDelete.value = false
  } finally {
    isDeleting.value = false
  }
}

// ── Token 设置 ──
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

// ── 工具 ──
/** Unix 秒 → 可读时间 */
function formatTimestamp(ts: number) {
  const d = new Date(ts * 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  fetchUsers()
})
</script>

<template>
  <div class="memories">
    <div class="page-header">
      <div class="header-text">
        <h1>记忆管理</h1>
        <p class="header-desc">查看和管理用户的长期记忆（用户画像），支持新增、修改与删除</p>
      </div>
      <div class="header-actions">
        <button class="secondary-btn" @click="openToken">Token 设置</button>
      </div>
    </div>

    <!-- 用户列表错误 -->
    <div v-if="userError" class="error-state">
      <p>{{ userError }}</p>
      <button class="secondary-btn" @click="fetchUsers">重试</button>
    </div>

    <div v-else class="memory-layout">
      <!-- 左栏：用户列表 -->
      <div class="user-pane table-card">
        <div class="pane-header">
          <h2>用户列表</h2>
          <span class="pane-count">共 {{ userTotal }} 人</span>
        </div>
        <input v-model="keyword" class="filter-input" type="text" placeholder="搜索用户名或昵称…" />
        <div class="user-list">
          <div v-if="isLoadingUsers && users.length === 0" class="empty-cell">加载中…</div>
          <div v-else-if="users.length === 0" class="empty-cell">暂无用户</div>
          <button
            v-for="user in users"
            :key="user.id"
            class="user-item"
            :class="{ active: selectedUser?.id === user.id }"
            @click="selectUser(user)"
          >
            <span class="user-name">{{ user.nickname || user.username }}</span>
            <span class="user-username">{{ user.username }}</span>
            <span class="memory-count" :class="{ zero: user.memory_count === 0 }">
              {{ user.memory_count }} 条
            </span>
          </button>
        </div>
        <div v-if="userTotalPages > 1" class="pagination">
          <button class="page-btn" :disabled="userPage === 1" @click="goUserPage(userPage - 1)">
            上一页
          </button>
          <span class="page-info">第 {{ userPage }} / {{ userTotalPages }} 页</span>
          <button
            class="page-btn"
            :disabled="userPage === userTotalPages"
            @click="goUserPage(userPage + 1)"
          >
            下一页
          </button>
        </div>
      </div>

      <!-- 右栏：选中用户的记忆列表 -->
      <div class="memory-pane table-card">
        <div class="pane-header">
          <h2>
            {{
              selectedUser
                ? (selectedUser.nickname || selectedUser.username) + ' 的记忆'
                : '记忆详情'
            }}
          </h2>
          <button
            v-if="selectedUser"
            class="primary-btn primary-btn-sm"
            :disabled="isLoadingMemories"
            @click="openCreate"
          >
            + 新增记忆
          </button>
        </div>

        <template v-if="!selectedUser">
          <div class="empty-cell pane-empty">从左侧选择用户查看其长期记忆</div>
        </template>

        <template v-else-if="memoryError">
          <div class="error-state">
            <p>{{ memoryError }}</p>
            <button class="secondary-btn" @click="loadMemories(selectedUser)">重试</button>
          </div>
        </template>

        <template v-else>
          <div v-if="isLoadingMemories && memories.length === 0" class="empty-cell">加载中…</div>
          <div v-else-if="memories.length === 0" class="empty-cell">
            <p class="empty-title">该用户暂无记忆</p>
            <p class="empty-hint">游客/匿名用户无长期记忆；可在右侧「新增记忆」手动添加</p>
          </div>
          <ul v-else class="memory-list">
            <li v-for="item in memories" :key="item.memory_id" class="memory-item">
              <div class="memory-main">
                <span :class="['fact-badge', item.fact_type]">
                  {{ FACT_TYPE_LABELS[item.fact_type] }}
                </span>
                <span class="memory-content">{{ item.content }}</span>
              </div>
              <div class="memory-meta">
                <span class="memory-time">{{ formatTimestamp(item.timestamp) }}</span>
                <span
                  v-if="item.source_conversation_id"
                  class="memory-source"
                  :title="item.source_conversation_id"
                >
                  来源会话
                </span>
                <div class="action-group">
                  <button class="text-btn" @click="openEdit(item)">编辑</button>
                  <button class="text-btn danger" @click="confirmDelete(item)">删除</button>
                </div>
              </div>
            </li>
          </ul>
        </template>
      </div>
    </div>

    <!-- 新增 / 编辑记忆弹窗 -->
    <Teleport to="body">
      <div v-if="showEditor" class="modal-overlay" @click.self="showEditor = false">
        <div class="modal-panel">
          <div class="modal-header">
            <h2>{{ editingMemory ? '编辑记忆' : '新增记忆' }}</h2>
            <button class="icon-btn" @click="showEditor = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>归属用户</label>
              <input
                :value="selectedUser ? selectedUser.nickname || selectedUser.username : ''"
                type="text"
                disabled
                class="input-disabled"
              />
            </div>
            <div class="form-row">
              <label>事实类型</label>
              <select v-model="form.fact_type">
                <option value="preference">偏好（preference）</option>
                <option value="fact">事实（fact）</option>
                <option value="habit">习惯（habit）</option>
              </select>
            </div>
            <div class="form-row">
              <label>记忆内容</label>
              <textarea
                v-model="form.content"
                rows="4"
                :maxlength="CONTENT_MAX_LEN"
                placeholder="请输入记忆句，如「喜欢拍照」"
              />
              <p class="form-hint">
                {{ contentLength }} / {{ CONTENT_MAX_LEN }} 字符，首尾空白自动去除
              </p>
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

    <!-- Token 设置弹窗 -->
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

    <!-- 删除确认弹窗 -->
    <Teleport to="body">
      <div v-if="showDelete" class="modal-overlay" @click.self="showDelete = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>确认删除</h2>
            <button class="icon-btn" @click="showDelete = false">✕</button>
          </div>
          <div class="modal-body">
            <p>
              确定要删除记忆 <strong>{{ deletingMemory?.content }}</strong> 吗？删除后无法恢复。
            </p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showDelete = false">取消</button>
            <button class="primary-btn danger" :disabled="isDeleting" @click="doDelete">
              {{ isDeleting ? '删除中…' : '删除' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.memories {
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

.primary-btn-sm {
  padding: var(--space-xs) var(--space-md);
}

.secondary-btn {
  background: transparent;
  color: var(--color-ink);
  border: 1px solid var(--color-border);
}

.secondary-btn:hover {
  background: var(--color-overlay);
}

/* 双栏布局 */
.memory-layout {
  display: grid;
  grid-template-columns: 340px 1fr;
  gap: var(--space-lg);
  align-items: start;
}

.table-card {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  background: var(--color-paper);
}

.pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--color-border);
}

.pane-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--color-ink);
}

.pane-count {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

/* 左栏：用户列表 */
.user-pane {
  display: flex;
  flex-direction: column;
  min-height: 420px;
}

.filter-input {
  margin: var(--space-md) var(--space-lg);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: 6px;
  font-size: var(--text-body);
  background: var(--color-paper);
  color: var(--color-ink);
}

.user-list {
  flex: 1;
  overflow-y: auto;
  max-height: 480px;
  border-top: 1px solid var(--color-border);
}

.user-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  width: 100%;
  padding: var(--space-md) var(--space-lg);
  border: none;
  border-bottom: 1px solid var(--color-border);
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition:
    background var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}

.user-item:hover {
  background: var(--color-overlay);
}

.user-item.active {
  background: var(--color-accent-light);
}

.user-item.active .user-name {
  color: var(--color-accent);
}

.user-name {
  font-weight: 500;
  color: var(--color-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-username {
  flex: 1;
  min-width: 0;
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.memory-count {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: var(--text-caption);
  font-weight: 500;
  background: var(--color-accent-light);
  color: var(--color-accent);
}

.memory-count.zero {
  background: var(--color-overlay);
  color: var(--color-ink-secondary);
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

/* 右栏：记忆列表 */
.memory-pane {
  min-height: 420px;
}

.pane-empty {
  padding: var(--space-3xl);
}

.memory-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.memory-item {
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--color-border);
}

.memory-item:last-child {
  border-bottom: none;
}

.memory-main {
  display: flex;
  align-items: flex-start;
  gap: var(--space-sm);
  margin-bottom: var(--space-xs);
}

.memory-content {
  color: var(--color-ink);
  font-size: var(--text-body);
  line-height: 1.6;
  word-break: break-all;
}

.fact-badge {
  flex-shrink: 0;
  display: inline-block;
  margin-top: 2px;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: var(--text-caption);
  font-weight: 500;
}

.fact-badge.preference {
  background: var(--color-accent-light);
  color: var(--color-accent);
}

.fact-badge.fact {
  background: var(--color-overlay);
  color: var(--color-ink-secondary);
}

.fact-badge.habit {
  background: #e3f0e9;
  color: #2f7d5b;
}

.memory-meta {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding-left: 0;
}

.memory-time {
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
}

.memory-source {
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
}

.action-group {
  margin-left: auto;
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

.text-btn:hover:not(:disabled) {
  color: var(--color-ink);
  text-decoration: underline;
}

.text-btn.danger {
  color: var(--color-accent);
}

.text-btn.danger:hover:not(:disabled) {
  color: #a34b1e;
}

.text-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 空态 / 错误态 */
.empty-cell {
  text-align: center;
  color: var(--color-ink-secondary);
  padding: var(--space-3xl);
}

.empty-title {
  margin: 0 0 var(--space-xs);
  font-weight: 500;
  color: var(--color-ink);
}

.empty-hint {
  margin: 0;
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
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
  border-color: var(--color-ink-secondary);
}

.input-disabled {
  opacity: 0.6;
}

.form-hint {
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
  margin: var(--space-xs) 0 0;
}

.form-error {
  color: var(--color-accent);
  font-size: var(--text-small);
  margin: var(--space-sm) 0 0;
}

@media (max-width: 960px) {
  .memory-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .memories {
    padding: var(--space-lg);
  }

  .page-header {
    flex-direction: column;
  }
}
</style>
