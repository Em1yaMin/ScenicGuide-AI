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
        { path: 'knowledge', name: 'admin-knowledge', component: () => import('@/views/admin/KnowledgeView.vue') },
      ],
    },
  ],
})

export default router
