<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { ref, onMounted } from 'vue'
import { fetchWithCodeEnvelope } from '@/utils/fetchWithCodeEnvelope'
import { API_HOME_HIGHLIGHTS, API_PUBLIC_ANNOUNCEMENTS } from '@/api'
import type { HomeHighlightsResponse, HomeRoute } from '@/types/home'
import type { PublicAnnouncementListResponse } from '@/types/announcement'

const heroVisible = ref(false)

/** 首页卡片归一化结构（活数据与静态兜底共用渲染，保持原有卡片样式） */
interface SpotCard {
  name: string
  tag: string
  description: string
  icon: string
}

interface RouteCard {
  name: string
  description: string
  meta: string
}

/** 静态兜底数据：首页活数据接口不可用时的热门景点（原写死卡片内容） */
const FALLBACK_SPOTS: SpotCard[] = [
  {
    name: '黄鹤楼',
    tag: '历史古迹',
    description: '天下绝景，千年诗楼，登高望远尽览江城风貌',
    icon: '🏯',
  },
  {
    name: '东湖绿道',
    tag: '自然风光',
    description: '城中湖泊，四季皆景，骑行漫步的绝佳去处',
    icon: '🏞',
  },
  {
    name: '省博物馆',
    tag: '文化博览',
    description: '曾侯乙编钟，千年楚文化的璀璨见证',
    icon: '🏛',
  },
]

/** 静态兜底数据：首页活数据接口不可用时的推荐路线 */
const FALLBACK_ROUTES: RouteCard[] = [
  {
    name: '经典一日游',
    description: '黄鹤楼 → 户部巷 → 东湖绿道',
    meta: '约 6 小时 · 适合初次到访',
  },
  {
    name: '文化深度游',
    description: '省博物馆 → 归元寺 → 古琴台',
    meta: '约 8 小时 · 适合文化爱好者',
  },
  {
    name: '亲子欢乐行',
    description: '东湖海洋世界 → 植物园 → 磨山',
    meta: '约 5 小时 · 适合家庭出游',
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
      }))
    }
    if (data.routes?.length) {
      routes.value = data.routes.slice(0, 3).map((r) => ({
        name: r.name,
        description: r.description,
        meta: routeMeta(r),
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
    <!-- Hero Section -->
    <section class="hero" :class="{ 'hero--visible': heroVisible }">
      <div class="hero-noise" />
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
      </div>
    </section>

    <!-- Announcements（公告区：仅在有已发布公告时显示） -->
    <section v-if="announcements.length > 0" class="announcements section">
      <div class="announcements-decor" aria-hidden="true" />
      <div class="section-header">
        <h2 class="section-title">景区公告</h2>
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
            <span v-if="item.pinned" class="announcement-pin">📌 置顶</span>
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
        <h2 class="section-title">热门景点</h2>
        <p class="section-desc">探索最受游客喜爱的目的地</p>
      </div>
      <div class="spots-grid">
        <div
          v-for="(spot, index) in spots"
          :key="spot.name"
          class="spot-card"
          :class="{ 'spot-card--featured': index === 0 }"
        >
          <div class="spot-card-img">
            <div class="img-placeholder">
              <span class="img-placeholder-icon">{{ spot.icon }}</span>
            </div>
          </div>
          <div class="spot-card-body">
            <span class="spot-card-tag">{{ spot.tag }}</span>
            <h3>{{ spot.name }}</h3>
            <p>{{ spot.description }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- Recommended Routes -->
    <section id="recommended-routes" class="section">
      <div class="section-header">
        <h2 class="section-title">推荐路线</h2>
        <p class="section-desc">精心规划的游览体验</p>
      </div>
      <div class="routes-list">
        <div v-for="(route, index) in routes" :key="route.name" class="route-card">
          <div class="route-index">{{ String(index + 1).padStart(2, '0') }}</div>
          <div class="route-content">
            <h3>{{ route.name }}</h3>
            <p>{{ route.description }}</p>
            <span class="route-meta">{{ route.meta }}</span>
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

.hero-noise {
  position: absolute;
  inset: 0;
  opacity: 0.03;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  pointer-events: none;
}

.hero-content {
  position: relative;
  max-width: 800px;
  opacity: 0;
  transform: translateY(24px);
  transition:
    opacity 0.8s var(--ease-out),
    transform 0.8s var(--ease-out);
}

.hero--visible .hero-content {
  opacity: 1;
  transform: translateY(0);
}

.hero-label {
  display: flex;
  align-items: center;
  gap: var(--space-sm);
  font-family: var(--font-mono);
  font-size: var(--text-small);
  color: var(--color-accent);
  letter-spacing: 0.15em;
  text-transform: uppercase;
  margin-bottom: var(--space-xl);
}

.hero-label-line {
  display: inline-block;
  width: 24px;
  height: 1px;
  background: var(--color-accent);
}

.hero-title {
  font-family: var(--font-display);
  font-size: var(--text-hero);
  line-height: 1.15;
  color: var(--color-ink);
  margin-bottom: var(--space-lg);
  font-weight: 700;
}

.hero-accent {
  color: var(--color-accent);
}

.hero-subtitle {
  font-size: var(--text-subtitle);
  color: var(--color-ink-secondary);
  line-height: 1.6;
  max-width: 28em;
  margin-bottom: var(--space-xl);
}

.hero-quote {
  font-family: var(--font-display);
  font-size: var(--text-title);
  font-style: italic;
  color: var(--color-ink-secondary);
  margin: 0 0 var(--space-xl);
  padding-left: var(--space-md);
  border-left: 2px solid var(--color-accent);
  opacity: 0.6;
}

.hero-actions {
  display: flex;
  gap: var(--space-md);
  flex-wrap: wrap;
}

.btn-primary {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  padding: var(--space-md) var(--space-xl);
  background: var(--color-ink);
  color: var(--color-paper);
  font-size: var(--text-body);
  font-weight: 500;
  text-decoration: none;
  border-radius: 6px;
  transition:
    background var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out);
}

.btn-primary:hover {
  background: var(--color-accent);
  transform: translateY(-1px);
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
  color: var(--color-ink);
  font-size: var(--text-body);
  font-weight: 500;
  font-family: inherit;
  cursor: pointer;
  text-decoration: none;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}

.btn-secondary:hover {
  border-color: var(--color-ink);
  text-decoration: none;
}

/* ── Announcements ── */
.announcements {
  position: relative;
  overflow: hidden;
}

/* 渐变点缀：柔和光晕铺底，衬托毛玻璃卡片 */
.announcements-decor {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(640px 240px at 6% 0%, var(--color-accent-light), transparent 70%),
    radial-gradient(520px 220px at 94% 100%, var(--color-accent-light), transparent 70%);
}

.announcements .section-header {
  position: relative;
}

.announcement-list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-md);
}

.announcement-card {
  position: relative;
  padding: var(--space-lg) var(--space-xl);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(255, 255, 255, 0.75);
  box-shadow: 0 8px 24px rgba(26, 26, 26, 0.06);
  overflow: hidden;
  transition:
    transform var(--duration-normal) var(--ease-out),
    box-shadow var(--duration-normal) var(--ease-out);
}

/* 左侧渐变描边点缀 */
.announcement-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: linear-gradient(180deg, var(--color-accent), rgba(196, 92, 38, 0.15));
}

.announcement-card--pinned::before {
  background: linear-gradient(180deg, var(--color-accent), #e8a87c);
}

.announcement-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(26, 26, 26, 0.1);
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
  color: var(--color-ink);
  margin: 0;
}

.announcement-pin {
  font-size: var(--text-caption);
  font-weight: 500;
  color: var(--color-accent);
  background: var(--color-accent-light);
  padding: 2px 10px;
  border-radius: 12px;
  flex-shrink: 0;
}

.announcement-date {
  margin-left: auto;
  font-size: var(--text-caption);
  color: var(--color-ink-secondary);
  font-family: var(--font-mono);
  opacity: 0.8;
}

.announcement-content {
  margin: 0;
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  line-height: 1.7;
  white-space: pre-line;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── Section shared ── */
.section {
  padding: var(--space-2xl) var(--space-2xl);
}

.section-header {
  margin-bottom: var(--space-xl);
}

.section-title {
  font-family: var(--font-display);
  font-size: var(--text-display);
  color: var(--color-ink);
  margin: 0 0 var(--space-xs);
}

.section-desc {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
  margin: 0;
}

/* ── Spots Grid ── */
.spots-grid {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
  gap: var(--space-lg);
}

.spot-card {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  overflow: hidden;
  background: transparent;
  transition:
    border-color var(--duration-normal) var(--ease-out),
    transform var(--duration-normal) var(--ease-out);
}

.spot-card:hover {
  border-color: var(--color-ink-secondary);
  transform: translateY(-2px);
}

.spot-card--featured {
  grid-row: span 1;
}

.spot-card-img {
  aspect-ratio: 16/9;
  background: var(--color-overlay);
  overflow: hidden;
}

.img-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-accent-light);
}

