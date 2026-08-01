# Layout Components

## Responsibility
页面布局壳组件。作为 Vue Router 的父路由 `component`，提供导航 + `<RouterView />` 内容出口。纯展示，无数据获取。

## Dependencies
- **vue-router**: `RouterLink`、`RouterView`（唯一导入）

## Consumers
- **router/index.ts**: 作为父路由的 `component` 属性

## Layout Patterns

### AdminLayout — 侧边栏布局
```vue
<template>
  <div class="admin-layout">
    <aside class="admin-sidebar">      <!-- 固定 240px 侧边栏 -->
      <div class="brand">管理后台</div>
      <nav>
        <RouterLink to="/admin">数据大屏</RouterLink>
        <RouterLink to="/admin/report">感受度报告</RouterLink>
        <RouterLink to="/admin/knowledge">知识库</RouterLink>
      </nav>
    </aside>
    <main class="admin-main">
      <RouterView />                   <!-- 子路由渲染 -->
    </main>
  </div>
</template>
```
CSS: `display: flex`（行向）、`border-right` 分隔线

### VisitorLayout — 顶部导航布局
```vue
<template>
  <div class="visitor-layout">
    <header class="visitor-header">   <!-- 水平导航栏 -->
      <RouterLink to="/visitor" class="brand">景区导览 AI 数字人</RouterLink>
      <nav>
        <RouterLink to="/visitor">首页</RouterLink>
        <RouterLink to="/visitor/chat">数字人</RouterLink>
      </nav>
    </header>
    <main class="visitor-main">
      <RouterView />                   <!-- 子路由渲染 -->
    </main>
  </div>
</template>
```
CSS: `display: flex; flex-direction: column`、`border-bottom` 分隔线

## Active Link Styling
```css
nav a.router-link-active {
  color: var(--color-accent);
  /* Admin: 额外加 background: var(--color-accent-light) */
}
```

## Architectural Boundaries
- **NO 数据获取** — 仅导航+内容出口
- **NO `<slot>`** — 子内容通过嵌套 RouterView 渲染，不用 slots
- **NO props/emits** — 布局不接收也不发出数据
- 所有样式用 `<style scoped>` + `var(--*)` 令牌

<important if="you are adding a new layout to this layer">
## Adding a New Layout
1. 创建 `layouts/XxxLayout.vue`，按上述模板（选择侧边栏或顶部导航布局）
2. 在 `router/index.ts` 中静态导入并添加父路由组：
   ```ts
   { path: '/xxx', component: XxxLayout, children: [...] }
   ```
3. 子视图用 `() => import('@/views/XxxView.vue')` 懒加载
</important>
