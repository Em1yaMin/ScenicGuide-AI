import { defineConfig } from '@playwright/test'

/**
 * Playwright E2E 冒烟配置（批次7 C6：测试质量门）。
 *
 * 设计要点：
 * - 仅跑 chromium（CI 只安装该浏览器，控制总时长）
 * - webServer 自动拉起 Vite dev server（独立端口 5199，避开 5173/8000/8001/3000）
 * - 后端接口全部在浏览器层用 page.route 拦截 mock（见 e2e/mocks.ts），
 *   不依赖真实后端 / 外部服务，稳定可重复
 */
export default defineConfig({
  testDir: './e2e',
  // 冒烟套件：串行执行，避免并行拉起的 dev server 状态互相干扰
  fullyParallel: false,
  workers: 1,
  // 冒烟用例整体控制在数分钟内
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:5199',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
  webServer: {
    command: 'npm run dev -- --port 5199 --strictPort',
    url: 'http://localhost:5199',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
