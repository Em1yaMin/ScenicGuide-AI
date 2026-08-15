import { expect, test } from '@playwright/test'
import { attachPageErrorCollector, mockApiRoutes, homeHighlights } from './mocks'

/**
 * 首页冒烟（批次7 C6）：加载渲染 + 浏览推荐滚动。
 */

test.describe('访客首页', () => {
  test('首页加载：hero 与活数据卡片渲染', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/visitor')

    // hero 区渲染
    await expect(page.locator('.hero-title')).toContainText('AI 数字人')
    await expect(page.locator('.btn-primary')).toHaveText(/开始对话/)

    // 公告区（mock 返回 1 条已发布公告）
    await expect(page.locator('.announcement-card')).toHaveCount(1)
    await expect(page.locator('.announcement-card-title h3')).toContainText('开放时间调整')

    // 热门景点活数据（3 条；mock 数据含静态兜底没有的「祥符禅寺」，证明活数据生效）
    await expect(page.locator('.spot-card')).toHaveCount(3)
    await expect(page.locator('.spot-card h3').first()).toContainText('灵山大照壁')
    await expect(page.getByRole('heading', { name: '祥符禅寺' })).toBeVisible()

    // 热门景点实景图：mock 仅首条带 image → 渲染 <img>（余下回退 emoji 占位）
    await expect(page.locator('.spot-card-img img')).toHaveCount(1)
    await expect(page.locator('.spot-card-img img').first()).toHaveAttribute(
      'src',
      '/images/spots/spot_001.jpg',
    )

    // 推荐路线活数据（3 条；mock 数据含静态兜底没有的「历史文化深度游」）
    await expect(page.locator('.route-card')).toHaveCount(3)
    await expect(page.locator('.route-card h3').first()).toContainText('历史文化深度游')

    expect(errors).toEqual([])
  })

  test('浏览推荐：点击按钮平滑滚动到推荐路线区块', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/visitor')

    // 初始时推荐路线区块不在视口内（hero 占满首屏）
    const routesSection = page.locator('#recommended-routes')
    await expect(routesSection).not.toBeInViewport()

    // 点击「浏览推荐」→ scrollIntoView 平滑滚动
    await page.getByRole('button', { name: '浏览推荐' }).click()
    await expect(routesSection).toBeInViewport()
    await expect(routesSection.locator('.route-card h3').first()).toContainText(
      homeHighlights.routes[0].name,
    )

    expect(errors).toEqual([])
  })
})
