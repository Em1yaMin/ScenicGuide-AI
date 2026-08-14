<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  createAdminUser,
  deleteAdminUser,
  fetchAdminAuditLogs,
  fetchAdminUsers,
  resetAdminUserPassword,
  setAdminUserDisabled,
} from '@/api/userAdmin'
import type { AdminAuditLogItem, AdminUserItem } from '@/types/userAdmin'
import { ADMIN_AUDIT_ACTION_LABELS } from '@/types/userAdmin'

const USER_PAGE_SIZE = 20
const AUDIT_PAGE_SIZE = 20

// ── 页签 ──
const activeTab = ref<'users' | 'audit'>('users')

// ── State：账号列表 ──
const users = ref<AdminUserItem[]>([])
const userTotal = ref(0)
const userPage = ref(1)
const isLoadingUsers = ref(false)
const userError = ref<string | null>(null)
const keyword = ref('')
const debouncedKeyword = ref('')

// ── State：选中账号 ──
const selectedUser = ref<AdminUserItem | null>(null)

// ── State：创建弹窗 ──
const showCreate = ref(false)
const createForm = ref({ username: '', password: '', nickname: '' })
const isCreating = ref(false)
const createError = ref<string | null>(null)

// ── State：禁用/启用确认 ──
const showToggleConfirm = ref(false)
const toggleDisabled = ref(false) // true=将要禁用
const isToggling = ref(false)

// ── State：重置密码弹窗 ──
const showReset = ref(false)
const resetForm = ref({ new_password: '' })
const isResetting = ref(false)
const resetResult = ref<{ generated: boolean; new_password: string } | null>(null)
const resetError = ref<string | null>(null)

// ── State：删除确认（含影响展示） ──
const showDelete = ref(false)
const deletingUser = ref<AdminUserItem | null>(null)
const isDeleting = ref(false)
const deleteResult = ref<{ deleted_dialogues: number; deleted_evaluations: number; deleted_memories: number } | null>(null)

