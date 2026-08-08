import { createRouter, createWebHistory } from 'vue-router'
import VisitorLayout from '@/layouts/VisitorLayout.vue'
import AdminLayout from '@/layouts/AdminLayout.vue'

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
      ],
    },
    // 认证页为顶层路由：访客端与管理后台均可直达
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    { path: '/register', name: 'register', component: () => import('@/views/RegisterView.vue') },
  ],
})

export default router
