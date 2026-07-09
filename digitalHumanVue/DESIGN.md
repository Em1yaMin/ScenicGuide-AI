# 景区导览 AI 数字人 — 前端视觉设计文档

> 本项目为第十五届中国软件杯 A5 赛题「景区导览服务 AI 数字人」的 Vue3 前端。
> 本文档固化前端视觉方向，避免开发过程中滑向默认 SaaS 模板审美。

---

## 1. 设计定位

- **气质（Tone）**: 编辑感 / Editorial
  - 像文化杂志内页，而非仪表盘。
  - 字体与排版承担叙事，减少装饰性元素。
  - 适合景区的历史、人文与自然故事表达。

- **差异化（Differentiation）**: 字体即视觉
  - 用超大、有编排感的标题替代传统 hero 插图。
  - 不追求猎奇，而是通过字体层级与排版精度消除「AI 味」与廉价感。

---

## 2. 色彩系统

### 2.1 基础色板

| Token | 色值 | 用途 |
|-------|------|------|
| `--color-paper` | `#F7F5F0` | 页面主背景，模拟纸张/宣纸质感 |
| `--color-ink` | `#1A1A1A` | 主文字，接近墨黑 |
| `--color-ink-secondary` | `#5C5C5C` | 次级文字、说明文字 |
| `--color-accent` | `#C45C26` | 强调色（示例：古建筑红墙/赭石），需按景区主色调调整 |
| `--color-accent-light` | `#F4E9E0` | 强调色浅色背景、标签、hover 底色 |
| `--color-border` | `#E5E2DB` | 分割线、边框 |
| `--color-overlay` | `rgba(26, 26, 26, 0.04)` | 卡片/模块微弱底色 |

### 2.2 使用原则

- 底色以 `--color-paper` 为主，营造纸张/文化感。
- 文字以 `--color-ink` 为主，保证高对比度可读性。
- **仅使用一个强调色** `--color-accent`，所有按钮、标签、高亮统一使用它。
- 强调色应根据实际景区主色调调整（例如山水景区可改用 `#2D6E6E` 青绿色，森林景区可改用 `#4A6B3E` 墨绿色）。
- 禁止：紫蓝渐变、SaaS 蓝（#3B82F6）、马卡龙均等色盘。

---

## 3. 字体系统

### 3.1 字体选择

| 层级 | 中文字体 | 回退栈 | 英文字体 | 回退栈 |
|------|---------|--------|---------|--------|
| 展示标题 | 思源宋体 / Noto Serif SC | `"Noto Serif SC", "Source Han Serif SC", "STSong", serif` | IBM Plex Serif | `"IBM Plex Serif", "Newsreader", Georgia, serif` |
| 正文/界面 | 思源黑体 / Noto Sans SC | `"Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif` | Inter（仅用于数字/英文正文） | `"Inter", system-ui, sans-serif` |
| 代码/数据 | — | — | JetBrains Mono / Fira Code | `"JetBrains Mono", "Fira Code", monospace` |

### 3.2 字体加载

- 优先通过 Google Fonts / 阿里 CDN 加载：
  - `https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;600;700&family=Noto+Sans+SC:wght@400;500;700&family=IBM+Plex+Serif:wght@400;600;700&display=swap`
- 如加载失败，回退到系统字体，保证可读性。

### 3.3 字号比例

| Token | 尺寸 | 用途 |
|-------|------|------|
| `--text-hero` | `clamp(2.5rem, 6vw, 5rem)` | 首页大标题 |
| `--text-display` | `clamp(2rem, 4vw, 3.5rem)` | 页面主标题 |
| `--text-title` | `1.75rem` | 区块标题 |
| `--text-subtitle` | `1.25rem` | 小标题、卡片标题 |
| `--text-body` | `1rem` | 正文 |
| `--text-small` | `0.875rem` | 辅助说明、标签 |
| `--text-caption` | `0.75rem` | 时间戳、元信息 |

### 3.4 排版原则

- 标题使用思源宋体/IBM Plex Serif，正文使用思源黑体。
- 行高：标题 `1.2`，正文 `1.7`。
- 中文段落最佳宽度：每行 25–35 个汉字（约 `max-width: 36em`）。
- 避免大段文字居中，标题可左对齐或故意偏移。

---

## 4. 间距与布局

### 4.1 间距令牌

| Token | 值 | 用途 |
|-------|-----|------|
| `--space-xs` | `0.5rem` | 紧凑内边距、行间距 |
| `--space-sm` | `0.75rem` | 小组件间距 |
| `--space-md` | `1rem` | 默认间距 |
| `--space-lg` | `1.5rem` | 卡片内边距 |
| `--space-xl` | `2.5rem` | 区块内部间距 |
| `--space-2xl` | `4rem` | 大区块间距 |
| `--space-3xl` | `6rem` | 页面级上下留白 |

### 4.2 布局原则

- **宽松留白 + 非对称**：避免所有区块都 `max-w-7xl mx-auto` 居中堆叠。
- 首页 hero 区可让标题压得很低，右侧或下方留白。
- 图片可破格出血（bleed）到容器边缘。
- 管理后台可适度收紧，但仍保持清晰层级和留白。

---

## 5. 动效

### 5.1 动效方向

- **克制微动效**： felt, not noticed。
- 一次编排好的入场胜过十个随机 hover 效果。

### 5.2 推荐实现

