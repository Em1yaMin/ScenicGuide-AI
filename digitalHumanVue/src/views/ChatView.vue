<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useVisitorStore } from '@/stores/visitor'
import { useAuthStore } from '@/stores/auth'
import { API_VISITOR_RECOMMENDED_QUESTIONS } from '@/api'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const store = useVisitorStore()
const auth = useAuthStore()

const iframeSrc = computed(() => {
  const base = import.meta.env.VITE_LIGHT4AI_URL
  if (!base) return ''
  const params = new URLSearchParams()
  params.set('visitor_id', store.visitorId)
  params.set('live2d_model', import.meta.env.VITE_LIVE2D_MODEL || 'hiyori')
  // 登录用户 / 游客 token：供数字人端透传对话接口（Bearer），实现「对话携带 token」
  if (auth.token) {
    params.set('auth_token', auth.token)
  }
  const separator = base.includes('?') ? '&' : '?'
  return `${base}${separator}${params.toString()}`
})

const IFRAME_LOAD_TIMEOUT_MS = 10_000
const MAX_RETRIES = 3

// ── iframe fault tolerance state ──
const iframeKey = ref(0)
const iframeReady = ref(false)
const isLoading = ref(true)
const loadingError = ref(false)
const retryCount = ref(0)
const hasLoadedOnce = ref(false)
let timeoutId: ReturnType<typeof setTimeout> | null = null

function startLoadingTimeout() {
  if (timeoutId) clearTimeout(timeoutId)
  timeoutId = setTimeout(() => {
    if (isLoading.value) {
      isLoading.value = false
      loadingError.value = true
    }
  }, IFRAME_LOAD_TIMEOUT_MS)
}

function onIframeLoad() {
  if (loadingError.value) return
  if (hasLoadedOnce.value) {
    if (timeoutId) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
    return
  }
  hasLoadedOnce.value = true
  isLoading.value = false
  loadingError.value = false
  retryCount.value = 0
  if (timeoutId) {
    clearTimeout(timeoutId)
    timeoutId = null
  }
}

function onIframeError() {
  isLoading.value = false
  loadingError.value = true
  if (timeoutId) {
    clearTimeout(timeoutId)
    timeoutId = null
  }
}

function reloadIframe() {
  if (retryCount.value >= MAX_RETRIES) return
  isLoading.value = true
  loadingError.value = false
  hasLoadedOnce.value = false
  retryCount.value += 1
  iframeKey.value += 1
  startLoadingTimeout()
}

onMounted(async () => {
  // 游客兜底：未登录时自动签发短期游客 token（失败静默降级，不阻塞对话使用）
  if (!auth.isAuthenticated) {
    await auth.enterGuestMode()
  }
  iframeReady.value = true
  await Promise.all([store.registerVisitor(), fetchRecommendedQuestions()])
  if (iframeSrc.value) {
    startLoadingTimeout()
  }
})

onUnmounted(() => {
  if (timeoutId) {
    clearTimeout(timeoutId)
    timeoutId = null
  }
})

// ── Suggested Questions (dynamic, from backend recommend task) ──
// 数字人 iframe 已内置完整对话功能（输入框/语音/聊天记录），本页不再有独立
// 聊天面板；推荐问句以底部浮层呈现，点击后经 postMessage SET_INPUT 注入
// 数字人输入框（不自动发送，由用户确认后发送）。
const suggestedQuestions = ref<string[]>([])
const questionsLoading = ref(true)
/** 浮层展开/折叠（点击推荐问句注入后自动收起，避免遮挡数字人输入框）。 */
const questionsVisible = ref(true)

async function fetchRecommendedQuestions() {
  questionsLoading.value = true
  try {
    const data = await fetchWithCodeEnvelope<{ questions: string[] }>(
      API_VISITOR_RECOMMENDED_QUESTIONS,
    )
    if (data.questions && Array.isArray(data.questions)) {
      suggestedQuestions.value = data.questions
    }
  } catch {
    // Fall back to empty — overlay shows a generic hint instead
    suggestedQuestions.value = []
  } finally {
    questionsLoading.value = false
  }
}

function onQuestionClick(text: string) {
  // 经 postMessage SET_INPUT 注入 ADH 数字人输入框（不自动发送，由用户确认）
  const targetOrigin = import.meta.env.VITE_ADH_IFRAME_ORIGIN || '*'
  const iframe = document.querySelector('.digital-human-frame') as HTMLIFrameElement | null
  iframe?.contentWindow?.postMessage({ type: 'SET_INPUT', text }, targetOrigin)
  // 注入后自动收起浮层，露出数字人输入框供用户确认发送
  questionsVisible.value = false
}
</script>

