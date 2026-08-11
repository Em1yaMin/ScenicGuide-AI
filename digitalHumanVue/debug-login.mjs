import { chromium } from '@playwright/test'
import { mockApiRoutes } from './e2e/mocks.ts'

const browser = await chromium.launch()
const page = await browser.newPage()
page.on('pageerror', (e) => console.log('PAGEERROR:', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE-ERR:', m.text().slice(0, 300)) })
await mockApiRoutes(page)
await page.goto('http://localhost:5199/admin/login')
await page.waitForTimeout(3000)
console.log('URL:', page.url())
console.log('BODY:', (await page.locator('body').innerText()).slice(0, 500))
await browser.close()