// ── State：操作日志 ──
const audits = ref<AdminAuditLogItem[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditKeyword = ref('')
const auditAction = ref('')
const debouncedAuditKeyword = ref('')
const isLoadingAudits = ref(false)
const auditError = ref<string | null>(null)

let userDebounceTimer: ReturnType<typeof setTimeout> | null = null
let auditDebounceTimer: ReturnType<typeof setTimeout> | null = null
let usersRequestSeq = 0
let auditsRequestSeq = 0

// ── Computed ──
const userTotalPages = computed(() => Math.ceil(userTotal.value / USER_PAGE_SIZE))
const auditTotalPages = computed(() => Math.ceil(auditTotal.value / AUDIT_PAGE_SIZE))

// ── Watchers ──
watch(keyword, (value) => {
  if (userDebounceTimer) clearTimeout(userDebounceTimer)
  userDebounceTimer = setTimeout(() => {
    debouncedKeyword.value = value
    userPage.value = 1
    fetchUsers()
  }, 300)
})

watch(auditKeyword, (value) => {
  if (auditDebounceTimer) clearTimeout(auditDebounceTimer)
  auditDebounceTimer = setTimeout(() => {
    debouncedAuditKeyword.value = value
    auditPage.value = 1
    fetchAudits()
  }, 300)
})

watch(auditAction, () => {
  auditPage.value = 1
  fetchAudits()
})

watch(activeTab, (tab) => {
  if (tab === 'audit') {
    auditPage.value = 1
    fetchAudits()
  }
})

// ── 账号列表 ──
async function fetchUsers() {
  const seq = ++usersRequestSeq
  isLoadingUsers.value = true
  userError.value = null
  try {
    const data = await fetchAdminUsers({
      keyword: debouncedKeyword.value,
      page: userPage.value,
      page_size: USER_PAGE_SIZE,
    })
    if (seq !== usersRequestSeq) return
    users.value = data.items
    userTotal.value = data.total
    if (selectedUser.value) {
      const stillThere = data.items.find((u) => u.id === selectedUser.value!.id)
      if (stillThere) selectedUser.value = stillThere
    }
  } catch (e) {
    if (seq !== usersRequestSeq) return
    userError.value = e instanceof Error ? e.message : '账号列表加载失败'
  } finally {
    if (seq === usersRequestSeq) isLoadingUsers.value = false
  }
}

function selectUser(user: AdminUserItem) {
  selectedUser.value = user
}

function goUserPage(p: number) {
  if (p < 1 || p > userTotalPages.value) return
  userPage.value = p
  fetchUsers()
}

// ── 创建账号 ──
function openCreate() {
  createForm.value = { username: '', password: '', nickname: '' }
  createError.value = null
  showCreate.value = true
}

async function saveCreate() {
  const username = createForm.value.username.trim()
  const password = createForm.value.password
  if (!/^[a-zA-Z0-9_]{3,32}$/.test(username)) {
    createError.value = '用户名须为 3-32 位字母/数字/下划线'
    return
  }
  if (password.length < 6) {
    createError.value = '初始密码至少 6 位'
    return
  }
  isCreating.value = true
  createError.value = null
  try {
    const user = await createAdminUser({
      username,
      password,
      nickname: createForm.value.nickname.trim() || null,
    })
    showCreate.value = false
    userPage.value = 1
    debouncedKeyword.value = ''
    keyword.value = ''
    await fetchUsers()
    selectedUser.value = user
  } catch (e) {
    createError.value = e instanceof Error ? e.message : '创建失败'
  } finally {
    isCreating.value = false
  }
}

// ── 禁用 / 启用（二次确认） ──
function confirmToggle(user: AdminUserItem, disabled: boolean) {
  selectedUser.value = user
  toggleDisabled.value = disabled
  showToggleConfirm.value = true
}

async function doToggle() {
  if (isToggling.value || !selectedUser.value) return
  isToggling.value = true
  try {
    const updated = await setAdminUserDisabled(selectedUser.value.id, {
      disabled: toggleDisabled.value,
    })
    showToggleConfirm.value = false
    selectedUser.value = updated
    await fetchUsers()
  } catch (e) {
    userError.value = e instanceof Error ? e.message : '操作失败'
    showToggleConfirm.value = false
  } finally {
    isToggling.value = false
  }
}

// ── 重置密码 ──
function openReset(user: AdminUserItem) {
  selectedUser.value = user
  resetForm.value = { new_password: '' }
  resetResult.value = null
  resetError.value = null
  showReset.value = true
}

async function doReset() {
  if (isResetting.value || !selectedUser.value) return
  const newPassword = resetForm.value.new_password
  if (newPassword && newPassword.length < 6) {
    resetError.value = '新密码至少 6 位（留空则系统生成临时密码）'
    return
  }
  isResetting.value = true
  resetError.value = null
  try {
    const result = await resetAdminUserPassword(selectedUser.value.id, {
      new_password: newPassword || undefined,
    })
    resetResult.value = { generated: result.generated, new_password: result.new_password }
  } catch (e) {
    resetError.value = e instanceof Error ? e.message : '重置失败'
  } finally {
    isResetting.value = false
  }
}

// ── 删除账号（二次确认 + 影响展示） ──
function confirmDelete(user: AdminUserItem) {
  selectedUser.value = user
  deletingUser.value = user
  deleteResult.value = null
  showDelete.value = true
}

async function doDelete() {
  if (isDeleting.value || !deletingUser.value) return
  isDeleting.value = true
  try {
    const result = await deleteAdminUser(deletingUser.value.id)
    deleteResult.value = result
    selectedUser.value = null
    deletingUser.value = null
    await fetchUsers()
  } catch (e) {
    userError.value = e instanceof Error ? e.message : '删除失败'
    showDelete.value = false
    deletingUser.value = null
  } finally {
    isDeleting.value = false
  }
}

// ── 操作日志 ──
async function fetchAudits() {
  const seq = ++auditsRequestSeq
  isLoadingAudits.value = true
  auditError.value = null
  try {
    const data = await fetchAdminAuditLogs({
      keyword: debouncedAuditKeyword.value,
      action: auditAction.value || undefined,
      page: auditPage.value,
      page_size: AUDIT_PAGE_SIZE,
    })
    if (seq !== auditsRequestSeq) return
    audits.value = data.items
    auditTotal.value = data.total
  } catch (e) {
    if (seq !== auditsRequestSeq) return
    auditError.value = e instanceof Error ? e.message : '审计日志加载失败'
  } finally {
    if (seq === auditsRequestSeq) isLoadingAudits.value = false
  }
}

function goAuditPage(p: number) {
  if (p < 1 || p > auditTotalPages.value) return
  auditPage.value = p
  fetchAudits()
}

function formatTime(iso: string | null) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  fetchUsers()
})
</script>

