<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { ref, onMounted } from 'vue'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { API_HOME_HIGHLIGHTS, API_PUBLIC_ANNOUNCEMENTS } from '@/api'
import type { HomeHighlightsResponse, HomeRoute } from '@/types/home'
import type { PublicAnnouncementListResponse } from '@/types/announcement'

const heroVisible = ref(false)

/** 首页卡片归一化结构（活数据与静态兜底共用渲染） */
interface SpotCard {
  name: string
  tag: string
  description: string
  icon: string
  meta: string
}

interface RouteCard {
  name: string
  description: string
  meta: string
  reason: string
}

/** 路线序数：卷轴式中文数字（结构即信息，替代通用 01/02 编号） */
const ROUTE_NUMERALS = ['一', '二', '三', '四', '五']

/** 静态兜底数据：首页活数据接口不可用时的热门景点（原写死卡片内容） */
const FALLBACK_SPOTS: SpotCard[] = [
  {
    name: '黄鹤楼',
    tag: '历史古迹',
    description: '天下绝景，千年诗楼，登高望远尽览江城风貌',
    icon: '🏯',
    meta: '武昌区 · 约 2 小时',
  },
  {
    name: '东湖绿道',
    tag: '自然风光',
    description: '城中湖泊，四季皆景，骑行漫步的绝佳去处',
    icon: '🏞',
    meta: '东湖风景区 · 约 3 小时',
  },
  {
    name: '省博物馆',
    tag: '文化博览',
    description: '曾侯乙编钟，千年楚文化的璀璨见证',
    icon: '🏛',
    meta: '武昌区 · 约 3 小时',
  },
]

/** 静态兜底数据：首页活数据接口不可用时的推荐路线 */
const FALLBACK_ROUTES: RouteCard[] = [
  {
    name: '经典一日游',
    description: '黄鹤楼 → 户部巷 → 东湖绿道',
    meta: '约 6 小时 · 适合初次到访',
    reason: '经典行程一次看遍江城精华',
  },
  {
    name: '文化深度游',
    description: '省博物馆 → 归元寺 → 古琴台',
    meta: '约 8 小时 · 适合文化爱好者',
    reason: '让历史爱好者不虚此行',
  },
  {
    name: '亲子欢乐行',
    description: '东湖海洋世界 → 植物园 → 磨山',
    meta: '约 5 小时 · 适合家庭出游',
    reason: '寓教于乐，孩子玩得开心',
  },
]

// ── State ──
const spots = ref<SpotCard[]>(FALLBACK_SPOTS)
const routes = ref<RouteCard[]>(FALLBACK_ROUTES)
const announcements = ref<PublicAnnouncementListResponse['items']>([])

onMounted(() => {
  // 入场动画延迟一帧以确保浏览器完成初始渲染
  requestAnimationFrame(() => {
    heroVisible.value = true
  })
  fetchHighlights()
  fetchAnnouncements()
})

/** 标签 → 图标：活数据无图标字段，按标签关键词映射 emoji，与原有卡片视觉一致 */
function spotIcon(tags: string[] | undefined): string {
  const tagText = (tags ?? []).join(' ')
  if (tagText.includes('瀑布')) return '💦'
  if (tagText.includes('湖')) return '🌊'
  if (tagText.includes('花')) return '🌸'
  if (tagText.includes('寺') || tagText.includes('宗教') || tagText.includes('古建筑')) return '🏯'
  if (tagText.includes('历史') || tagText.includes('文化') || tagText.includes('人文')) return '🏛'
  if (tagText.includes('山') || tagText.includes('峰') || tagText.includes('登山')) return '⛰'
  if (tagText.includes('森林') || tagText.includes('自然')) return '🌲'
  return '🏔'
}

/** 景点卡片元信息：所在地 · 建议游览时长（活数据字段真实展示） */
function spotMeta(spot: { location?: string; duration_minutes?: number }): string {
  const parts: string[] = []
  if (spot.location) parts.push(spot.location)
  if (spot.duration_minutes) {
    const mins = spot.duration_minutes
    parts.push(mins % 60 === 0 ? `约 ${mins / 60} 小时` : `约 ${(mins / 60).toFixed(1)} 小时`)
  }
  return parts.join(' · ')
}

