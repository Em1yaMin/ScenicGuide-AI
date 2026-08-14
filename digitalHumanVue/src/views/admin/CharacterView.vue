<script setup lang="ts">
/**
 * 数字人形象管理页（2026-08-14 后端 PR #99 前端部分）。
 *
 * 展示 ADH 内置 13 个 Live2D 形象卡片网格：预览图 + 名称 + 当前选中高亮；
 * 点击卡片 → 确认弹窗 → PUT /api/admin/dh-character 保存 → 成功提示。
 * 形象列表以接口 available 字段为准（与后端常量/ADH public/characters
 * 目录一致），预览图 URL 按名称拼接 VITE_LIGHT4AI_URL + /characters/{name}/{name}.png。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { adminApiHeaders } from '@/utils/adminHeaders'
import { API_ADMIN_DH_CHARACTER } from '@/api'
import type { DhCharacterResponse } from '@/types/dhCharacter'

// ── State ──
const character = ref('') // 当前生效形象
const available = ref<string[]>([]) // 可选形象列表（接口返回）
const isLoading = ref(true)
const error = ref<string | null>(null)

const saving = ref(false)
const saveError = ref<string | null>(null)
const saveSuccess = ref(false)

const showConfirm = ref(false)
const pendingName = ref('')

/** 预览图加载失败的名称集合（失败显示占位：名称首字） */
const imageFailed = reactive<Record<string, boolean>>({})

const adhBase = import.meta.env.VITE_LIGHT4AI_URL

// ── Computed ──
const currentCharacter = computed(() => character.value || '—')

function previewUrl(name: string): string {
  return `${adhBase}/characters/${name}/${name}.png`
}

// ── API ──
async function fetchList() {
  isLoading.value = true
  error.value = null
  try {
    const data = await fetchWithCodeEnvelope<DhCharacterResponse>(
      API_ADMIN_DH_CHARACTER,
      { headers: adminApiHeaders(false) },
    )
    character.value = data.character
    available.value = data.available ?? []
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    isLoading.value = false
  }
}

function onImageError(name: string) {
  imageFailed[name] = true
}

/** 点击形象卡片：已是当前形象直接忽略；否则弹确认框 */
function chooseCharacter(name: string) {
  if (name === character.value || saving.value) return
  saveError.value = null
  saveSuccess.value = false
  pendingName.value = name
  showConfirm.value = true
}

async function doSave() {
  saving.value = true
  saveError.value = null
  saveSuccess.value = false
  try {
    const data = await fetchWithCodeEnvelope<DhCharacterResponse>(
      API_ADMIN_DH_CHARACTER,
      {
        method: 'PUT',
        headers: adminApiHeaders(),
        body: JSON.stringify({ character: pendingName.value }),
      },
    )
    character.value = data.character
    showConfirm.value = false
    saveSuccess.value = true
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : '保存失败'
    showConfirm.value = false
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  fetchList()
})
</script>

<template>
  <div class="characters">
    <div class="page-header">
      <div class="header-text">
        <h1>形象管理</h1>
        <p class="header-desc">选择数字人展示形象，保存后访客对话页即时生效</p>
      </div>
      <div class="header-actions">
        <span class="current-badge">
          当前形象：<strong>{{ currentCharacter }}</strong>
        </span>
      </div>
    </div>

    <!-- 保存成功提示（页内轻提示，3s 后自然淡出由用户自行消化，不额外倒计时） -->
    <Transition name="tip">
      <div v-if="saveSuccess" class="success-tip">✓ 形象已切换为「{{ character }}」，访客对话页即时生效</div>
    </Transition>
    <p v-if="saveError" class="save-error">保存失败：{{ saveError }}</p>

    <!-- 加载中 -->
    <div v-if="isLoading" class="empty-state">
      <div class="loading-spinner" />
      <p>形象列表加载中…</p>
    </div>

    <!-- 加载失败 -->
    <div v-else-if="error" class="error-state">
      <p>{{ error }}</p>
      <button class="secondary-btn" @click="fetchList">重试</button>
    </div>

    <!-- 形象卡片网格 -->
    <div v-else class="card-grid">
      <button
        v-for="name in available"
        :key="name"
        type="button"
        class="character-card"
        :class="{ active: name === character }"
        :disabled="saving"
        @click="chooseCharacter(name)"
      >
        <div class="card-preview">
          <img
            v-if="adhBase && !imageFailed[name]"
            :src="previewUrl(name)"
            :alt="name"
            loading="lazy"
            @error="onImageError(name)"
          />
          <!-- 预览图加载失败/无 ADH 地址时的占位（名称首字，勿破版） -->
          <div v-else class="preview-placeholder">{{ name.charAt(0) }}</div>
          <span v-if="name === character" class="active-seal">当前</span>
        </div>
        <div class="card-footer">
          <span class="card-name">{{ name }}</span>
        </div>
      </button>
      <div v-if="available.length === 0" class="empty-state">
        <p>暂无可用形象</p>
      </div>
    </div>

    <!-- 确认切换弹窗 -->
    <Teleport to="body">
      <div v-if="showConfirm" class="modal-overlay" @click.self="showConfirm = false">
        <div class="modal-panel modal-small">
          <div class="modal-header">
            <h2>确认切换形象</h2>
            <button class="icon-btn" @click="showConfirm = false">✕</button>
          </div>
          <div class="modal-body">
            <p>
              确定将数字人形象切换为 <strong>{{ pendingName }}</strong> 吗？
              保存后访客对话页与数字人应用将使用新形象。
            </p>
          </div>
          <div class="modal-footer">
            <button class="secondary-btn" :disabled="saving" @click="showConfirm = false">
              取消
            </button>
            <button class="primary-btn" :disabled="saving" @click="doSave">
              {{ saving ? '保存中…' : '确认切换' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.characters {
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
  align-items: center;
}

/* 当前形象徽标（青玉淡底圆角胶囊，与既有 status-badge 同源） */
.current-badge {
  padding: var(--space-sm) var(--space-lg);
  border: 1px solid var(--mist);
  border-radius: var(--radius-full);
  background: var(--surface);
  font-size: var(--text-small);
  color: var(--slate);
  white-space: nowrap;
}

.current-badge strong {
  color: var(--jade);
  font-weight: 600;
}

.success-tip {
  margin-bottom: var(--space-md);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--jade);
  border-radius: var(--radius-sm);
  background: var(--jade-soft);
  color: var(--jade);
  font-size: var(--text-small);
}

.save-error {
  margin: 0 0 var(--space-md);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--cinnabar);
  border-radius: var(--radius-sm);
  background: var(--cinnabar-soft);
  color: var(--cinnabar);
  font-size: var(--text-small);
}

