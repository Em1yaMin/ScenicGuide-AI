# Views (Page-Level Components)

## Responsibility

页面级 Vue SFC。被 Vue Router 懒加载，渲染在 Layout 组件的 `<RouterView />` 中。自行管理数据获取、状态和图表配置。

## Views Overview

| View                         | Route                   | Pattern                                                                                                                              | Data Source                                                  |
| ---------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------ |
| `HomeView.vue`               | `/visitor`              | 公告区（仅已发布，置顶优先，无公告隐藏）+ 热门景点/推荐路线活数据卡片（接口失败降级静态兜底）                                        | fetch(API_HOME_HIGHLIGHTS) + fetch(API_PUBLIC_ANNOUNCEMENTS) |
| `ChatView.vue`               | `/visitor/chat`         | iframe 嵌入+容错（数字人占满整页）+ 推荐问句底部浮层（postMessage SET_INPUT 注入数字人输入框，不自动发送）                           | import.meta.env + Pinia store                                |
| `DashboardView.vue`          | `/admin`                | API 数据+ECharts                                                                                                                     | fetch(API_ADMIN_DASHBOARD)                                   |
| `ReportView.vue`             | `/admin/report`         | API 数据+ECharts                                                                                                                     | fetch(API_ADMIN_REPORT)                                      |
| `admin/KnowledgeView.vue`    | `/admin/knowledge`      | API 数据+CRUD 弹窗                                                                                                                   | fetch(API_ADMIN_KNOWLEDGE)                                   |
| `admin/AnnouncementView.vue` | `/admin/announcements`  | API 数据+CRUD 弹窗（X-Admin-Token）                                                                                                  | fetch(API_ADMIN_ANNOUNCEMENTS)                               |
| `admin/MemoryView.vue`       | `/admin/memories`       | 双栏：用户列表（搜索/分页）→ 记忆列表（增/改/删，X-Admin-Token）                                                                     | src/api/memory.ts（API_ADMIN_MEMORIES）                      |
| `admin/AdminAgentView.vue`   | `/admin/agent`          | SSE 流式对话（token 逐字渲染 + 工具步骤气泡 + 二次确认按钮，X-Admin-Token）                                                          | src/api/adminAgent.ts（API_ADMIN_AGENT_CHAT_STREAM）         |
| `admin/UsageView.vue`        | `/admin/usage`          | 成本仪表盘：时间范围预设 + ECharts 趋势（Token 用量柱状，日/周/月粒度）+ 按模型/链路聚合 + 分页明细 + CSV 导出（X-Admin-Token） | src/api/usage.ts（API*ADMIN_USAGE*\*）                       |
| `admin/AdminLoginView.vue`   | `/admin/login`          | 管理员账号密码登录表单（批次9：主页入口/路由守卫重定向目标，样式对齐设计 token；成功签发管理员 JWT 后跳转 /admin）                   | Pinia adminAuth store（src/api/adminAuth.ts）                 |
| `LoginView.vue`              | `/login`（顶层路由）    | 表单+游客模式                                                                                                                        | Pinia auth store                                             |
| `RegisterView.vue`           | `/register`（顶层路由） | 表单校验+自动登录                                                                                                                    | Pinia auth store                                             |

## Data-Fetching View Pattern (三态模板)

每个数据视图使用完全一致的 4-ref 状态机：

```vue
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { API_ADMIN_DASHBOARD } from '@/api'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'

const data = ref<ResponseType | null>(null)
const isLoading = ref(true)
const error = ref<string | null>(null)
const hasData = computed(() => data.value !== null && /* 检查是否有实际内容 */)

async function fetchData() {
  isLoading.value = true; error.value = null
  try {
    // 统一走 fetchWithCodeEnvelope（双向错误检查 + 超时），详见 doc/frontend-conventions.md「API 封装模式」
    data.value = await fetchWithCodeEnvelope<ResponseType>(API_ADMIN_DASHBOARD)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '未知错误'
  } finally { isLoading.value = false }
}

onMounted(() => { fetchData() })
</script>

<template>
  <div class="page">
    <div v-if="error" class="error-state">
      <!-- 错误 -->
      <p>加载失败: {{ error }}</p>
      <button @click="fetchData">重试</button>
    </div>
    <div v-else-if="!hasData && !isLoading" class="empty-state">
      <!-- 空 -->
      <p>暂无数据</p>
    </div>
    <div v-else>内容区域</div>
    <!-- 数据 -->
  </div>
</template>
```

## Iframe Embed Pattern (ChatView)

```ts
const iframeKey = ref(0) // 递增强制重新挂载
const iframeSrc = computed(() => {
  const base = import.meta.env.VITE_SERVICE_URL
  return `${base}?param=${encodeURIComponent(value)}`
})
// iframe 用 v-show（非 v-if）保持 DOM 使 @load/@error 事件触发
// 10 秒超时兜底 + onUnmounted 清理
```

## ECharts Integration

1. 模块顶层调用 `registerECharts()`（`composables/useECharts.ts`）一次
2. chart options 作为 `computed` 从 `data` 派生
3. `<VChart :option="computed" :loading="isLoading" autoresize />`

## Shared Components Convention

error-state、empty-state 等重复的 UI 模式应提取到 `components/` 目录作为可复用组件（`ErrorState.vue`、`EmptyState.vue`），而非在每个 View 中内联重复。

## Architectural Boundaries

- **NO 直接导入其他 View** — 导航仅通过 `<RouterLink>`
- View 不接收 props — 数据通过 store 或自身 fetch 获取
- View 不定义 `<slot>` — 不是可组合组件

<important if="you are adding a new page to this layer">
## Adding a New Page
1. 确定模式：静态 | iframe 嵌入 | 数据+图表
2. 以最近的同类 View 为模板创建 `views/XxxView.vue`
3. 如需数据，在 `api/index.ts` 添加端点常量
4. 在 `router/index.ts` 对应 layout 的 children 中添加懒加载路由
</important>