/** 路线卡片元信息：约 X 小时 · 适合 Y */
function routeMeta(route: HomeRoute): string {
  const parts: string[] = []
  if (route.total_duration_minutes) {
    parts.push(`约 ${Math.round(route.total_duration_minutes / 60)} 小时`)
  }
  if (route.suitable_for?.length) {
    parts.push(`适合 ${route.suitable_for[0]}`)
  }
  return parts.join(' · ')
}

/** 首页活数据：热门景点 / 推荐路线（失败保持静态兜底，页面不崩） */
async function fetchHighlights() {
  try {
    const data = await fetchWithCodeEnvelope<HomeHighlightsResponse>(API_HOME_HIGHLIGHTS)
    if (data.spots?.length) {
      spots.value = data.spots.slice(0, 3).map((s) => ({
        name: s.name,
        tag: s.tags?.[0] ?? '景区景点',
        description: s.description,
        icon: spotIcon(s.tags),
        meta: spotMeta(s),
      }))
    }
    if (data.routes?.length) {
      routes.value = data.routes.slice(0, 3).map((r) => ({
        name: r.name,
        description: r.description,
        meta: routeMeta(r),
        reason: r.recommend_reason,
      }))
    }
  } catch {
    // 接口失败：静默降级为静态兜底数据
  }
}

/** 首页公告：仅已发布、置顶优先（后端排序）；失败/空列表时隐藏公告区 */
async function fetchAnnouncements() {
  try {
    const data = await fetchWithCodeEnvelope<PublicAnnouncementListResponse>(
      `${API_PUBLIC_ANNOUNCEMENTS}?limit=10`,
    )
    announcements.value = data.items ?? []
  } catch {
    announcements.value = []
  }
}