- 使用 CSS transitions + keyframes，优先不依赖 JS 动画库。
- 如需复杂编排，再引入 GSAP 或 Framer Motion（Vue 生态可用 `@vueuse/motion`）。

### 5.3 动效令牌

| Token | 值 | 用途 |
|-------|-----|------|
| `--duration-fast` | `150ms` | hover、focus 反馈 |
| `--duration-normal` | `300ms` | 展开、切换 |
| `--duration-slow` | `600ms` | 页面进入、区块 reveal |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` | 出场/进入 |
| `--ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` | 过渡 |

### 5.4 具体场景

- **页面进入**：标题从下方 `translateY(24px)` + `opacity: 0` 淡入，持续 600ms， stagger 100ms。
- **卡片 hover**：轻微上浮 `translateY(-4px)` + 阴影加深，持续 200ms。
- **按钮 hover**：背景色从 `--color-accent` 过渡到加深 10% 的色值。
- **数字人对话区**：数字人出现/思考时可用 subtle pulse，避免过度弹跳。

---

## 6. 组件风格

### 6.1 按钮

- **主按钮**：`--color-accent` 背景 + `--color-paper` 文字，无圆角或微圆角（`border-radius: 2px`）。
- **次按钮**：透明背景 + `--color-ink` 边框 + `--color-ink` 文字。
- **文字按钮**：无背景，hover 时加下划线或颜色变化。
- 禁止：大圆角 pill 按钮、强阴影、渐变背景。

### 6.2 卡片

- 背景：`--color-overlay` 或 `--color-paper`。
- 边框：`1px solid --color-border`。
- 圆角：`4px` 或 `0`（编辑感更强）。
- 阴影：仅在 hover 时使用极淡阴影，日常不使用 `shadow-md`。

### 6.3 表单/输入框

- 边框：`1px solid --color-border`，focus 时变为 `--color-accent`。
- 背景：`--color-paper`。
- 圆角：`2px`。
- placeholder 颜色：`--color-ink-secondary`。

### 6.4 导航

- 游客端：极简顶部导航，logo + 2–3 个链接 + 进入对话入口。
- 管理后台：侧边栏导航，图标 + 文字，当前项用 `--color-accent-light` 背景或左侧 accent 边条标识。

---

## 7. 背景与纹理

- 默认使用纯色 `--color-paper`。
- 可在关键页面叠加极淡噪点纹理（noise texture），增加纸张/宣纸质感。
- 允许在 hero 区使用一张高质量景区摄影作为大背景，但需叠加半透明 `--color-paper` 渐变遮罩以保证文字可读性。
- 禁止：无意义的紫蓝渐变网格、花哨几何图案。

---

## 8. 页面结构建议

### 8.1 游客端（游客交互端）

1. **首页 / 导览入口**
   - 大标题 + 景区简介 + CTA（开始导览）
   - 推荐路线卡片（非对称布局）
   - 景点速览

2. **数字人对话页**
   - 左侧/下方：light4ai 数字人 iframe
   - 右侧/上方：当前推荐景点、快捷问题
   - 保持视觉简洁，突出数字人

3. **景点详情页**
   - 大标题 + 图片 + 文字介绍
   - 相关推荐路线

4. **个性化推荐页**
   - 兴趣标签选择
   - 推荐路线结果展示

### 8.2 管理后台

1. **数据大屏**
   - 核心指标卡片
   - 访问量趋势图
   - 满意度分布
   - 热门问题 TOP

2. **游客感受度报告**
   - 情感分析概览
   - 关注点/投诉点列表
   - 导出功能

3. **景点维度分析**（如实现）
   - 各景点问答分布
   - 满意度按景点分析

---

## 9. 反廉价清单（NEVER Generate）

- **默认字体当展示字体**：系统黑体/微软雅黑/苹方直接当标题；英文避免 Inter、Roboto、Arial 作为展示字体。
- **廉价配色**：白底紫蓝渐变、通用 SaaS 蓝（#3B82F6 家族）、马卡龙均等色盘。
- **可预测布局**：居中卡片堆、hero + 三列、千篇一律的导航栏、每个 section 都包 `max-w-7xl mx-auto`。
- **流水线组件**：千篇一律的 `rounded-xl shadow-md` 卡片、通用 ghost 按钮。
- **散装动效**：每个滚动元素都 fade-in、统一弹跳缓动、没有编排的悬浮效果。
- **虚假交互表面**：任何看起来像可点击的元素都必须是真实 `<button>` 或 `<a>`。Vue 中 must use `<Link>` 或 `@click` handler。视觉 affordance 承诺了行为，要么兑现，要么去掉 affordance。

---

## 10. 快速启动检查项

在新增页面/组件前，确认：

- [ ] 是否使用了 `--color-paper` / `--color-ink` / `--color-accent` 三色系统？
- [ ] 标题是否使用了思源宋体/IBM Plex Serif，正文是否使用了思源黑体？
- [ ] 是否避免了 `rounded-xl shadow-md` 的默认卡片样式？
- [ ] 动效是否只有一个核心编排，而非随机 hover 堆砌？
- [ ] 布局是否至少有一处非对称或破格处理？

---

## 11. 相关文件

- `src/styles/variables.css` — 建议存放 CSS 自定义属性（颜色、字体、间距、动效）。
- `src/styles/base.css` — 全局基础样式、字体引入。
- `src/App.vue` — 全局布局、字体加载。

---

*最后更新：2026-07-09*
