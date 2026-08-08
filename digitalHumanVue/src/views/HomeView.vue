<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { ref, onMounted } from 'vue'

const heroVisible = ref(false)

onMounted(() => {
  // 入场动画延迟一帧以确保浏览器完成初始渲染
  requestAnimationFrame(() => {
    heroVisible.value = true
  })
})

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
        <p class="hero-label">
          <span class="hero-label-line" />AI 智慧景区服务
        </p>
        <h1 class="hero-title">
          探索景区故事<br />
          <span class="hero-accent">AI 数字人</span>为您导览
        </h1>
        <p class="hero-subtitle">
          与数字人对话，发现每个景点背后的历史与文化
        </p>
        <blockquote class="hero-quote">
          「登山则情满于山，观海则意溢于海」
        </blockquote>
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

    <!-- Featured Spots -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">热门景点</h2>
        <p class="section-desc">探索最受游客喜爱的目的地</p>
      </div>
      <div class="spots-grid">
        <div class="spot-card spot-card--featured">
          <div class="spot-card-img">
            <div class="img-placeholder">
              <span class="img-placeholder-icon">🏯</span>
            </div>
          </div>
          <div class="spot-card-body">
            <span class="spot-card-tag">历史古迹</span>
            <h3>黄鹤楼</h3>
            <p>天下绝景，千年诗楼，登高望远尽览江城风貌</p>
          </div>
        </div>
        <div class="spot-card">
          <div class="spot-card-img">
            <div class="img-placeholder">
              <span class="img-placeholder-icon">🏞</span>
            </div>
          </div>
          <div class="spot-card-body">
            <span class="spot-card-tag">自然风光</span>
            <h3>东湖绿道</h3>
            <p>城中湖泊，四季皆景，骑行漫步的绝佳去处</p>
          </div>
        </div>
        <div class="spot-card">
          <div class="spot-card-img">
            <div class="img-placeholder">
              <span class="img-placeholder-icon">🏛</span>
            </div>
          </div>
          <div class="spot-card-body">
            <span class="spot-card-tag">文化博览</span>
            <h3>省博物馆</h3>
            <p>曾侯乙编钟，千年楚文化的璀璨见证</p>
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
        <div class="route-card">
          <div class="route-index">01</div>
          <div class="route-content">
            <h3>经典一日游</h3>
            <p>黄鹤楼 → 户部巷 → 东湖绿道</p>
            <span class="route-meta">约 6 小时 · 适合初次到访</span>
          </div>
        </div>
        <div class="route-card">
          <div class="route-index">02</div>
          <div class="route-content">
            <h3>文化深度游</h3>
            <p>省博物馆 → 归元寺 → 古琴台</p>
            <span class="route-meta">约 8 小时 · 适合文化爱好者</span>
          </div>
        </div>
        <div class="route-card">
          <div class="route-index">03</div>
          <div class="route-content">
            <h3>亲子欢乐行</h3>
            <p>东湖海洋世界 → 植物园 → 磨山</p>
            <span class="route-meta">约 5 小时 · 适合家庭出游</span>
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
