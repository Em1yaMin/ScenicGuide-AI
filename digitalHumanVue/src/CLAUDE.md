# Vue 3 Frontend Source Root

## Responsibility
Vue 3 + TypeScript + Vite 8 SPA 入口层。`main.ts` 负责应用引导（Pinia + Router 插件安装），`App.vue` 仅作为路由出口壳。

## Architecture (引导序列)
```
main.ts → createApp(App) → .use(createPinia()) → .use(router) → .mount('#app')
  │
  └── import './assets/main.css'  (导入 styles/design-tokens.css + styles/variables.css + styles/base.css)
```

## Design System (CSS token 体系)
```
styles/design-tokens.css — 设计体系 token 权威源（--ink/--paper/--jade/--cinnabar/--mist/--slate/--surface + 间距/圆角/阴影/动效/字阶）
styles/variables.css     — 旧 --color-* 兼容别名层（映射到 design-tokens.css；已无存量依赖，保留兼容）
styles/base.css          — CSS reset + 排版基准
assets/main.css          — #app { min-height: 100vh } 入口（依次 import design-tokens → variables → base）
```
所有组件使用 `<style scoped>` + `var(--*)` 令牌，**不使用** Sass/Tailwind。权威约定见 [`doc/frontend-conventions.md`](../../doc/frontend-conventions.md)「样式方案」与 [`doc/redesign-v1.md`](../../doc/redesign-v1.md)（token 权威文档，改 token 需同步）。

## API Client Pattern

统一走 `src/utils/fetchWithCodeEnvelope.ts` 封装（双向错误检查 + 默认 15s 超时 + FastAPI detail 三形态解析），按域封装见 `api/auth.ts` / `api/memory.ts` / `api/usage.ts` / `api/adminAgent.ts`；端点常量集中于 `api/index.ts`（禁止组件内硬编码 URL）。**权威约定见 [`doc/frontend-conventions.md`](../../doc/frontend-conventions.md)「API 封装模式」**，此处不重复全文。
**不使用 axios** — 全部用原生 `fetch()`（经 fetchWithCodeEnvelope 封装）。Vite dev proxy（`vite.config.ts`）将 `/api` 转发到后端。

## Pinia Store Pattern
```ts
// stores/visitor.ts — Composition API (setup store) 语法
// generateUUID(): 优先 crypto.randomUUID()，非安全上下文（HTTP 局域网 IP）降级为 Math.random + Date.now 生成 UUID v4
import { generateUUID } from '@/utils/uuid'
export const useVisitorStore = defineStore('visitor', () => {
  const visitorId = ref(localStorage.getItem(STORAGE_KEY) ?? generateUUID())
  const isAuthenticated = computed(() => Boolean(visitorId.value))
  return { visitorId, isAuthenticated }
})
```

## Auth Store Pattern（stores/auth.ts）
```ts
// token 持久化 localStorage：auth_token（JWT）+ auth_user（用户信息 JSON）
// 游客兜底：enterGuestMode() 签发短期游客 token；App.vue 启动时 restoreSession() 校验 /auth/me
// 注入请求头：authHeaders() → { Authorization: 'Bearer <token>' }（未登录返回空对象，保持向后兼容）
```

## Router Pattern
```ts
// router/index.ts — 嵌套布局路由
{ path: '/visitor', component: VisitorLayout,  // 布局静态导入
  children: [
    { path: '',       component: () => import('@/views/HomeView.vue') },  // 视图懒加载
    { path: 'chat',   component: () => import('@/views/ChatView.vue') },
  ]}
// 例外：认证页 /login /register 为顶层路由（不挂布局），访客端与管理后台均可直达（见 views/CLAUDE.md）
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
3. 在 `api/xxx.ts` 按域封装 `fetchWithCodeEnvelope` 调用（SSE/CSV 特例见 doc/frontend-conventions.md），view/store 中经封装调用
</important>
