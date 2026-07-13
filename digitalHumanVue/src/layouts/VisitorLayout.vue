<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="visitor-layout">
    <header class="visitor-header">
      <RouterLink to="/visitor" class="brand">景区导览 AI 数字人</RouterLink>
      <nav>
        <RouterLink to="/visitor">首页</RouterLink>
        <RouterLink to="/visitor/chat">数字人</RouterLink>
      </nav>
    </header>
    <main class="visitor-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.visitor-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  color: var(--color-ink);
  background: var(--color-paper);
}

.visitor-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--color-border);
}

.brand {
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--color-ink);
}

nav {
  display: flex;
  gap: var(--space-md);
}

nav a {
  font-size: var(--text-body);
}

nav a.router-link-active {
  color: var(--color-accent);
}
</style>
