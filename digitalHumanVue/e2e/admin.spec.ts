import { expect, test, type Page } from '@playwright/test'
import { attachPageErrorCollector, mockApiRoutes } from './mocks'

/**
 * 管理端冒烟（批次7 C6）：登录流程 + 知识库/公告/用量页基础渲染。
 *
 * 鉴权策略：管理端页面读 localStorage 的 `admin_api_token` 作为 X-Admin-Token，
 * 用例在 goto 前用 addInitScript 预置 token（无需走真实登录），接口请求全部
 * 由浏览器层 mock 应答。
 */

/** 预置管理端 token，使各管理页 API 请求携带 X-Admin-Token。 */
async function presetAdminToken(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('admin_api_token', 'e2e-admin-token')
  })
}

test.describe('管理端', () => {
  test('登录流程：mock 登录成功跳转访客首页', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/login')

    await page.getByLabel(/用户名/).fill('e2e_admin')
    await page.getByLabel(/密码/).fill('e2e-password')
    await page.getByRole('button', { name: /登\s*录/ }).click()

    // 登录成功 → 跳转访客首页（hero 渲染）
    await expect(page).toHaveURL(/\/visitor$/)
    await expect(page.locator('.hero-title')).toContainText('AI 数字人')

    // 登录态持久化（侧栏显示用户名）——回到管理端布局验证
    await page.goto('/admin')
    await expect(page.locator('.sidebar-footer')).toContainText('E2E 测试员')

    expect(errors).toEqual([])
  })

  test('知识库页：列表渲染', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await presetAdminToken(page)
    await mockApiRoutes(page)
    await page.goto('/admin/knowledge')

    // 侧栏导航
    await expect(page.locator('.sidebar-nav')).toContainText('知识库')
    // mock 数据行渲染（标题 + 分类）
    const rows = page.locator('.data-table tbody tr')
    await expect(rows).toHaveCount(2)
    await expect(rows.first()).toContainText('黄鹤楼讲解词')
    await expect(rows.nth(1)).toContainText('FAQ')

    expect(errors).toEqual([])
  })

  test('公告管理页：列表渲染', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await presetAdminToken(page)
    await mockApiRoutes(page)
    await page.goto('/admin/announcements')

    await expect(page.locator('.data-table tbody tr')).toHaveCount(2)
    await expect(page.locator('.data-table tbody tr').first()).toContainText('开放时间调整')
    // 草稿状态展示
    await expect(page.locator('.status-badge.draft')).toHaveText('草稿')

    expect(errors).toEqual([])
  })

  test('用量页：KPI 与明细渲染', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await presetAdminToken(page)
    await mockApiRoutes(page)
    await page.goto('/admin/usage')

    // KPI 卡片（mock totals: 42 次调用 / 15000 tokens / 未配置单价表）
    await expect(page.locator('.kpi-card').filter({ hasText: '调用次数' })).toContainText('42')
    await expect(page.locator('.kpi-card').filter({ hasText: 'Token 总量' })).toContainText(
      '15,000',
    )
    await expect(page.locator('.kpi-card').filter({ hasText: '估算费用' })).toContainText(
      '未配置单价表',
    )

    // 明细表格渲染 mock 数据
    const detailRows = page.locator('.detail-table tbody tr')
    await expect(detailRows).toHaveCount(2)
    await expect(detailRows.first()).toContainText('deepseek-v4-flash')
    await expect(page.locator('.detail-card')).toContainText('共 2 条')

    expect(errors).toEqual([])
  })
})
