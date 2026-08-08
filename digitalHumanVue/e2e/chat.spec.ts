import { expect, test } from '@playwright/test'
import { attachPageErrorCollector, mockApiRoutes } from './mocks'

/**
 * 对话页冒烟（批次7 C6）：进入页面 + 推荐问句浮层。
 *
 * 说明：E2E 环境未配置 VITE_LIGHT4AI_URL（ADH 数字人 iframe 地址），
 * 页面渲染占位面板；推荐问句浮层与对话入口无关，照常覆盖。
 */

test.describe('访客对话页', () => {
  test('进入对话页：游客兜底 + 推荐问句浮层渲染', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/visitor/chat')

    // 页面主体渲染（E2E 无 ADH 地址 → 配置缺失占位面板）
    await expect(page.locator('.chat')).toBeVisible()

    // 推荐问句浮层加载并展示 mock 问句
    await expect(page.locator('.questions-bar')).toBeVisible()
    await expect(page.locator('.question-chip')).toHaveCount(3)
    await expect(page.locator('.question-chip').first()).toContainText('必打卡景点')

    // 游客 token 已签发（登录态被持久化，重新进入时不重复签发）
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('auth_token')))
      .toBe('e2e-guest-token')

    expect(errors).toEqual([])
  })

  test('点击推荐问句：浮层收起（注入后自动隐藏）', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/visitor/chat')

    // 等待问句渲染后点击第一条
    const firstChip = page.locator('.question-chip').first()
    await expect(firstChip).toBeVisible()
    await firstChip.click()

    // 浮层收起 → 出现「💬 推荐问题」重新展开按钮
    await expect(page.locator('.questions-reopen')).toBeVisible()
    await expect(page.locator('.questions-bar')).toBeHidden()

    // 可重新展开
    await page.locator('.questions-reopen').click()
    await expect(page.locator('.questions-bar')).toBeVisible()

    expect(errors).toEqual([])
  })
})