<template>
  <div class="user-admin">
    <div class="page-header">
      <div class="header-text">
        <h1>账号管理</h1>
        <p class="header-desc">用户账号全生命周期管理（创建/禁用/启用/重置密码/删除）与操作审计</p>
      </div>
      <div class="header-actions">
        <button class="primary-btn" @click="openCreate">+ 创建账号</button>
      </div>
    </div>

    <!-- 页签：账号管理 / 操作日志 -->
    <div class="tab-bar">
      <button
        class="tab-item"
        :class="{ active: activeTab === 'users' }"
        @click="activeTab = 'users'"
      >
        账号管理
      </button>
      <button
        class="tab-item"
        :class="{ active: activeTab === 'audit' }"
        @click="activeTab = 'audit'"
      >
        操作日志
        <span class="tab-badge" v-if="auditTotal > 0">{{ auditTotal }}</span>
      </button>
    </div>

    <!-- ══════════ 页签一：账号管理 ══════════ -->
    <div v-if="activeTab === 'users'">
      <div v-if="userError" class="error-state">
        <p>{{ userError }}</p>
        <button class="secondary-btn" @click="fetchUsers">重试</button>
      </div>

      <div v-else class="user-layout">
        <!-- 左栏：账号列表 -->
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
              <span class="user-main">
                <span class="user-name">{{ user.nickname || user.username }}</span>
                <span
                  v-if="user.is_disabled"
                  class="status-badge disabled"
                  >已禁用</span
                >
              </span>
              <span class="user-username">{{ user.username }}</span>
              <span class="user-counts">
                <span class="count-chip">{{ user.dialogue_count }} 对话</span>
                <span class="count-chip">{{ user.memory_count }} 记忆</span>
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

        <!-- 右栏：选中账号详情 -->
        <div class="detail-pane table-card">
          <div class="pane-header">
            <h2>
              {{
                selectedUser
                  ? (selectedUser.nickname || selectedUser.username) + ' 的账号'
                  : '账号详情'
              }}
            </h2>
            <span v-if="selectedUser?.is_disabled" class="status-badge disabled">已禁用</span>
          </div>

          <template v-if="!selectedUser">
            <div class="empty-cell pane-empty">从左侧选择账号查看详情与操作</div>
          </template>

          <template v-else>
            <!-- 基本信息 -->
            <div class="info-grid">
              <div class="info-item">
                <span class="info-label">用户名</span>
                <span class="info-value">{{ selectedUser.username }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">昵称</span>
                <span class="info-value">{{ selectedUser.nickname || '—' }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">注册时间</span>
                <span class="info-value">{{ formatTime(selectedUser.created_at) }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">最后登录</span>
                <span class="info-value">{{ formatTime(selectedUser.last_login_at) }}</span>
              </div>
              <div class="info-item">
                <span class="info-label">禁用时间</span>
                <span class="info-value">{{ formatTime(selectedUser.disabled_at) }}</span>
              </div>
            </div>

            <!-- 数据概览 -->
            <div class="stat-row">
              <div class="stat-card">
                <span class="stat-num">{{ selectedUser.dialogue_count }}</span>
                <span class="stat-label">对话条数</span>
              </div>
              <div class="stat-card">
                <span class="stat-num">{{ selectedUser.memory_count }}</span>
                <span class="stat-label">长期记忆</span>
              </div>
            </div>

            <!-- 操作区 -->
            <div class="action-area">
              <button
                v-if="!selectedUser.is_disabled"
                class="secondary-btn danger-outline"
                @click="confirmToggle(selectedUser, true)"
              >
                禁用账号
              </button>
              <button
                v-else
                class="primary-btn"
                @click="confirmToggle(selectedUser, false)"
              >
                启用账号
              </button>
              <button class="secondary-btn" @click="openReset(selectedUser)">重置密码</button>
              <button class="danger-btn" @click="confirmDelete(selectedUser)">删除账号</button>
            </div>
            <p class="action-hint">
              禁用后该账号无法登录与对话；删除将<strong>级联清理</strong>其对话、量化评估与长期记忆（不可恢复）。
            </p>
          </template>
        </div>
      </div>
    </div>

    <!-- ══════════ 页签二：操作日志 ══════════ -->
    <div v-else class="audit-pane table-card">
      <div class="audit-toolbar">
        <input v-model="auditKeyword" class="filter-input" type="text" placeholder="搜索操作人 / 目标账号 / 结果…" />
        <select v-model="auditAction" class="filter-input filter-select">
          <option value="">全部动作</option>
          <option value="create">创建账号</option>
          <option value="disable">禁用账号</option>
          <option value="enable">启用账号</option>
          <option value="reset_password">重置密码</option>
          <option value="delete">删除账号</option>
        </select>
        <span class="pane-count">共 {{ auditTotal }} 条</span>
      </div>

      <div v-if="auditError" class="error-state">
        <p>{{ auditError }}</p>
        <button class="secondary-btn" @click="fetchAudits">重试</button>
      </div>

      <table v-else class="audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作人</th>
            <th>动作</th>
            <th>目标账号</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="isLoadingAudits && audits.length === 0">
            <td colspan="5" class="empty-cell">加载中…</td>
          </tr>
          <tr v-else-if="audits.length === 0">
            <td colspan="5" class="empty-cell">暂无操作记录</td>
          </tr>
          <tr v-for="log in audits" :key="log.id">
            <td class="mono-cell">{{ formatTime(log.created_at) }}</td>
            <td>{{ log.operator }}</td>
            <td>
              <span :class="['action-badge', log.action]">
                {{ ADMIN_AUDIT_ACTION_LABELS[log.action] ?? log.action }}
              </span>
            </td>
            <td>{{ log.target_username }}<span v-if="log.target_user_id" class="target-id">#{{ log.target_user_id }}</span></td>
            <td class="detail-cell" :title="log.detail ?? ''">{{ log.detail ?? '—' }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="auditTotalPages > 1" class="pagination">
        <button class="page-btn" :disabled="auditPage === 1" @click="goAuditPage(auditPage - 1)">
          上一页
        </button>
        <span class="page-info">第 {{ auditPage }} / {{ auditTotalPages }} 页</span>
        <button
          class="page-btn"
          :disabled="auditPage === auditTotalPages"
          @click="goAuditPage(auditPage + 1)"
        >
          下一页
        </button>
      </div>
    </div>

    <!-- 创建账号弹窗 -->
    <Teleport to="body">
      <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
        <div class="modal-panel">
          <div class="modal-header">
            <h2>创建账号</h2>
            <button class="icon-btn" @click="showCreate = false">✕</button>
          </div>
          <div class="modal-body">
            <div class="form-row">
              <label>用户名</label>
              <input
                v-model="createForm.username"
                type="text"
                maxlength="32"
                placeholder="3-32 位字母/数字/下划线"
              />
            </div>
            <div class="form-row">
              <label>初始密码</label>
              <input
                v-model="createForm.password"
                type="password"
                maxlength="64"
                placeholder="至少 6 位"
              />
            </div>
            <div class="form-row">
              <label>昵称（可选）</label>
              <input v-model="createForm.nickname" type="text" maxlength="32" placeholder="展示名称" />
            </div>
            <p v-if="createError" class="form-error">{{ createError }}</p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showCreate = false">取消</button>
            <button class="primary-btn" :disabled="isCreating" @click="saveCreate">
              {{ isCreating ? '创建中…' : '创建' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 禁用/启用确认弹窗 -->
    <Teleport to="body">
      <div v-if="showToggleConfirm" class="modal-overlay" @click.self="showToggleConfirm = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>{{ toggleDisabled ? '确认禁用' : '确认启用' }}</h2>
            <button class="icon-btn" @click="showToggleConfirm = false">✕</button>
          </div>
          <div class="modal-body">
            <p v-if="toggleDisabled">
              确定要禁用账号
              <strong>{{ selectedUser?.nickname || selectedUser?.username }}</strong>
              （{{ selectedUser?.username }}）吗？禁用后该账号<strong>无法登录与对话</strong>。
            </p>
            <p v-else>
              确定要启用账号
              <strong>{{ selectedUser?.nickname || selectedUser?.username }}</strong>
              （{{ selectedUser?.username }}）吗？启用后恢复正常使用。
            </p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" @click="showToggleConfirm = false">取消</button>
            <button
              :class="toggleDisabled ? 'primary-btn danger' : 'primary-btn'"
              :disabled="isToggling"
              @click="doToggle"
            >
              {{ isToggling ? '处理中…' : toggleDisabled ? '禁用' : '启用' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 重置密码弹窗 -->
    <Teleport to="body">
      <div v-if="showReset" class="modal-overlay" @click.self="showReset = false">
        <div class="modal-panel">
          <div class="modal-header">
            <h2>重置密码 — {{ selectedUser?.username }}</h2>
            <button class="icon-btn" @click="showReset = false">✕</button>
          </div>
          <div class="modal-body">
            <template v-if="!resetResult">
              <div class="form-row">
                <label>新密码（留空则系统生成临时密码）</label>
                <input
                  v-model="resetForm.new_password"
                  type="text"
                  maxlength="64"
                  placeholder="至少 6 位；留空自动生成"
                />
              </div>
              <p v-if="resetError" class="form-error">{{ resetError }}</p>
            </template>
            <template v-else>
              <div class="result-box">
                <p class="result-title">
                  {{ resetResult.generated ? '系统已生成临时密码（仅此一次展示，请立即保存）' : '密码已重置' }}
                </p>
                <p class="result-password mono">{{ resetResult.new_password }}</p>
              </div>
            </template>
          </div>
          <div class="modal-footer">
            <template v-if="!resetResult">
              <button class="secondary-btn" @click="showReset = false">取消</button>
              <button class="primary-btn" :disabled="isResetting" @click="doReset">
                {{ isResetting ? '重置中…' : '重置密码' }}
              </button>
            </template>
            <template v-else>
              <button class="primary-btn" @click="showReset = false">我已保存</button>
            </template>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 删除账号确认弹窗（含影响展示） -->
    <Teleport to="body">
      <div v-if="showDelete" class="modal-overlay" @click.self="showDelete = false">
        <div class="modal-panel">
          <div class="modal-header">
            <h2>确认删除账号</h2>
            <button class="icon-btn" @click="showDelete = false">✕</button>
          </div>
          <div class="modal-body">
            <template v-if="!deleteResult">
              <p>
                确定要删除账号
                <strong>{{ deletingUser?.nickname || deletingUser?.username }}</strong>
                （{{ deletingUser?.username }}）吗？
              </p>
              <div class="impact-box">
                <p class="impact-title">删除将级联清理以下数据（不可恢复）：</p>
                <ul>
                  <li>对话记录：<strong>{{ deletingUser?.dialogue_count ?? 0 }}</strong> 条</li>
                  <li>长期记忆：<strong>{{ deletingUser?.memory_count ?? 0 }}</strong> 条</li>
                  <li>量化评估：随对话一并清理</li>
                </ul>
              </div>
            </template>
            <template v-else>
              <div class="result-box">
                <p class="result-title">账号已删除，级联清理完成：</p>
                <ul class="result-list">
                  <li>对话：{{ deleteResult.deleted_dialogues }} 条</li>
                  <li>量化评估：{{ deleteResult.deleted_evaluations }} 条</li>
                  <li>长期记忆：{{ deleteResult.deleted_memories }} 条</li>
                </ul>
              </div>
            </template>
          </div>
          <div class="modal-footer">
            <template v-if="!deleteResult">
              <button class="secondary-btn" @click="showDelete = false">取消</button>
              <button class="primary-btn danger" :disabled="isDeleting" @click="doDelete">
                {{ isDeleting ? '删除中…' : '确认删除' }}
              </button>
            </template>
            <template v-else>
              <button class="primary-btn" @click="showDelete = false">关闭</button>
            </template>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.user-admin {
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

.primary-btn,
.secondary-btn,
.danger-btn {
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
}

.secondary-btn:hover {
  background: var(--overlay);
}

.danger-outline {
  color: var(--cinnabar);
  border-color: var(--cinnabar);
}

.danger-outline:hover {
  background: var(--cinnabar);
  color: var(--surface);
}

.danger-btn {
  background: var(--cinnabar);
  color: var(--surface);
}

.danger-btn:hover {
  background: var(--cinnabar-strong);
}

/* 页签 */
.tab-bar {
  display: flex;
  gap: var(--space-xs);
  margin-bottom: var(--space-lg);
  border-bottom: 1px solid var(--mist);
}

.tab-item {
  padding: var(--space-sm) var(--space-lg);
  border: none;
  background: transparent;
  color: var(--slate);
  font-size: var(--text-body);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.tab-item:hover {
  color: var(--ink);
}

.tab-item.active {
  color: var(--jade);
  border-bottom-color: var(--jade);
  font-weight: 600;
}

.tab-badge {
  display: inline-block;
  margin-left: var(--space-xs);
  padding: 1px 8px;
  border-radius: var(--radius-full);
  font-size: var(--text-caption);
  background: var(--jade-soft);
  color: var(--jade);
}

/* 双栏布局 */
.user-layout {
  display: grid;
  grid-template-columns: 380px 1fr;
  gap: var(--space-lg);
  align-items: start;
}

.table-card {
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--surface);
  box-shadow: var(--shadow-card);
}

.pane-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--mist);
}

.pane-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--ink);
}

.pane-count {
  font-size: var(--text-small);
  color: var(--slate);
}

/* 左栏：账号列表 */
.user-pane {
  display: flex;
  flex-direction: column;
  min-height: 460px;
}

.filter-input {
  margin: var(--space-md) var(--space-lg);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  font-size: var(--text-body);
  background: var(--surface);
  color: var(--ink);
}

.filter-select {
  margin: 0;
  min-width: 130px;
}

.user-list {
  flex: 1;
  overflow-y: auto;
  max-height: 520px;
  border-top: 1px solid var(--mist);
}

.user-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: var(--space-md) var(--space-lg);
  border: none;
  border-bottom: 1px solid var(--mist);
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition:
    background var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}

.user-item:hover {
  background: var(--overlay);
}

.user-item.active {
  background: var(--jade-soft);
}

.user-item.active .user-name {
  color: var(--jade);
}

.user-main {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
}

.user-name {
  font-weight: 500;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-username {
  font-size: var(--text-caption);
  color: var(--slate);
}

.user-counts {
  display: flex;
  gap: var(--space-sm);
}

.count-chip {
  padding: 1px 8px;
  border-radius: var(--radius-full);
  font-size: var(--text-caption);
  background: var(--overlay);
  color: var(--slate);
}

.status-badge {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--text-caption);
  font-weight: 500;
}

.status-badge.disabled {
  background: var(--cinnabar);
  color: var(--surface);
}

/* 右栏：详情 */
.detail-pane {
  min-height: 460px;
}

.pane-empty {
  padding: var(--space-3xl);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-md);
  padding: var(--space-lg);
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.info-label {
  font-size: var(--text-caption);
  color: var(--slate);
}

.info-value {
  font-size: var(--text-body);
  color: var(--ink);
}

.stat-row {
  display: flex;
  gap: var(--space-md);
  padding: 0 var(--space-lg) var(--space-lg);
}

.stat-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  background: var(--jade-soft);
}

.stat-num {
  font-family: var(--font-mono);
  font-size: var(--text-display);
  color: var(--jade);
  font-weight: 700;
}

.stat-label {
  font-size: var(--text-caption);
  color: var(--slate);
}

.action-area {
  display: flex;
  gap: var(--space-sm);
  flex-wrap: wrap;
  padding: 0 var(--space-lg) var(--space-md);
}

.action-hint {
  margin: 0;
  padding: 0 var(--space-lg) var(--space-lg);
  font-size: var(--text-caption);
  color: var(--slate);
}

/* 审计日志 */
.audit-pane {
  min-height: 420px;
}

.audit-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--mist);
  flex-wrap: wrap;
}

.audit-toolbar .filter-input {
  margin: 0;
  flex: 1;
  min-width: 200px;
}

.audit-table {
  width: 100%;
  border-collapse: collapse;
}

.audit-table th {
  text-align: left;
  padding: var(--space-sm) var(--space-lg);
  background: var(--jade-soft);
  color: var(--ink);
  font-size: var(--text-small);
  font-weight: 600;
}

.audit-table td {
  padding: var(--space-sm) var(--space-lg);
  border-bottom: 1px solid var(--mist);
  font-size: var(--text-small);
  color: var(--ink);
}

.audit-table tr:last-child td {
  border-bottom: none;
}

.mono-cell {
  font-family: var(--font-mono);
  color: var(--slate);
  white-space: nowrap;
}

.detail-cell {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--slate);
}

