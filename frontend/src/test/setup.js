// ── 前端测试全局 setup ──
// 提供测试环境所需的浏览器 API stub 与通用 mock

import { vi, afterEach, beforeEach } from 'vitest'
import { config } from '@vue/test-utils'

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
})