.img-placeholder-icon {
  font-size: 2.5rem;
  opacity: 0.5;
}

.spot-card-body {
  padding: var(--space-md) var(--space-lg);
}

.spot-card-tag {
  display: inline-block;
  font-size: var(--text-caption);
  color: var(--color-accent);
  font-weight: 500;
  margin-bottom: var(--space-xs);
}

.spot-card-body h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  margin: 0 0 var(--space-xs);
  color: var(--color-ink);
}

.spot-card-body p {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  margin: 0;
  line-height: 1.5;
}

/* ── Routes List ── */
.routes-list {
  display: flex;
  flex-direction: column;
  gap: 1px;
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}

.route-card {
  display: flex;
  align-items: center;
  gap: var(--space-xl);
  padding: var(--space-lg) 0;
  transition: background var(--duration-fast) var(--ease-out);
}

.route-card:hover {
  background: var(--color-overlay);
}

.route-index {
  font-family: var(--font-mono);
  font-size: var(--text-title);
  color: var(--color-border);
  min-width: 3em;
  text-align: right;
}

.route-content h3 {
  font-family: var(--font-display);
  font-size: var(--text-subtitle);
  margin: 0 0 var(--space-xs);
  color: var(--color-ink);
}

.route-content p {
  font-size: var(--text-body);
  color: var(--color-ink-secondary);
  margin: 0 0 var(--space-xs);
}

.route-meta {
  font-size: var(--text-small);
  color: var(--color-ink-secondary);
  opacity: 0.7;
}

/* ── Responsive ── */
@media (max-width: 1024px) {
  .spots-grid {
    grid-template-columns: 1fr 1fr;
  }
  .spot-card--featured {
    grid-row: auto;
  }
}

@media (max-width: 640px) {
  .hero {
    padding: var(--space-xl) var(--space-lg);
    min-height: auto;
  }

  .hero-title {
    font-size: clamp(1.75rem, 8vw, 3rem);
  }

  .spots-grid {
    grid-template-columns: 1fr;
  }

  .section {
    padding: var(--space-xl) var(--space-lg);
  }

  .route-card {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-sm);
  }

  .route-index {
    text-align: left;
  }
}
</style>
