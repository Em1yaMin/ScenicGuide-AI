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
        <span class="brand-icon" aria-hidden="true">⛰</span>
        <span class="brand-text">景区导览 AI 数字人</span>
      </RouterLink>
      <nav aria-label="主导航">
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
        <span class="footer-seal" aria-hidden="true">导</span>
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
  color: var(--ink);
  background: var(--paper);
}

.visitor-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: var(--space-lg) var(--space-2xl);
  border-bottom: 1px solid var(--mist);
  position: sticky;
  top: 0;
  background: var(--paper);
  z-index: 10;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: var(--space-xs);
  color: var(--ink);
  text-decoration: none;
}

.brand-icon {
  font-size: var(--text-title);
  line-height: 1;
}

.brand-text {
  font-family: var(--font-display);
  font-size: var(--text-title);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink);
}

nav {
  display: flex;
  gap: var(--space-xl);
}

nav a {
  font-size: var(--text-body);
  color: var(--slate);
  text-decoration: none;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
  padding: 2px 0 6px;
  border-bottom: 2px solid transparent;
}

nav a:hover {
  color: var(--ink);
}

nav a.router-link-active {
  color: var(--jade);
  border-bottom-color: var(--jade);
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
  color: var(--slate);
}

.auth-link {
  font-size: var(--text-small);
  color: var(--slate);
  text-decoration: none;
  transition: color var(--duration-fast) var(--ease-out);
}

.auth-link:hover {
  color: var(--jade);
}

.auth-btn {
  padding: 2px var(--space-sm);
  border: 1px solid var(--mist-strong);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--slate);
  font-size: var(--text-caption);
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.auth-btn:hover {
  color: var(--jade);
  border-color: var(--jade);
}

.visitor-footer {
  border-top: 1px solid var(--mist);
  padding: var(--space-lg) var(--space-2xl);
  margin-top: var(--space-3xl);
}

.footer-inner {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-size: var(--text-small);
  color: var(--slate);
}

/* 页脚微缩印章：签名元素「导」的母题回响 */
.footer-seal {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  background: var(--cinnabar);
  color: var(--surface);
  font-family: var(--font-display);
  font-size: var(--text-tiny);
  font-weight: 700;
  border-radius: 3px;
}

.footer-brand {
  font-family: var(--font-display);
  font-weight: 700;
}

.footer-divider {
  color: var(--mist-strong);
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
