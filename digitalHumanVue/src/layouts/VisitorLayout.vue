<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="visitor-layout">
    <header class="visitor-header">
      <RouterLink to="/visitor" class="brand">
        <span class="brand-icon">⛰</span>
        <span class="brand-text">景区导览 AI 数字人</span>
      </RouterLink>
      <nav>
        <RouterLink to="/visitor">首页</RouterLink>
        <RouterLink to="/visitor/chat">数字人</RouterLink>
      </nav>
    </header>

    <main class="visitor-main">
      <RouterView v-slot="{ Component }">
        <Transition name="page-fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>

    <footer class="visitor-footer">
      <div class="footer-inner">
        <span class="footer-brand">景区导览 AI 数字人</span>
        <span class="footer-divider">·</span>
        <span class="footer-copy">© 2026 智慧导览系统</span>
      </div>
    </footer>
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
  padding: var(--space-lg) var(--space-2xl);
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  background: var(--color-paper);
  z-index: 10;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: var(--space-xs);
  color: var(--color-ink);
  text-decoration: none;
}

.brand-icon {
  font-size: var(--text-title);
  line-height: 1;
}

.brand-text {
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--color-ink);
}

nav {
  display: flex;
  gap: var(--space-xl);
}

nav a {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
  text-decoration: none;
  transition: color var(--duration-fast) var(--ease-out);
  padding-bottom: 2px;
}

nav a:hover {
  color: var(--color-ink);
}

nav a.router-link-active {
  color: var(--color-accent);
}

.visitor-main {
  flex: 1;
  width: 100%;
}

.visitor-footer {
  border-top: 1px solid var(--color-border);
  padding: var(--space-lg) var(--space-2xl);
  margin-top: var(--space-3xl);
}

.footer-inner {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

.footer-brand {
  font-family: var(--font-display);
}

.footer-divider {
  color: var(--color-border);
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
