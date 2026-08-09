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
  color: var(--slate);
  gap: var(--space-md);
}

.guide-btn {
  display: inline-block;
  padding: var(--space-xs) var(--space-md);
  background: var(--jade);
  color: var(--surface);
  border-radius: var(--radius-sm);
  text-decoration: none;
  font-size: var(--text-small);
  transition: background var(--duration-fast) var(--ease-out);
}

.guide-btn:hover {
  background: var(--jade-strong);
}
</style>
