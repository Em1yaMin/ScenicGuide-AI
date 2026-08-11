import { chromium } from '@playwright/test'
import { mockApiRoutes } from './e2e/mocks.ts'

const out = '/tmp/no-mistakes-evidence/01KZRG68Z1NWT07P2K3XD18452'
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
await mockApiRoutes(page)

// 1. 游客视角进入 /admin/login（无任何登录态）
await page.goto('http://localhost:5199/admin/login')
await page.waitForTimeout(1500)
await page.screenshot({ path: `${out}/01-admin-login-back-home.png` })
const link = page.getByRole('link', { name: /返回首页/ })
console.log('back-home link visible:', await link.isVisible())

// 2. hover 态（描边按钮 hover 青玉）
await link.hover()
await page.waitForTimeout(400)
await page.screenshot({ path: `${out}/02-back-home-hover.png` })

// 3. 点击返回首页 → /visitor
await link.click()
await page.waitForTimeout(1500)
console.log('final URL:', page.url())
await page.screenshot({ path: `${out}/03-visitor-home-after-back.png` })
const hero = await page.locator('.hero-title').innerText().catch(() => 'NOT FOUND')
console.log('hero-title:', hero.replace(/\n/g, ' | '))
await browser.close()
