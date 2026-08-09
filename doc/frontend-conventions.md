# 前端工程化与组件规范（digitalHumanVue）

批次8 C8：TS 严格模式 + ESLint + 统一组件规范。本文档为**前端开发权威约定**，与 `digitalHumanVue/src/CLAUDE.md`（各子层指南：views/layouts）互补——本文档讲**横切约定**（命名/组织/样式/状态/API 封装/工程基线），子层 CLAUDE.md 讲**各层内部模式**。

## 工程基线（C8 现状核对结论）

| 项 | 状态 | 说明 |
|----|------|------|
| TS 严格模式 | ✅ 已开启 | `tsconfig.app.json` 继承 `@vue/tsconfig/tsconfig.dom.json`（`strict: true`），另开 `noUncheckedIndexedAccess` |
| type-check 入 build | ✅ 已含 | `npm run build` = `run-p type-check "build-only"`（`vue-tsc --build` + `vite build` 并行） |
| ESLint | ✅ 已配置 | `eslint.config.ts`：`pluginVue flat/essential` + `vueTsConfigs.recommended` + oxlint（`.oxlintrc.json`）+ prettier（`eslint-config-prettier`） |
| lint 脚本 | ✅ 已入 | `npm run lint` = `lint:eslint`（`eslint . --fix --cache`）+ `lint:oxlint`（`oxlint . --fix`）；另 `npm run format`（prettier） |
| CI 门禁 | ✅ 批次8 补齐 | frontend job 步骤：`npm ci` → `npm run lint` → `npm run build`；E2E 冒烟独立 job |

技术栈（锁定，不引入新重依赖）：Vue 3.5 + TypeScript 6（strict）+ Vite 8 + Pinia 3 + vue-router 5 + ECharts（vue-echarts）。
**不使用**：axios（一律原生 fetch）、Sass/Tailwind（一律 scoped css + CSS 变量）、第三方 mock 库（前端 E2E 用 Playwright 内置 `page.route` mock）。

## 命名规范

- 文件：视图 `XxxView.vue` / 布局 `XxxLayout.vue` / Store `useXxxStore`（`stores/xxx.ts`）/ Composable `useXxx.ts`（`composables/`）/ API 封装 `xxx.ts`（`api/`，小写驼峰）/ 类型 `xxx.ts`（`types/`）
- 组件内：`<script setup lang="ts">`，响应式状态 `ref`/`computed` 直接命名（`data`、`isLoading`、`error`），函数 `fetchXxx`/`handleXxx` 驼峰
- CSS 类：`kebab-case`；路由名：`role-page`（如 `admin-dashboard`）；API 响应字段：`snake_case`（对齐后端 FastAPI）
- 路径别名：`@/` → `./src/`（见 tsconfig paths），禁止相对路径深链

## 组件组织（目录职责）

```
src/
  api/          # 端点常量（index.ts 集中式）+ 按域封装（auth/memory/usage/adminAgent）
  stores/       # Pinia store（auth.ts 登录态、visitor.ts 游客）
  types/        # 与后端契约对齐的 TS 接口（dashboard/home/knowledge/memory/usage/auth/...）
  utils/        # 纯函数工具（fetchWithCodeEnvelope.ts、uuid.ts）
  composables/  # 组合式函数（useApi 三态、useECharts 图表注册）
  components/   # 通用展示组件（EmptyStateGuide.vue 空态）
  layouts/      # 布局壳（AdminLayout/VisitorLayout），父路由 component，消费 auth store
  views/        # 页面组件（含 admin/ 子目录），路由懒加载，自行管理数据获取
  router/       # 嵌套布局路由；认证页 /login /register 为顶层路由例外
```

- **页面组件（views/）** 管理数据获取与状态；**通用组件（components/）** 只做展示、props 驱动；**布局（layouts/）** 不直接 fetch，只消费 auth store 渲染导航/登录态
- 新增领域能力时：`types/` 定义契约 → `api/` 封装请求 → view/store 消费（见文末「新增 API」速查）

## 样式方案（CSS token 体系 + scoped）

```
styles/design-tokens.css — 设计体系 token 权威源（--ink/--paper/--jade/--cinnabar/--mist/--slate/--surface + 间距/圆角/阴影/动效/字阶；token 决策见 doc/redesign-v1.md）
styles/variables.css     — 旧 --color-* 名兼容别名层（映射到 design-tokens.css；阶段 2 已全部迁移到新名、无存量依赖，按船长决策保留不删）
styles/base.css          — CSS reset + 排版基准（含全局 :focus-visible 描边 + prefers-reduced-motion 降级）
assets/main.css          — 入口：依次 @import design-tokens.css → variables.css → base.css；#app { min-height: 100vh }
```

