import { createRouter, createWebHistory } from 'vue-router'
import VisitorLayout from '@/layouts/VisitorLayout.vue'
import AdminLayout from '@/layouts/AdminLayout.vue'
import { useAdminAuthStore } from '@/stores/adminAuth'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/visitor' },
    {
      path: '/visitor',
      component: VisitorLayout,
      children: [
        { path: '', name: 'visitor-home', component: () => import('@/views/HomeView.vue') },
        { path: 'chat', name: 'visitor-chat', component: () => import('@/views/ChatView.vue') },
      ],
    },
    {
      path: '/admin',
      component: AdminLayout,
      children: [
        { path: '', name: 'admin-dashboard', component: () => import('@/views/DashboardView.vue') },
        { path: 'login', name: 'admin-login', component: () => import('@/views/admin/AdminLoginView.vue') },
        { path: 'report', name: 'admin-report', component: () => import('@/views/ReportView.vue') },
        {
          path: 'knowledge',
          name: 'admin-knowledge',
          component: () => import('@/views/admin/KnowledgeView.vue'),
        },
        {
          path: 'announcements',
          name: 'admin-announcements',
          component: () => import('@/views/admin/AnnouncementView.vue'),
        },
        {
          path: 'memories',
          name: 'admin-memories',
          component: () => import('@/views/admin/MemoryView.vue'),
        },
        {
          path: 'agent',
          name: 'admin-agent',
          component: () => import('@/views/admin/AdminAgentView.vue'),
        },
        {
          path: 'usage',
          name: 'admin-usage',
          component: () => import('@/views/admin/UsageView.vue'),
        },
        {
          path: 'users',
          name: 'admin-users',
          component: () => import('@/views/admin/UserAdminView.vue'),
        },
      ],
    },
    // 认证页为顶层路由：访客端与管理后台均可直达
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    { path: '/register', name: 'register', component: () => import('@/views/RegisterView.vue') },
  ],
})

/**
 * 管理端路由守卫（批次9）：未登录（无管理员 JWT）访问 /admin 重定向到
 * 管理员登录页 /admin/login（携带 redirect 以便登录后跳回）；/admin/login
 * 本身放行。X-Admin-Token 旧用法不受影响（localStorage 直读，见
 * utils/adminHeaders.ts）。
 */
router.beforeEach((to) => {
  if (to.path.startsWith('/admin') && to.path !== '/admin/login') {
    const adminAuth = useAdminAuthStore()
    if (!adminAuth.isLoggedIn) {
      return {
        path: '/admin/login',
        query: to.fullPath !== '/admin' ? { redirect: to.fullPath } : undefined,
      }
    }
  }
  return true
})

export default router
