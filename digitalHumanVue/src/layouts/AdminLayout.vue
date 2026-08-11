<script setup lang="ts">
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { useAdminAuthStore } from '@/stores/adminAuth'

const adminAuth = useAdminAuthStore()
const route = useRoute()

/** 管理员退出登录（批次9：账号密码登录替代 X-Admin-Token 输入形式）。 */
function adminLogout() {
  adminAuth.logout()
  // 清除后路由守卫会自动重定向到 /admin/login（当前页仍在管理端布局内）
  window.location.href = '/admin/login'
}
</script>

<template>
  <div class="admin-layout">
    <aside class="admin-sidebar">
      <div class="sidebar-brand">
        <span class="brand-seal">管</span>
        <span class="brand-text">景区管理后台</span>
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
        <RouterLink to="/admin/agent" class="nav-item">
          <span class="nav-icon">✦</span>
          <span>管理员 Agent</span>
        </RouterLink>
        <RouterLink to="/admin/usage" class="nav-item">
          <span class="nav-icon">◔</span>
          <span>成本仪表盘</span>
        </RouterLink>
      </nav>
      <div class="sidebar-footer">
        <!-- 管理身份（批次9：账号密码登录替代 X-Admin-Token 输入形式） -->
        <div class="admin-area">
          <template v-if="adminAuth.isLoggedIn">
            <span class="auth-user">管理：{{ adminAuth.username }}</span>
            <button type="button" class="auth-btn" @click="adminLogout">退出</button>
          </template>
          <template v-else>
            <RouterLink
              :to="{ path: '/admin/login', query: { redirect: route.fullPath } }"
              class="auth-link"
              >管理登录</RouterLink
            >
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
/* 第二阶段视觉打磨：侧边栏/内容区在新 token 体系下的精修。
 * 数据工具风：与访客端同一体系（青玉/朱砂/宣纸），但更紧凑——
 * 素白侧边栏、6px 圆角、无大阴影，信息密度优先。 */
.admin-layout {
  min-height: 100vh;
  display: flex;
  color: var(--ink);
  background: var(--paper);
}

.admin-sidebar {
  width: 232px;
  min-height: 100vh;
  padding: var(--space-lg) 0;
  border-right: 1px solid var(--mist);
  background: var(--surface);
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 0;
  height: 100vh;
}

/* 品牌区：朱砂印章式方块（印章母题，见 doc/redesign-v1.md §4） */
.sidebar-brand {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-xs) var(--space-lg) var(--space-lg);
  border-bottom: 1px solid var(--mist);
  margin-bottom: var(--space-md);
}

.brand-seal {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  background: var(--cinnabar);
  color: var(--surface);
  font-family: var(--font-display);
  font-size: var(--text-small);
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
  transform: rotate(-2deg);
}

.brand-text {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink);
  white-space: nowrap;
}

.sidebar-nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 var(--space-sm);
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  padding: var(--space-xs) var(--space-md);
  color: var(--slate);
  text-decoration: none;
  font-size: var(--text-small);
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  transition:
    color var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}

.nav-item:hover {
  color: var(--ink);
  background: var(--overlay);
}

/* 精确匹配（router-link-exact-active）：仅当前路径命中，避免「数据大屏」在子路由（/admin/*）下仍高亮 */
.nav-item.router-link-exact-active {
  color: var(--jade);
  background: var(--jade-soft);
  border-color: var(--mist);
  font-weight: 600;
}

.nav-icon {
  font-size: var(--text-body);
  width: 1.25em;
  text-align: center;
  flex-shrink: 0;
}

.sidebar-footer {
  padding: var(--space-md) var(--space-lg);
  border-top: 1px solid var(--mist);
  margin-top: auto;
}

/* 管理身份区（批次9）：侧栏底部仅保留管理登录/退出 */
.admin-area {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-xs);
  flex-wrap: wrap;
  padding-bottom: var(--space-xs);
  border-bottom: 1px dashed var(--mist);
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
  border: 1px solid var(--mist);
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

.version {
  font-size: var(--text-caption);
  color: var(--slate);
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
