<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()

/** 登录/注册链接携带当前路径，登录后可跳回原页面。 */
function withRedirect(path: string) {
  return { path, query: { redirect: route.fullPath } }
}
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
      <div class="auth-area">
        <template v-if="auth.isLoggedIn">
          <span class="auth-user">{{ auth.displayName }}</span>
          <button type="button" class="auth-btn" @click="auth.logout()">退出</button>
        </template>
        <template v-else>
          <span v-if="auth.isGuest" class="auth-user">游客</span>
          <RouterLink :to="withRedirect('/login')" class="auth-link">登录</RouterLink>
          <RouterLink :to="withRedirect('/register')" class="auth-link">注册</RouterLink>
        </template>
      </div>
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

/* ── Auth area ── */
.auth-area {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  margin-left: var(--space-xl);
  white-space: nowrap;
}

.auth-user {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
}

.auth-link {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  text-decoration: none;
  transition: color var(--duration-fast) var(--ease-out);
}

.auth-link:hover {
  color: var(--color-accent);
}

.auth-btn {
  padding: 2px var(--space-sm);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: transparent;
  color: var(--color-ink-secondary);
  font-size: var(--text-caption);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.auth-btn:hover {
  color: var(--color-accent);
  border-color: var(--color-accent);
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