function formatDate(iso: string) {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

// 「浏览推荐」按钮：页内平滑滚动到下方「推荐路线」区块
// （原 /visitor/recommend 为不存在的死路由，改为 scrollIntoView 避免跳转无响应）
function scrollToRecommendedRoutes() {
  const el = document.getElementById('recommended-routes')
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}
</script>

<template>
  <div class="home">
    <!-- Hero Section：论点 = 数字人导览；右侧墨色山峦剪影 + 朱砂印章落款（签名元素） -->
    <section class="hero" :class="{ 'hero--visible': heroVisible }">
      <!-- 水墨山峦剪影（三层：远雾 → 青玉淡 → 墨青；支持元素，aria-hidden） -->
      <svg
        class="hero-mountains"
        viewBox="0 0 640 360"
        preserveAspectRatio="xMidYMax slice"
        aria-hidden="true"
      >
        <circle cx="486" cy="116" r="40" fill="var(--surface)" opacity="0.9" />
        <path
          d="M0 218 L118 142 L206 208 L296 118 L420 216 L516 158 L640 226 L640 360 L0 360 Z"
          fill="var(--mist)"
        />
        <path
          d="M0 278 L138 202 L258 268 L382 188 L502 276 L640 238 L640 360 L0 360 Z"
          fill="var(--jade-soft)"
          opacity="0.85"
        />
        <path
          d="M0 330 L176 262 L318 318 L468 252 L640 310 L640 360 L0 360 Z"
          fill="var(--jade)"
          opacity="0.4"
        />
      </svg>

      <div class="hero-content">
        <p class="hero-label"><span class="hero-label-line" />AI 智慧景区服务</p>
        <h1 class="hero-title">
          探索景区故事<br />
          <span class="hero-accent">AI 数字人</span>为您导览
        </h1>
        <p class="hero-subtitle">与数字人对话，发现每个景点背后的历史与文化</p>
        <blockquote class="hero-quote">「登山则情满于山，观海则意溢于海」</blockquote>
        <div class="hero-actions">
          <RouterLink to="/visitor/chat" class="btn-primary">
            开始对话
            <span class="btn-arrow">→</span>
          </RouterLink>
          <button type="button" class="btn-secondary" @click="scrollToRecommendedRoutes">
            浏览推荐
          </button>
        </div>
        <!-- 管理后台入口（批次9：管理员账号密码登录，替代 X-Admin-Token 输入形式） -->
        <p class="hero-admin-entry">
          景区管理者？
          <RouterLink to="/admin/login" class="admin-entry-link">进入管理后台 →</RouterLink>
        </p>
      </div>
    </section>

    <!-- Announcements（公告区：仅在有已发布公告时显示） -->
    <section v-if="announcements.length > 0" class="announcements section">
      <div class="section-header">
        <h2 class="section-title"><span class="section-seal" aria-hidden="true" />景区公告</h2>
        <p class="section-desc">园区动态与温馨提示</p>
      </div>
      <div class="announcement-list">
        <article
          v-for="item in announcements"
          :key="item.id"
          class="announcement-card"
          :class="{ 'announcement-card--pinned': item.pinned }"
        >
          <div class="announcement-card-title">
            <span v-if="item.pinned" class="announcement-pin">置顶</span>
            <h3>{{ item.title }}</h3>
            <time class="announcement-date">{{ formatDate(item.created_at) }}</time>
          </div>
          <p class="announcement-content">{{ item.content }}</p>
        </article>
      </div>
    </section>

    <!-- Featured Spots -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title"><span class="section-seal" aria-hidden="true" />热门景点</h2>
        <p class="section-desc">探索最受游客喜爱的目的地</p>
      </div>
      <div class="spots-grid">
        <div v-for="spot in spots" :key="spot.name" class="spot-card">
          <div class="spot-card-img">
            <div class="img-placeholder">
              <span class="img-placeholder-icon">{{ spot.icon }}</span>
            </div>
          </div>
          <div class="spot-card-body">
            <span class="spot-card-tag">{{ spot.tag }}</span>
            <h3>{{ spot.name }}</h3>
            <p>{{ spot.description }}</p>
            <span class="spot-meta">{{ spot.meta }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- Recommended Routes -->
    <section id="recommended-routes" class="section">
      <div class="section-header">
        <h2 class="section-title"><span class="section-seal" aria-hidden="true" />推荐路线</h2>
        <p class="section-desc">精心规划的游览体验</p>
      </div>
      <div class="routes-list">
        <div v-for="(route, index) in routes" :key="route.name" class="route-card">
          <div class="route-index" aria-hidden="true">{{ ROUTE_NUMERALS[index] ?? index + 1 }}</div>
          <div class="route-content">
            <div class="route-title-row">
              <h3>{{ route.name }}</h3>
              <span class="route-meta">{{ route.meta }}</span>
            </div>
            <p class="route-desc">{{ route.description }}</p>
            <p v-if="route.reason" class="route-reason">「{{ route.reason }}」</p>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* ── Hero ── */
.hero {
  position: relative;
  padding: var(--space-3xl) var(--space-2xl);
  min-height: 85vh;
  display: flex;
  align-items: center;
  overflow: hidden;
}

/* 水墨山峦剪影：右下角三层山脊（远雾 → 青玉淡 → 墨青），hero 入场时淡入 */
.hero-mountains {
  position: absolute;
  right: 0;
  bottom: 0;
  width: min(52vw, 640px);
  height: 100%;
  max-height: 560px;
  pointer-events: none;
  opacity: 0;
  transition: opacity var(--duration-slow) var(--ease-out) 150ms;
}

.hero--visible .hero-mountains {
  opacity: 1;
}

.hero-content {
  position: relative;
  z-index: 1;
  max-width: 800px;
}

/* 入场序列：小标 → 题字 → 副文 → 题跋 → 动作，逐段浮现（唯一动效编排） */
.hero-content > * {
  opacity: 0;
  transform: translateY(16px);
  transition:
    opacity 0.7s var(--ease-out),
    transform 0.7s var(--ease-out);
}

.hero--visible .hero-content > * {
  opacity: 1;
  transform: translateY(0);
}

.hero--visible .hero-label {
  transition-delay: 0ms;
}
.hero--visible .hero-title {
  transition-delay: 80ms;
}
.hero--visible .hero-subtitle {
  transition-delay: 160ms;
}
.hero--visible .hero-quote {
  transition-delay: 240ms;
}
.hero--visible .hero-actions {
  transition-delay: 320ms;
}

.hero-label {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--jade);
  letter-spacing: 0.18em;
  text-transform: uppercase;
  margin-bottom: var(--space-xl);
}

.hero-label-line {
  display: inline-block;
  width: 24px;
  height: 1px;
  background: var(--jade);
}

.hero-title {
  font-family: var(--font-display);
  font-size: var(--text-hero);
  font-weight: 700;
  letter-spacing: 0.02em;
  line-height: 1.15;
  color: var(--ink);
  margin-bottom: var(--space-lg);
}

.hero-accent {
  color: var(--jade);
}

.hero-subtitle {
  font-size: var(--text-subtitle);
  color: var(--slate);
  line-height: 1.6;
  max-width: 28em;
  margin-bottom: var(--space-xl);
}

/* 题跋引用：朱砂竖签，卷轴旁注气质 */
.hero-quote {
  font-family: var(--font-display);
  font-size: var(--text-title);
  color: var(--slate);
  margin: 0 0 var(--space-xl);
  padding-left: var(--space-md);
  border-left: 2px solid var(--cinnabar);
}

.hero-actions {
  display: flex;
  gap: var(--space-md);
  flex-wrap: wrap;
}

/* 管理后台入口（批次9：管理员账号密码登录） */
.hero-admin-entry {
  margin: var(--space-lg) 0 0;
  font-size: var(--text-small);
  color: var(--slate);
}

.admin-entry-link {
  color: var(--jade);
  text-decoration: none;
  font-weight: 500;
}

.admin-entry-link:hover {
  text-decoration: underline;
}

/* ── 按钮（青玉主 / 描边次，全站通用形态） ── */
.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  padding: var(--space-md) var(--space-xl);
  background: var(--jade);
  color: var(--surface);
  font-size: var(--text-body);
  font-weight: 500;
  text-decoration: none;
  border-radius: var(--radius-sm);
  transition:
    background var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out);
}

