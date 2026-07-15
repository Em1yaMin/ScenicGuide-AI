# Vue 3 Frontend Source Root

## Responsibility
Vue 3 + TypeScript + Vite 8 SPA 入口层。`main.ts` 负责应用引导（Pinia + Router 插件安装），`App.vue` 仅作为路由出口壳。

## Architecture (引导序列)
```
main.ts → createApp(App) → .use(createPinia()) → .use(router) → .mount('#app')
  │
  └── import './assets/main.css'  (导入 styles/variables.css + styles/base.css)
```

## Design System (三层 CSS)
```
styles/variables.css  — :root { --color-*, --space-*, --text-*, --font-*, --duration-* }
styles/base.css       — CSS reset + 排版基准
assets/main.css       — #app { min-height: 100vh } 入口
```
所有组件使用 `<style scoped>` + `var(--*)` 令牌，**不使用** Sass/Tailwind。

## API Client Pattern
```ts
// api/index.ts — 集中式端点常量，禁止组件内硬编码 URL
export const API_VISITOR_REGISTER = '/api/v1/visitor/register'
export const API_ADMIN_DASHBOARD   = '/api/v1/admin/dashboard'

// 组件中使用原生 fetch()
const response = await fetch(API_ADMIN_DASHBOARD)
const result = await response.json()
if (result.code) { error.value = result.message || '服务器错误' }
```
**不使用 axios** — 全部用原生 `fetch()`。Vite dev proxy（`vite.config.ts`）将 `/api` 转发到后端。

## Pinia Store Pattern
```ts
// stores/visitor.ts — Composition API (setup store) 语法
export const useVisitorStore = defineStore('visitor', () => {
  const visitorId = ref(localStorage.getItem(STORAGE_KEY) ?? crypto.randomUUID())
  const isAuthenticated = computed(() => Boolean(visitorId.value))
  return { visitorId, isAuthenticated }
})
```

## Router Pattern
```ts
// router/index.ts — 嵌套布局路由
{ path: '/visitor', component: VisitorLayout,  // 布局静态导入
  children: [
    { path: '',       component: () => import('@/views/HomeView.vue') },  // 视图懒加载
    { path: 'chat',   component: () => import('@/views/ChatView.vue') },
  ]}
```

## Naming Conventions
- 视图: `XxxView.vue` | 布局: `XxxLayout.vue` | Store: `useXxxStore` | Composable: `useXxx.ts`
- CSS 类: `kebab-case` | Route 名: `role-page` | API 响应字段: `snake_case`（对齐后端）
- 路径别名: `@/` → `./src/`

## Sub-Layer Guides
- 页面组件 → `views/CLAUDE.md`
- 布局组件 → `layouts/CLAUDE.md`

<important if="you are adding a new API endpoint to the frontend">
1. 在 `api/index.ts` 添加 `export const API_MY_FEATURE = '/api/v1/my/feature'`
2. 如需类型，在 `types/` 创建接口文件
3. 在 view/store 中使用 `fetch(API_MY_FEATURE)` 调用
</important>
