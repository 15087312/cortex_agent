import { defineConfig } from '@playwright/test'

// UI 交互/可视化冒烟：真实 Chromium + 真实 CSS，覆盖 jsdom 测不到的计算样式与媒体权限。
export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: false,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5175',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'npx vite --host 127.0.0.1 --port 5175 --strictPort',
    url: 'http://127.0.0.1:5175/e2e/chat-input.html',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
