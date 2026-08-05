<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useVisitorStore } from '@/stores/visitor'
import { useAuthStore } from '@/stores/auth'
import { useDialogueStream } from '@/composables/useDialogueStream'
import { API_VISITOR_RECOMMENDED_QUESTIONS } from '@/api'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const store = useVisitorStore()
const auth = useAuthStore()

// ── SSE 流式对话（步骤可视化 + 逐字回答） ──
const { turns, isStreaming, send } = useDialogueStream()
const draft = ref('')
const messagesEl = ref<HTMLElement | null>(null)

function submitDraft() {
  const text = draft.value.trim()
  if (!text || isStreaming.value) return
  draft.value = ''
  void send(text)
}

// 新内容到达时自动滚动到底部
watch(
  turns,
  async () => {
    await nextTick()
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight
    }
  },
  { deep: true },
)

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
  // 发送到 SSE 聊天面板（步骤可视化 + 逐字回答）
  void send(text)
  // 同时预填 ADH 数字人 iframe 输入框（保留原行为，不自动发送）
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

      <!-- Right: SSE Streaming Chat Panel -->
      <div class="chat-guide">
        <div class="guide-inner">
          <div class="guide-header">
            <h2>您好</h2>
            <p>我是您的景区 AI 数字人导览助手</p>
          </div>
          <div class="guide-divider" />

          <!-- Messages (SSE streaming: steps + typewriter answer) -->
          <div ref="messagesEl" class="chat-messages">
            <div v-if="turns.length === 0" class="chat-empty">
              <p>在下方输入问题开始对话，AI 将展示思考过程并逐字回答</p>
            </div>
            <div
              v-for="turn in turns"
              :key="turn.id"
              class="msg-row"
              :class="`msg-row--${turn.role}`"
            >
              <!-- Agent 思考过程/步骤可视化（仅助手消息） -->
              <div v-if="turn.role === 'assistant'" class="msg-steps">
                <div
                  v-for="(step, i) in turn.steps"
                  :key="i"
                  class="msg-step"
                  :class="{
                    'msg-step--active':
                      turn.streaming && turn.currentStep === step.label,
                  }"
                >
                  <span v-if="turn.streaming && turn.currentStep === step.label" class="step-spinner" />
                  <span v-else class="step-check">✓</span>
                  {{ step.label }}
                </div>
              </div>

              <div class="msg-bubble">
                <span v-if="turn.fallback" class="msg-fallback">
                  ⚠ 主链路异常，已自动切换备用服务
                </span>
                <span v-if="turn.error" class="msg-error">{{ turn.error }}</span>
                <template v-else>
                  {{ turn.content }}<span v-if="turn.streaming" class="msg-cursor" />
                </template>
              </div>
            </div>
          </div>

          <!-- Composer: suggested questions + input -->
          <div class="chat-composer">
            <p v-if="questionsLoading" class="guide-hint">推荐问句加载中…</p>
            <p v-else-if="suggestedQuestions.length > 0" class="guide-hint">您可以这样问我：</p>
            <div v-if="suggestedQuestions.length > 0" class="question-list">
              <div
                v-for="q in suggestedQuestions"
                :key="q"
                class="question-chip"
                :class="{ 'is-disabled': isStreaming }"
                @click="onQuestionClick(q)"
              >
                {{ q }}
              </div>
            </div>
            <div class="chat-input-row">
              <input
                v-model="draft"
                class="chat-input"
                placeholder="输入您的问题，Enter 发送…"
                :disabled="isStreaming"
                @keyup.enter="submitDraft"
              />
              <button
                class="chat-send"
                :disabled="isStreaming || !draft.trim()"
                @click="submitDraft"
              >
                {{ isStreaming ? '…' : '发送' }}
              </button>
            </div>
          </div>

          <div class="guide-footer">
            <div class="mic-hint">
              <span class="mic-icon">🎤</span>
              <span>点击左侧数字人麦克风开始语音对话</span>
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

/* ── Chat messages（SSE 流式渲染：步骤可视化 + 逐字回答） ── */
.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
  padding: var(--space-xs) 2px;
  scroll-behavior: smooth;
}

.chat-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-secondary);
  font-size: var(--text-small);
  text-align: center;
  padding: var(--space-lg);
}

.msg-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.msg-row--user {
  align-items: flex-end;
}

.msg-row--assistant {
  align-items: flex-start;
}

/* Agent 思考过程/步骤 chips */
.msg-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 100%;
}

.msg-step {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
  background: var(--color-accent-light);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  padding: 2px 8px;
  white-space: nowrap;
}

.msg-step--active {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.step-check {
  font-size: 10px;
  color: #6B9E6D;
  font-weight: 700;
}

.step-spinner {
  width: 10px;
  height: 10px;
  border: 1.5px solid var(--color-accent-light);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}

/* 消息气泡 */
.msg-bubble {
  max-width: 88%;
  padding: var(--space-sm) var(--space-md);
  border-radius: 12px;
  font-size: var(--text-small);
  line-height: 1.6;
  word-break: break-word;
  white-space: pre-wrap;
}

.msg-row--user .msg-bubble {
  background: var(--color-ink);
  color: var(--color-paper);
  border-bottom-right-radius: 4px;
}

.msg-row--assistant .msg-bubble {
  background: var(--color-paper);
  border: 1px solid var(--color-border);
  border-bottom-left-radius: 4px;
}

.msg-fallback {
  display: block;
  font-size: var(--text-caption);
  color: var(--color-accent);
  margin-bottom: 4px;
}

.msg-error {
  display: block;
  color: var(--color-accent);
}

/* 逐字显示光标 */
.msg-cursor {
  display: inline-block;
  width: 2px;
  height: 1em;
  margin-left: 2px;
  vertical-align: -0.15em;
  background: var(--color-accent);
  animation: blink 0.9s steps(2, start) infinite;
}

@keyframes blink {
  to { visibility: hidden; }
}

/* ── Composer（推荐问句 + 输入框） ── */
.chat-composer {
  padding-top: var(--space-md);
}

.chat-input-row {
  display: flex;
  gap: var(--space-sm);
  margin-top: var(--space-sm);
}

.chat-input {
  flex: 1;
  min-width: 0;
  padding: var(--space-sm) var(--space-md);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-paper);
  font-size: var(--text-small);
  font-family: var(--font-body);
  color: var(--color-ink);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out);
}

.chat-input:focus {
  border-color: var(--color-accent);
}

.chat-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.chat-send {
  flex-shrink: 0;
  padding: var(--space-sm) var(--space-lg);
  background: var(--color-ink);
  color: var(--color-paper);
  border: none;
  border-radius: 8px;
  font-size: var(--text-small);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.chat-send:hover:not(:disabled) {
  background: var(--color-accent);
}

.chat-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.question-chip.is-disabled {
  opacity: 0.5;
  pointer-events: none;
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
