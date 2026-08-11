import { expect, test, type Page } from '@playwright/test'
import { attachPageErrorCollector, mockApiRoutes } from './mocks'

/**
 * 管理端冒烟（批次7 C6 基础 + 批次9 管理员登录改造）。
 *
 * 批次9 起管理端鉴权为「管理员账号密码登录 → JWT（admin_jwt）」：
 * - 路由守卫：未登录访问 /admin 重定向 /admin/login
 * - 管理页 API 请求携带 Authorization Bearer（utils/adminHeaders.ts，
 *   同时保留 X-Admin-Token 向后兼容）
 * 用例在 goto 前用 addInitScript 预置 admin_jwt（无需走真实登录），
 * 接口请求全部由浏览器层 mock 应答。
 */

/** 预置管理员登录态（admin_jwt），使各管理页 API 请求携带管理员 JWT。 */
async function presetAdminLogin(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('admin_jwt', 'e2e-admin-jwt')
    localStorage.setItem('admin_username', 'admin')
    // 旧用法兼容：仍预置 X-Admin-Token（证明旧路径不受影响）
    localStorage.setItem('admin_api_token', 'e2e-admin-token')
  })
}

test.describe('管理端', () => {
  test('管理员登录流程：账号密码登录成功跳转管理后台', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/admin/login')

    await page.getByLabel(/管理员账号/).fill('admin')
    await page.getByLabel(/密码/).fill('admin123456')
    await page.getByRole('button', { name: /进入管理后台/ }).click()

    // 登录成功 → 跳转管理后台首页（数据大屏侧栏导航渲染）
    await expect(page).toHaveURL(/\/admin$/)
    await expect(page.locator('.sidebar-nav')).toContainText('数据大屏')
    // 侧栏显示管理身份
    await expect(page.locator('.sidebar-footer')).toContainText('管理：admin')

    expect(errors).toEqual([])
  })

  test('登录守卫：未登录访问 /admin 重定向到 /admin/login', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/admin/knowledge')

    // 未登录（无 admin_jwt）→ 重定向管理员登录页，登录表单可见
    await expect(page).toHaveURL(/\/admin\/login/)
    await expect(page.getByLabel(/管理员账号/)).toBeVisible()

    expect(errors).toEqual([])
  })

  test('管理员登录页：返回首页入口存在且可导航回首页', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/admin/login')

    // 游客误入可随时退出：返回首页入口可见（页头描边按钮）
    const backHome = page.getByRole('link', { name: /返回首页/ })
    await expect(backHome).toBeVisible()

    // 点击 → 导航回首页（/ 重定向 /visitor，hero 渲染）
    await backHome.click()
    await expect(page).toHaveURL(/\/visitor$/)
    await expect(page.locator('.hero-title')).toContainText('AI 数字人')

    expect(errors).toEqual([])
  })

  test('用户登录流程：mock 登录成功跳转访客首页', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await mockApiRoutes(page)
    await page.goto('/login')

    await page.getByLabel(/用户名/).fill('e2e_admin')
    await page.getByLabel(/密码/).fill('e2e-password')
    await page.getByRole('button', { name: /登\s*录/ }).click()

    // 登录成功 → 跳转访客首页（hero 渲染）
    await expect(page).toHaveURL(/\/visitor$/)
    await expect(page.locator('.hero-title')).toContainText('AI 数字人')

    expect(errors).toEqual([])
  })

  test('知识库页：列表渲染', async ({ page }) => {
    const errors = attachPageErrorCollector(page)
    await presetAdminLogin(page)
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
    await presetAdminLogin(page)
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
    await presetAdminLogin(page)
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