.tip-enter-active,
.tip-leave-active {
  transition: opacity var(--duration-normal) var(--ease-out);
}

.tip-enter-from,
.tip-leave-to {
  opacity: 0;
}

/* ── 形象卡片网格（素白卡片 + 青玉高亮 + 水墨阴影） ── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: var(--space-md);
}

.character-card {
  display: flex;
  flex-direction: column;
  padding: 0;
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  background: var(--surface);
  box-shadow: var(--shadow-card);
  cursor: pointer;
  overflow: hidden;
  text-align: left;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out);
}

.character-card:hover:not(:disabled) {
  border-color: var(--mist-strong);
  box-shadow: var(--shadow-pop);
  transform: translateY(-2px);
}

.character-card:disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

/* 当前选中：青玉描边 + 淡青玉光环（与表单聚焦同源） */
.character-card.active {
  border-color: var(--jade);
  box-shadow:
    0 0 0 3px var(--jade-soft),
    var(--shadow-card);
}

.card-preview {
  position: relative;
  aspect-ratio: 1;
  background: var(--paper);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.card-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

/* 预览图失败占位：宣纸底 + 青玉大号首字 */
.preview-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  font-family: var(--font-display);
  font-size: var(--text-display);
  font-weight: 700;
  color: var(--jade);
  background: var(--jade-soft);
}

/* 当前选中角标（朱砂印章式，与品牌 seal 同源） */
.active-seal {
  position: absolute;
  top: var(--space-sm);
  right: var(--space-sm);
  padding: 2px var(--space-sm);
  border-radius: var(--radius-sm);
  background: var(--cinnabar);
  color: var(--surface);
  font-size: var(--text-tiny);
  font-weight: 600;
  line-height: 1.4;
}

.card-footer {
  padding: var(--space-sm) var(--space-md);
  border-top: 1px solid var(--mist);
}

.card-name {
  font-size: var(--text-small);
  font-weight: 500;
  color: var(--ink);
}

/* ── 状态区（加载/错误/空态，与公告页同风格） ── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-3xl);
  color: var(--slate);
  gap: var(--space-md);
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

.empty-state p,
.error-state p {
  margin: 0;
}

.loading-spinner {
  width: 28px;
  height: 28px;
  border: 2px solid var(--mist);
  border-top-color: var(--jade);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* ── 按钮（与公告页同源） ── */
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

.primary-btn:hover:not(:disabled) {
  background: var(--jade-strong);
}

.primary-btn:disabled,
.secondary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.secondary-btn {
  background: transparent;
  color: var(--ink);
  border: 1px solid var(--mist);
  border-radius: var(--radius-sm);
}

.secondary-btn:hover:not(:disabled) {
  background: var(--overlay);
}

/* ── 确认弹窗（与公告页 modal 同风格） ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(30, 42, 38, 0.4);
}

.modal-panel {
  background: var(--surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-pop);
  width: 100%;
  max-width: 520px;
}

.modal-small {
  max-width: 420px;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-lg);
  border-bottom: 1px solid var(--mist);
}

.modal-header h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  color: var(--ink);
}

.icon-btn {
  border: none;
  background: transparent;
  color: var(--slate);
  font-size: var(--text-body);
  cursor: pointer;
  padding: var(--space-xs);
}

.icon-btn:hover {
  color: var(--ink);
}

.modal-body {
  padding: var(--space-lg);
  color: var(--slate);
  font-size: var(--text-body);
  line-height: var(--line-height-body);
}

.modal-body strong {
  color: var(--ink);
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-sm);
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--mist);
}

@media (max-width: 640px) {
  .characters {
    padding: var(--space-lg) var(--space-md);
  }

  .page-header {
    flex-direction: column;
  }
}
</style>
