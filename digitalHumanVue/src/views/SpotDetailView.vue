<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="page">
    <h1>景点详情</h1>
    <p class="meta">景点 ID：{{ route.params.id }}</p>
  </div>
</template>

<style scoped>
.page {
  padding: var(--space-lg);
}

.meta {
  margin-top: var(--space-md);
  color: var(--color-ink-secondary);
}
</style>