.target-id {
  margin-left: 4px;
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--slate);
}

.action-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: var(--text-caption);
  font-weight: 500;
}

.action-badge.create {
  background: var(--jade-soft);
  color: var(--jade);
}

.action-badge.enable {
  background: var(--jade-soft);
  color: var(--jade);
}

.action-badge.disable {
  background: var(--overlay);
  color: var(--slate);
}

.action-badge.reset_password {
  background: var(--overlay);
  color: var(--slate);
}

.action-badge.delete {
  background: var(--cinnabar);
  color: var(--surface);
}

/* 空态 / 错误态 */
.empty-cell {
  text-align: center;
  color: var(--slate);
  padding: var(--space-3xl);
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
}

.form-row input:focus {
  outline: none;
  border-color: var(--jade);
  box-shadow: 0 0 0 3px var(--jade-soft);
}

.form-error {
  color: var(--cinnabar);
  font-size: var(--text-small);
  margin: var(--space-sm) 0 0;
}

.result-box {
  padding: var(--space-md);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
  background: var(--jade-soft);
}

.result-title {
  margin: 0 0 var(--space-sm);
  font-size: var(--text-small);
  color: var(--ink);
}

.result-password {
  margin: 0;
  font-size: var(--text-title);
  font-weight: 700;
  color: var(--jade);
  word-break: break-all;
}

.result-list {
  margin: 0;
  padding-left: var(--space-lg);
  font-size: var(--text-small);
  color: var(--ink);
}

.impact-box {
  margin-top: var(--space-md);
  padding: var(--space-md);
  border: 1px solid var(--cinnabar);
  border-radius: var(--radius-sm);
  background: var(--overlay);
}

.impact-title {
  margin: 0 0 var(--space-xs);
  font-size: var(--text-small);
  font-weight: 600;
  color: var(--cinnabar);
}

.impact-box ul {
  margin: 0;
  padding-left: var(--space-lg);
  font-size: var(--text-small);
  color: var(--ink);
}

.mono {
  font-family: var(--font-mono);
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

@media (max-width: 960px) {
  .user-layout {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .user-admin {
    padding: var(--space-lg);
  }

  .page-header {
    flex-direction: column;
  }

  .info-grid {
    grid-template-columns: 1fr;
  }
}
</style>
