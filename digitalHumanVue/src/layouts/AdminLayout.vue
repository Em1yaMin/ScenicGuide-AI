<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="admin-layout">
    <aside class="admin-sidebar">
      <div class="brand">管理后台</div>
      <nav>
        <RouterLink to="/admin">数据大屏</RouterLink>
        <RouterLink to="/admin/report">感受度报告</RouterLink>
      </nav>
    </aside>
    <main class="admin-main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.admin-layout {
  min-height: 100vh;
  display: flex;
  color: var(--color-ink);
  background: var(--color-paper);
}

.admin-sidebar {
  width: 240px;
  padding: var(--space-lg);
  border-right: 1px solid var(--color-border);
}

.brand {
  font-family: var(--font-display);
  font-size: var(--text-title);
  margin-bottom: var(--space-lg);
}

nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-sm);
}

nav a {
  padding: var(--space-sm);
}

nav a.router-link-active {
  color: var(--color-accent);
  background: var(--color-accent-light);
}
</style>
