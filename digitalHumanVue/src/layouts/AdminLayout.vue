<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()

/** 登录链接携带当前路径，登录后可跳回原页面。 */
function withRedirect(path: string) {
  return { path, query: { redirect: route.fullPath } }
}
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
        <RouterLink to="/admin/knowledge" class="nav-item">
          <span class="nav-icon">✎</span>
          <span>知识库</span>
        </RouterLink>
        <RouterLink to="/admin/announcements" class="nav-item">
          <span class="nav-icon">▤</span>
          <span>公告管理</span>
        </RouterLink>
        <RouterLink to="/admin/memories" class="nav-item">
          <span class="nav-icon">◉</span>
          <span>记忆管理</span>
        </RouterLink>
      </nav>
      <div class="sidebar-footer">
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

.auth-area {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-sm);
  flex-wrap: wrap;
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
