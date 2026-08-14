<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useVisitorStore } from '@/stores/visitor'
import { useAuthStore } from '@/stores/auth'
import { API_VISITOR_RECOMMENDED_QUESTIONS } from '@/api'
import { fetchPublicCharacter } from '@/api/dhCharacter'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const store = useVisitorStore()
const auth = useAuthStore()

/**
 * 当前数字人形象：页面加载时先调公开接口 GET /api/v1/dh-character 取后端
 * 配置（管理后台形象管理页保存的全局形象），取到用接口值；失败/未取到
 * 回退 VITE_LIVE2D_MODEL || 'Hiyori'。
 */
const live2dModel = ref(import.meta.env.VITE_LIVE2D_MODEL || 'Hiyori')

async function loadLive2dModel() {
  try {
    const data = await fetchPublicCharacter()
    if (data.character) {
      live2dModel.value = data.character
    }
  } catch {
    // 失败/接口不可达：保持默认（VITE_LIVE2D_MODEL || 'Hiyori'），不阻断对话页
  }
}

const iframeSrc = computed(() => {
  const base = import.meta.env.VITE_LIGHT4AI_URL
  if (!base) return ''
  const params = new URLSearchParams()
  params.set('visitor_id', store.visitorId)
  params.set('live2d_model', live2dModel.value)
  // 嵌入模式：隐藏 ADH 顶部菜单/设置/画廊（配置由本后端定死）
  params.set('hide_menu', '1')
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
  // 动态形象 + 游客登记 + 推荐问句并行加载，全部就绪后再渲染 iframe
  //（live2d_model 参数以后端配置为准，接口失败回退 env/Hiyori）
  await Promise.all([loadLive2dModel(), store.registerVisitor(), fetchRecommendedQuestions()])
  iframeReady.value = true
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
        <div class="questions-overlay" role="region" aria-label="推荐问句">
          <Transition name="questions-pop" mode="out-in">
            <button
              v-if="!questionsVisible"
              class="questions-reopen"
              type="button"
              aria-label="展开推荐问句"
              @click="questionsVisible = true"
            >
              💬 推荐问题
            </button>

            <!-- 加载中 -->
            <div v-else-if="questionsLoading" class="questions-bar">
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
                aria-label="收起推荐问句"
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
                aria-label="关闭推荐问句"
                @click="questionsVisible = false"
              >
                ✕
              </button>
            </div>
          </Transition>
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

/* 数字人嵌入容器：统一遵循设计体系 --embed-* token（见 doc/redesign-v1.md §2.5） */
.human-frame {
  flex: 1;
  position: relative;
  min-width: var(--embed-min-width);
  min-height: var(--embed-min-height);
  border: var(--embed-border);
  border-radius: var(--embed-radius);
  overflow: hidden;
  background: var(--embed-bg);
  box-shadow: var(--embed-shadow);
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
  color: var(--slate);
  background: var(--paper);
}

.loading-overlay {
  background: var(--paper);
  z-index: 5;
}

.placeholder-icon {
  font-size: 2rem;
  opacity: 0.4;
}

.placeholder-panel p {
  font-size: var(--text-body);
  color: var(--slate);
  margin: 0;
  text-align: center;
}

.loading-spinner {
  width: 28px;
  height: 28px;
  border: 2px solid var(--mist);
  border-top-color: var(--jade);
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
  background: var(--jade);
  color: var(--surface);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-small);
  font-weight: 500;
  transition: background var(--duration-fast) var(--ease-out);
}

.retry-btn:hover {
  background: var(--jade-strong);
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
  color: var(--slate);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-dot--live {
  background: var(--jade);
  box-shadow: 0 0 0 3px var(--jade-soft);
}

.status-text {
  font-size: var(--text-small);
  color: var(--slate);
}

/* ── 推荐问句浮层（底部居中，素白底，可折叠） ── */
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
  border-radius: var(--radius-full);
  background: var(--surface);
  border: 1px solid var(--mist);
  box-shadow: var(--shadow-pop);
  font-size: var(--text-small);
  color: var(--slate);
}

.questions-label {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  flex-shrink: 0;
  font-size: var(--text-caption);
  color: var(--slate);
}

/* 标签前的朱砂小点：与首页题跋点同源 */
.questions-label::before {
  content: '';
  width: 6px;
  height: 6px;
  background: var(--cinnabar);
  border-radius: 2px;
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
  border: 1px solid var(--mist);
  border-radius: var(--radius-full);
  background: var(--paper);
  font-size: var(--text-caption);
  color: var(--ink);
  cursor: pointer;
  user-select: none;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.question-chip:hover {
  border-color: var(--jade);
  background: var(--jade-soft);
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
  color: var(--slate);
  font-size: var(--text-caption);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.questions-toggle:hover {
  background: var(--jade-soft);
  color: var(--jade);
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
  border: 1px solid var(--mist);
  border-radius: var(--radius-full);
  background: var(--surface);
  box-shadow: var(--shadow-pop);
  font-size: var(--text-small);
  color: var(--ink);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.questions-reopen:hover {
  border-color: var(--jade);
  background: var(--jade-soft);
}

/* 浮层开合：300ms 淡入上滑（唯一浮层动效，见 doc/redesign-v1.md §5） */
.questions-pop-enter-active,
.questions-pop-leave-active {
  transition:
    opacity var(--duration-normal) var(--ease-out),
    transform var(--duration-normal) var(--ease-out);
}

.questions-pop-enter-from,
.questions-pop-leave-to {
  opacity: 0;
  transform: translateY(8px);
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
