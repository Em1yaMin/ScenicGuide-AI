<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useVisitorStore } from '@/stores/visitor'
import { API_VISITOR_RECOMMENDED_QUESTIONS } from '@/api'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const store = useVisitorStore()

const iframeSrc = computed(() => {
  const base = import.meta.env.VITE_LIGHT4AI_URL
  if (!base) return ''
  const params = new URLSearchParams()
  params.set('visitor_id', store.visitorId)
  params.set('live2d_model', import.meta.env.VITE_LIVE2D_MODEL || 'hiyori')
  const separator = base.includes('?') ? '&' : '?'
  return `${base}${separator}${params.toString()}`
})

const IFRAME_LOAD_TIMEOUT_MS = 10_000
const MAX_RETRIES = 3

// ── iframe fault tolerance state ──
const iframeKey = ref(0)
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
const suggestedQuestions = ref<string[]>([])
const questionsLoading = ref(false)

async function fetchRecommendedQuestions() {
  questionsLoading.value = true
  try {
    const data = await fetchWithCodeEnvelope<{ questions: string[] }>(
      API_VISITOR_RECOMMENDED_QUESTIONS
    )
    if (data.questions && Array.isArray(data.questions)) {
      suggestedQuestions.value = data.questions
    }
  } catch {
    // Fall back to empty — guide panel will show generic hint instead
    suggestedQuestions.value = []
  } finally {
    questionsLoading.value = false
  }
}

function onQuestionClick(text: string) {
  const targetOrigin = import.meta.env.VITE_ADH_IFRAME_ORIGIN || '*'
  const iframe = document.querySelector(
    '.digital-human-frame'
  ) as HTMLIFrameElement | null
  iframe?.contentWindow?.postMessage(
    { type: 'SET_INPUT', text },
    targetOrigin
  )
}
</script>

<template>
  <div class="chat">
    <div class="chat-layout">
      <!-- Left: Live2D Digital Human iframe -->
      <div class="chat-human">
        <div class="human-frame">
          <!-- Config missing -->
          <div v-if="!iframeSrc" class="placeholder-panel">
            <div class="placeholder-icon">⚙</div>
            <p>请在 .env 中配置 VITE_LIGHT4AI_URL</p>
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
              <button
                v-if="retryCount < MAX_RETRIES"
                class="retry-btn"
                @click="reloadIframe"
              >
                重新加载
              </button>
              <p v-else class="retry-note">已超过最大重试次数，请稍后再试</p>
            </div>
          </template>
        </div>

        <!-- Status indicator -->
        <div v-if="hasLoadedOnce && !loadingError" class="status-bar">
          <span class="status-dot status-dot--live" />
          <span class="status-text">数字人在线</span>
        </div>
      </div>

      <!-- Right: Welcome / Guide Panel -->
      <div class="chat-guide">
        <div class="guide-inner">
          <div class="guide-header">
            <h2>您好</h2>
            <p>我是您的景区 AI 数字人导览助手</p>
          </div>
          <div class="guide-divider" />
          <div class="guide-questions">
            <p v-if="questionsLoading" class="guide-hint">推荐问句加载中…</p>
            <p v-else-if="suggestedQuestions.length > 0" class="guide-hint">您可以这样问我：</p>
            <p v-else class="guide-hint">暂无推荐问句</p>
            <div class="question-list">
              <div
                v-for="q in suggestedQuestions"
                :key="q"
                class="question-chip"
                @click="onQuestionClick(q)"
              >
                {{ q }}
              </div>
            </div>
          </div>
          <div class="guide-footer">
            <div class="mic-hint">
              <span class="mic-icon">🎤</span>
              <span>点击麦克风图标开始语音对话</span>
            </div>
          </div>
        </div>
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

.chat-layout {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 380px;
  gap: var(--space-lg);
  min-height: 0;
}

/* ── Left: Digital Human Panel ── */
.chat-human {
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

@keyframes spin {
  to { transform: rotate(360deg); }
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
  background: #6B9E6D;
  box-shadow: 0 0 0 3px rgba(107, 158, 109, 0.15);
}

.status-text {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

/* ── Right: Guide Panel ── */
.chat-guide {
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.guide-inner {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: var(--space-xl);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: transparent;
}

.guide-header {
  margin-bottom: var(--space-lg);
}

.guide-header h2 {
  font-family: var(--font-display);
  font-size: var(--text-display);
  margin: 0 0 var(--space-sm);
  color: var(--color-ink);
}

.guide-header p {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
  margin: 0;
  line-height: 1.6;
}

.guide-divider {
  height: 1px;
  background: var(--color-border);
  margin-bottom: var(--space-lg);
}

.guide-questions {
  flex: 1;
}

.guide-hint {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin: 0 0 var(--space-md);
}

.question-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

.question-chip {
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: var(--text-small);
  color: var(--color-ink);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
  user-select: none;
}

.question-chip:hover {
  border-color: var(--color-accent);
  background: var(--color-accent-light);
}

.guide-footer {
  padding-top: var(--space-lg);
  border-top: 1px solid var(--color-border);
}

.mic-hint {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

.mic-icon {
  font-size: var(--text-body);
}

/* ── Responsive ── */
@media (max-width: 900px) {
  .chat {
    height: auto;
    min-height: calc(100vh - 140px);
  }

  .chat-layout {
    grid-template-columns: 1fr;
    grid-template-rows: 1fr auto;
  }

  .chat-guide {
    max-height: none;
  }

  .guide-inner {
    padding: var(--space-lg);
  }
}
</style>
