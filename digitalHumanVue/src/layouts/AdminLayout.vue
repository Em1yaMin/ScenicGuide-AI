<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="admin-layout">
    <aside class="admin-sidebar">
      <div class="sidebar-brand">
        <span class="brand-dot" />
        <span class="brand-text">管理后台</span>
      </div>
      <nav class="sidebar-nav">
        <RouterLink to="/admin" class="nav-item">
          <span class="nav-icon">◫</span>
          <span>数据大屏</span>
        </RouterLink>
        <RouterLink to="/admin/report" class="nav-item">
          <span class="nav-icon">☷</span>
          <span>感受度报告</span>
        </RouterLink>
      </nav>
      <div class="sidebar-footer">
        <span class="version">v0.2</span>
      </div>
    </aside>
    <main class="admin-main">
      <RouterView v-slot="{ Component }">
        <Transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
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
  min-height: 100vh;
  padding: var(--space-xl) 0;
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: 0 var(--space-xl) var(--space-xl);
  border-bottom: 1px solid var(--color-border);
  margin-bottom: var(--space-lg);
}

.brand-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  flex-shrink: 0;
}

.brand-text {
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--color-ink);
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 var(--space-md);
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  color: var(--color-ink-secondary);
  text-decoration: none;
  font-size: var(--text-body);
  border-radius: 6px;
  transition:
    color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out);
}

.nav-item:hover {
  color: var(--color-ink);
  background: var(--color-overlay);
}

.nav-item.router-link-active {
  color: var(--color-accent);
  background: var(--color-accent-light);
  font-weight: 500;
}

.nav-icon {
  font-size: var(--text-body);
  width: 1.25em;
  text-align: center;
  flex-shrink: 0;
}

.sidebar-footer {
  padding: var(--space-lg) var(--space-xl);
  border-top: 1px solid var(--color-border);
  margin-top: auto;
}

.version {
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
  font-family: var(--font-mono);
}

.admin-main {
  flex: 1;
  min-width: 0;
  overflow-x: hidden;
}

/* Page transition */
.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity var(--duration-normal) var(--ease-out);
}

.page-fade-enter-from,
.page-fade-leave-to {
  opacity: 0;
}
</style>
