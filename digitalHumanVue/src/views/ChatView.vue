<script setup lang="ts">
import { computed } from 'vue'
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
</script>

<template>
  <div class="chat-page">
    <h1>数字人对话</h1>
    <iframe
      v-if="iframeSrc"
      :src="iframeSrc"
      title="数字人"
      allow="microphone"
      class="digital-human-frame"
    />
    <div v-else class="placeholder">
      请在 .env 中配置 VITE_LIGHT4AI_URL
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

.digital-human-frame,
.placeholder {
  width: 100%;
  min-height: 70vh;
  border: 1px solid var(--color-border);
  background: var(--color-overlay);
}

.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-secondary);
}
</style>
