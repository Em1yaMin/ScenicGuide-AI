<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useVisitorStore } from '@/stores/visitor'

const store = useVisitorStore()

const iframeSrc = computed(() => {
  const base = import.meta.env.VITE_LIGHT4AI_URL
  if (!base) {
    return ''
  }
  const separator = base.includes('?') ? '&' : '?'
  return `${base}${separator}visitor_id=${encodeURIComponent(store.visitorId)}`
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
  if (timeoutId) {
    clearTimeout(timeoutId)
  }
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
    // Internal navigation: only clean up stale timeout
    if (timeoutId) {
      clearTimeout(timeoutId)
      timeoutId = null
    }
    return
  }

  // First successful load
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
  await store.registerVisitor()
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
</script>

<template>
  <div class="chat-page">
    <h1>数字人对话</h1>

    <div class="iframe-container">
      <!-- No config -->
      <div v-if="!iframeSrc" class="placeholder">
        请在 .env 中配置 VITE_LIGHT4AI_URL
      </div>

      <template v-else>
        <!-- iframe — always in DOM to receive @load/@error events;
             hidden via v-show when error state is active -->
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

        <!-- Loading overlay (positioned on top of iframe) -->
        <div v-if="isLoading && !loadingError" class="placeholder loading-overlay">
          数字人加载中，请稍候...
        </div>

        <!-- Error state (replaces iframe while error is displayed) -->
        <div v-if="!isLoading && loadingError" class="placeholder">
          <p>数字人服务加载失败</p>
          <button
            v-if="retryCount < MAX_RETRIES"
            class="retry-btn"
            @click="reloadIframe"
          >
            重新加载
          </button>
          <p v-else>已超过最大重试次数，请稍后再试</p>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.chat-page {
  padding: var(--space-lg);
}

h1 {
  font-size: var(--text-display);
  margin-bottom: var(--space-md);
}

.iframe-container {
  position: relative;
  width: 100%;
  min-height: 70vh;
}

.digital-human-frame,
.placeholder {
  width: 100%;
  min-height: 70vh;
  border: 1px solid var(--color-border);
  background: var(--color-overlay);
}

.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-secondary);
  gap: var(--space-md);
}

.loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
}

.retry-btn {
  padding: var(--space-xs) var(--space-md);
  background: var(--color-accent);
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: var(--text-small);
}

.retry-btn:hover {
  opacity: 0.9;
}
</style>