.btn-primary:hover {
  background: var(--jade-strong);
  text-decoration: none;
}

.btn-arrow {
  transition: transform var(--duration-fast) var(--ease-out);
}

.btn-primary:hover .btn-arrow {
  transform: translateX(3px);
}

.btn-secondary {
  display: inline-flex;
  align-items: center;
  padding: var(--space-md) var(--space-xl);
  background: transparent;
  color: var(--ink);
  font-size: var(--text-body);
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  border: 1px solid var(--mist-strong);
  border-radius: var(--radius-sm);
  transition:
    border-color var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}

.btn-secondary:hover {
  border-color: var(--ink);
  text-decoration: none;
}

/* ── Section 题跋（朱砂方点为印，标题为题字） ── */
.section {
  padding: var(--space-section) var(--space-2xl);
}

.section-header {
  margin-bottom: var(--space-xl);
}

.section-title {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-family: var(--font-display);
  font-size: var(--text-display);
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--ink);
  margin: 0 0 var(--space-xs);
}

.section-seal {
  display: inline-block;
  width: 9px;
  height: 9px;
  background: var(--cinnabar);
  border-radius: 2px;
  flex-shrink: 0;
}

.section-desc {
  font-size: var(--text-body);
  color: var(--slate);
  margin: 0;
}

/* ── Announcements（题签卡片） ── */
.announcement-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.announcement-card {
  position: relative;
  padding: var(--space-lg) var(--space-xl);
  border-radius: var(--radius-md);
  background: var(--surface);
  border: 1px solid var(--mist);
  box-shadow: var(--shadow-card);
  transition:
    border-color var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out);
}

/* 青玉侧签：题签的"签" */
.announcement-card::before {
  content: '';
  position: absolute;
  left: -1px;
  top: 16px;
  bottom: 16px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--jade);
}

.announcement-card--pinned::before {
  background: var(--cinnabar);
}

.announcement-card:hover {
  border-color: var(--mist-strong);
  box-shadow: var(--shadow-pop);
}