<template>
  <div class="chat">
    <!-- 数字人 iframe 区域：占满整个页面 -->
    <div class="chat-human">
      <div class="human-frame">
        <!-- Config missing -->
        <div v-if="!iframeSrc" class="placeholder-panel">
          <div class="placeholder-icon">⚙</div>
          <p>请在 .env 中配置 VITE_LIGHT4AI_URL</p>
        </div>

        <div v-else-if="!iframeReady" class="placeholder-panel loading-overlay">
          <div class="loading-spinner" />
          <p>数字人加载中，请稍候…</p>
        </div>

        <!-- iframe (always in DOM for @load/@error) -->
        <template v-else>
          <iframe
            v-show="!loadingError"
            :key="iframeKey"
            :src="iframeSrc"
            title="数字人"
            allow="microphone"
            class="digital-human-frame"
            @load="onIframeLoad"
            @error="onIframeError"
          />

          <!-- Loading overlay -->
          <div v-if="isLoading && !loadingError" class="placeholder-panel loading-overlay">
            <div class="loading-spinner" />
            <p>数字人加载中，请稍候…</p>
          </div>

          <!-- Error state -->
          <div v-if="!isLoading && loadingError" class="placeholder-panel">
            <div class="placeholder-icon">⚠</div>
            <p>数字人服务加载失败</p>
            <button v-if="retryCount < MAX_RETRIES" class="retry-btn" @click="reloadIframe">
              重新加载
            </button>
            <p v-else class="retry-note">已超过最大重试次数，请稍后再试</p>
          </div>
        </template>

        <!-- 推荐问句浮层（叠加在数字人区域底部，不遮挡主体过多） -->
        <div class="questions-overlay">
          <button
            v-if="!questionsVisible"
            class="questions-reopen"
            type="button"
            @click="questionsVisible = true"
          >
            💬 推荐问题
          </button>

          <template v-else>
            <!-- 加载中 -->
            <div v-if="questionsLoading" class="questions-bar">
              <span class="loading-spinner loading-spinner--sm" />
              <span>推荐问句加载中…</span>
            </div>

            <!-- 有推荐问句 -->
            <div v-else-if="suggestedQuestions.length > 0" class="questions-bar">
              <span class="questions-label">您可以这样问我</span>
              <div class="questions-scroll">
                <button
                  v-for="q in suggestedQuestions"
                  :key="q"
                  type="button"
                  class="question-chip"
                  @click="onQuestionClick(q)"
                >
                  {{ q }}
                </button>
              </div>
              <button
                class="questions-toggle"
                type="button"
                title="收起"
                @click="questionsVisible = false"
              >
                ✕
              </button>
            </div>

            <!-- 空状态：无推荐问句时的通用提示 -->
            <div v-else class="questions-bar questions-bar--empty">
              <span>点击数字人麦克风开始语音对话</span>
              <button
                class="questions-toggle"
                type="button"
                title="关闭"
                @click="questionsVisible = false"
              >
                ✕
              </button>
            </div>
          </template>
        </div>
      </div>

      <!-- Status indicator -->
      <div v-if="hasLoadedOnce && !loadingError" class="status-bar">
        <span class="status-dot status-dot--live" />
        <span class="status-text">数字人在线</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat {
  padding: var(--space-lg);
  height: calc(100vh - 140px);
  display: flex;
  flex-direction: column;
}

/* ── Digital Human Panel（占满整页，无右侧聊天面板） ── */
.chat-human {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.human-frame {
  flex: 1;
  position: relative;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  overflow: hidden;
  background: var(--color-overlay);
}

.digital-human-frame {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: none;
}

.placeholder-panel {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-md);
  color: var(--color-ink-secondary);
  background: var(--color-paper);
}

.loading-overlay {
  background: var(--color-paper);
  z-index: 5;
}

.placeholder-icon {
  font-size: 2rem;
  opacity: 0.4;
}

.placeholder-panel p {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
  margin: 0;
  text-align: center;
}

.loading-spinner {
  width: 28px;
  height: 28px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.loading-spinner--sm {
  width: 14px;
  height: 14px;
  border-width: 1.5px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.retry-btn {
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-ink);
  color: var(--color-paper);
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  transition: background var(--duration-fast) var(--ease-out);
}

.retry-btn:hover {
  background: var(--color-accent);
}

.retry-note {
  font-size: var(--text-small) !important;
  opacity: 0.6;
}

/* Status bar */
.status-bar {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  margin-top: var(--space-sm);
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot--live {
  background: #6b9e6d;
  box-shadow: 0 0 0 3px rgba(107, 158, 109, 0.15);
}

.status-text {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

/* ── 推荐问句浮层（底部居中，半透明毛玻璃，可折叠） ── */
.questions-overlay {
  position: absolute;
  left: 50%;
  bottom: var(--space-md);
  transform: translateX(-50%);
  z-index: 20;
  max-width: calc(100% - var(--space-xl));
}

.questions-bar {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(8px);
  border: 1px solid var(--color-border);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

.questions-label {
  flex-shrink: 0;
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
}

.questions-scroll {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  overflow-x: auto;
  max-width: 560px;
  scrollbar-width: none;
}

.questions-scroll::-webkit-scrollbar {
  display: none;
}

.question-chip {
  flex-shrink: 0;
  padding: 6px 14px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-paper);
  font-size: var(--text-caption);
  color: var(--color-ink);
  cursor: pointer;
  user-select: none;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.question-chip:hover {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
}

.questions-toggle {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: var(--color-ink-secondary);
  font-size: var(--text-caption);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.questions-toggle:hover {
  background: var(--color-accent-light);
  color: var(--color-accent);
}

.questions-bar--empty {
  padding: var(--space-sm) var(--space-md);
}

/* 收起后的重新展开入口 */
.questions-reopen {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(8px);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  font-size: var(--text-small);
  color: var(--color-ink);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.questions-reopen:hover {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
}

/* ── Responsive ── */
@media (max-width: 900px) {
  .chat {
    height: auto;
    min-height: calc(100vh - 140px);
  }

  .questions-scroll {
    max-width: min(560px, calc(100vw - 160px));
  }
}
</style>
