// ── 前端测试全局 setup ──
// 提供测试环境所需的浏览器 API stub 与通用 mock

import { vi, afterEach, beforeEach } from 'vitest'
import { config } from '@vue/test-utils'
import { useI18n } from '@/i18n/init.js'

// 全局注入 i18n：复用运行时同一单例（init.js），保证 locale store 的切换在测试中也生效
config.global.plugins.push(useI18n())

// 全局 fetch stub：默认返回空 JSON（各测试按需覆盖）
const defaultFetch = vi.fn(async () => ({
  ok: true,
  status: 200,
  json: async () => ({}),
  text: async () => '',
}))

beforeEach(() => {
  globalThis.fetch = defaultFetch
  // jsdom 缺少的浏览器 API
  if (!globalThis.navigator.geolocation) {
    globalThis.navigator.geolocation = {
      watchPosition: vi.fn(),
      clearWatch: vi.fn(),
    }
  }
  if (!globalThis.navigator.wakeLock) {
    globalThis.navigator.wakeLock = {
      request: vi.fn(async () => ({
        release: vi.fn(async () => {}),
        addEventListener: vi.fn(),
      })),
    }
  }
  if (!globalThis.navigator.clipboard) {
    globalThis.navigator.clipboard = { writeText: vi.fn(async () => {}) }
  }
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  // 重置 i18n 语言为默认 zh，避免测试间相互污染
  try { useI18n().global.locale.value = 'zh' } catch {}
})