.announcement-card-title {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  margin-bottom: var(--space-xs);
  flex-wrap: wrap;
}

.announcement-card-title h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  font-weight: 700;
  color: var(--ink);
  margin: 0;
}

/* 置顶 = 朱砂迷你印 */
.announcement-pin {
  font-size: var(--text-tiny);
  font-weight: 500;
  color: var(--surface);
  background: var(--cinnabar);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
  letter-spacing: 0.08em;
}

.announcement-date {
  margin-left: auto;
  font-size: var(--text-caption);
  color: var(--slate);
  font-family: var(--font-mono);
}

.announcement-content {
  margin: 0;
  font-size: var(--text-small);
  color: var(--slate);
  line-height: 1.7;
  white-space: pre-line;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── Spots Grid（等宽意象卡） ── */
.spots-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-lg);
}

.spot-card {
  border: 1px solid var(--mist);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--surface);
  transition: border-color var(--duration-fast) var(--ease-out);
}

.spot-card:hover {
  border-color: var(--mist-strong);
}

.spot-card-img {
  aspect-ratio: 16/8;
  background: var(--overlay);
  overflow: hidden;
}

.img-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--jade-soft);
}

.img-placeholder-icon {
  font-size: 2.75rem;
  opacity: 0.75;
}

.spot-card-body {
  padding: var(--space-md) var(--space-lg) var(--space-lg);
}

.spot-card-tag {
  display: inline-block;
  font-size: var(--text-caption);
  color: var(--jade);
  font-weight: 500;
  margin-bottom: var(--space-xs);
}

.spot-card-body h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  font-weight: 700;
  margin: 0 0 var(--space-xs);
  color: var(--ink);
}

.spot-card-body p {
  font-size: var(--text-small);
  color: var(--slate);
  margin: 0 0 var(--space-sm);
  line-height: 1.6;
}

/* 数据行：所在地 · 建议时长（mono，数据即排版） */
.spot-meta {
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--slate);
}

/* ── Routes List（卷轴式） ── */
.routes-list {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--mist);
}

.route-card {
  display: flex;
  gap: var(--space-xl);
  padding: var(--space-lg) 0;
  border-bottom: 1px solid var(--mist);
  transition: background var(--duration-fast) var(--ease-out);
}

.route-card:hover {
  background: var(--overlay);
}

.route-index {
  font-family: var(--font-display);
  font-size: var(--text-title);
  font-weight: 700;
  color: var(--jade);
  min-width: 2.5em;
  text-align: right;
  flex-shrink: 0;
}

.route-content {
  flex: 1;
  min-width: 0;
}

.route-title-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-md);
  flex-wrap: wrap;
}

.route-content h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  font-weight: 700;
  margin: 0;
  color: var(--ink);
}

.route-meta {
  font-family: var(--font-mono);
  font-size: var(--text-caption);
  color: var(--slate);
}

.route-desc {
  font-size: var(--text-body);
  color: var(--slate);
  margin: var(--space-xs) 0 0;
}

/* 推荐理由：题跋式引注 */
.route-reason {
  font-family: var(--font-display);
  font-size: var(--text-small);
  color: var(--slate);
  margin: var(--space-xs) 0 0;
}

/* ── Responsive ── */
@media (max-width: 1024px) {
  .spots-grid {
    grid-template-columns: 1fr 1fr;
  }
}

@media (max-width: 720px) {
  .hero {
    padding: var(--space-xl) var(--space-lg) 160px;
    min-height: auto;
  }

  /* 山峦收缩为底部细条（弱化，不挡内容） */
  .hero-mountains {
    width: 100%;
    height: 130px;
    max-height: none;
    opacity: 0.55 !important;
  }

  .hero-title {
    font-size: clamp(1.75rem, 8vw, 3rem);
  }

  .spots-grid {
    grid-template-columns: 1fr;
  }

  .section {
    padding: var(--space-section) var(--space-lg);
  }

  .route-card {
    gap: var(--space-md);
  }

  .route-index {
    text-align: left;
    min-width: 1.5em;
  }
}
</style>
