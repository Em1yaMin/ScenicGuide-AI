<script setup lang="ts">
import { RouterLink } from 'vue-router'

defineProps<{
  message: string
  to: string
  buttonText?: string
}>()
</script>

<template>
  <div class="empty-state">
    <p>{{ message }}</p>
    <RouterLink :to="to" class="guide-btn">{{ buttonText ?? '前往数字人对话' }}</RouterLink>
  </div>
</template>

<style scoped>
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--space-3xl);
  color: var(--color-ink-secondary);
  gap: var(--space-md);
}

.guide-btn {
  display: inline-block;
  padding: var(--space-xs) var(--space-md);
  background: var(--color-accent);
  color: #fff;
  border-radius: 4px;
  text-decoration: none;
  font-size: var(--text-small);
}
</style>