- 所有组件 `<style scoped>` + `var(--*)` 令牌，**禁止** Sass/Tailwind/全局样式污染
- 跨组件样式协调一律走 CSS 变量（权威源 design-tokens.css；旧 `--color-*` 名经 variables.css 别名映射兜底），不写无 scoped 的全局选择器（重置类除外）
- 类名 `kebab-case`；需要覆盖子组件样式时用 `:deep()` 并在注释说明原因（少用）

## 状态管理（Pinia）

- 统一 **Composition API（setup store）** 语法：`export const useXxxStore = defineStore('xxx', () => { ... })`
- 持久化：`localStorage` 直接读写（无 persist 插件）；键名沿用 snake_case（`auth_token`、`auth_user`、`visitor_id`、`admin_api_token`）
- 参考实现：
  - `stores/visitor.ts` — `generateUUID()`（非安全上下文降级 Math.random + Date.now）生成 visitorId
  - `stores/auth.ts` — token/JWT 持久化、`enterGuestMode()` 游客兜底、`restoreSession()` 启动校验 `/auth/me`（401 清登录态、网络失败保留本地缓存）、`authHeaders()` 注入请求头（未登录返回空对象向后兼容）
- 只把**跨组件共享**的状态放 store；页面局部状态留在组件内

## API 封装模式（fetchWithCodeEnvelope）

统一走 `src/utils/fetchWithCodeEnvelope.ts`（带双向错误检查的 fetch 封装）：

1. `response.ok` — 覆盖 nginx/Vite 代理等中间层非 JSON 错误
2. `body.code !== 200` — 覆盖后端业务层 ErrorResponse

- 成功返回解析后的 JSON body（泛型 `<T>` 透出类型）；失败抛 `FetchError`（含 `httpStatus`/`appCode`/`cause`）
- 错误消息提取：FastAPI `{ detail }` 三形态兼容——字符串（401 "用户名或密码错误"）/ 422 校验数组（拼接 `loc: msg`）/ 不可解析回退通用消息
- 默认 15s 超时（`AbortController`，`timeoutMs` 可配，0 禁用）
- **端点常量集中**：`api/index.ts` 的 `API_*` 常量，组件内禁止硬编码 URL
- **按域封装**：`api/auth.ts` / `api/memory.ts` / `api/usage.ts` 等导出类型化函数（`fetchWithCodeEnvelope<XxxResponse>(API_X, {...})`），调用方不直接碰 fetch
- 鉴权头：登录用户 `Authorization: Bearer <token>`（authHeaders()）；管理端 `X-Admin-Token` 读 `localStorage.admin_api_token`
- **SSE 特例**（`api/adminAgent.ts`）：用原生 fetch ReadableStream + TextDecoder 增量解码，统一剔 `\r` 后按 `\n\n` 分帧、按行取 `data:` 前缀解析 JSON；非流式走 fetchWithCodeEnvelope 备用
- **CSV/二进制特例**（`api/usage.ts`）：原生 fetch 取 Blob + 隐藏 `<a>` 触发下载，不走 JSON 封装

## 数据获取三态模式

- 简单 GET：`composables/useApi.ts`（`{ data, isLoading, error, execute }`）
- 视图内手动管理：4-ref 状态机 `data / isLoading / error / hasData`，模板按 error → empty → loading → content 渲染（见 `views/CLAUDE.md` 模板）；空态复用 `components/EmptyStateGuide.vue`
- 接口失败必须优雅降级：首页卡片降级静态兜底、无公告隐藏区块等既有模式，禁止抛裸错误页

## Lint / 格式化 / CI

- `npm run lint`（本地 + CI）：eslint（vue essential + TS recommended，`--fix` 自动修）+ oxlint（`.oxlintrc.json`，correctness=error）
- `npm run format`：prettier（`--experimental-cli src/`）；`.prettierrc.json` 与 `.editorconfig` 配套
- `npm run build`：type-check（vue-tsc）+ vite build，TS 类型错误即失败
- **新增依赖约束**：ESLint/工具类插件只进 devDependencies；不引入新的运行时重依赖（理由见「技术栈」锁定清单）

## 新增 API 端点速查

1. `api/index.ts` 添加 `export const API_MY_FEATURE = '/api/v1/my/feature'`
2. `types/` 创建接口（字段 snake_case 对齐后端契约）
3. 按域在 `api/xxx.ts` 封装 `fetchWithCodeEnvelope` 调用（或 SSE 特例）
4. view/store 中消费，三态渲染 + 空态兜底

## 环境变量约定

- 仅 `VITE_` 前缀变量在客户端可用（`digitalHumanVue/.env`），类型声明在 `env.d.ts`
- 凭证只从 `.env` 读取，绝不写入代码库；仓库内 `.env.example` 仅含占位符
