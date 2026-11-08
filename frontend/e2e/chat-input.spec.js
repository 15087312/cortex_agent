import { test, expect } from '@playwright/test'

test('联网搜索按钮有可见激活态，发送载荷带 webSearch', async ({ page }) => {
  await page.goto('/e2e/chat-input.html')
  const globe = page.locator('.chat-input-toolbar .chat-btn-icon').nth(2)
  const before = await globe.evaluate((el) => getComputedStyle(el).backgroundColor)
  await globe.click()
  await expect(globe).toHaveClass(/chat-btn-active/)
  const after = await globe.evaluate((el) => getComputedStyle(el).backgroundColor)
  expect(after).not.toBe(before)

  await page.locator('.chat-input-field').fill('查一下最新消息')
  await page.locator('.chat-send-btn').click()
  await expect(page.locator('#last-payload')).toContainText('"webSearch":true')
})

test('麦克风按钮进入可见录音态', async ({ page }) => {
  // CI/headless 不依赖真实麦克风：在页面初始化前注入可用的假设备与 MediaRecorder。
  // 真实 Qt/系统麦克风权限由桌面端手工冒烟确认。
  await page.addInitScript(() => {
    const stream = { getTracks: () => [{ stop() {} }] }
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: async () => stream },
    })
    class FakeRecorder {
      static isTypeSupported() { return false }
      constructor(_stream, opts) {
        this.mimeType = opts?.mimeType || 'audio/webm'
        this.state = 'inactive'
        this.ondataavailable = null
        this.onstop = null
      }
      start() { this.state = 'recording' }
      stop() { this.state = 'inactive'; this.onstop?.() }
    }
    window.MediaRecorder = FakeRecorder
  })
  await page.goto('/e2e/chat-input.html')
  const mic = page.locator('.chat-input-toolbar .chat-btn-icon').nth(1)
  await mic.click()
  await expect(mic).toHaveClass(/chat-btn-recording/, { timeout: 10_000 })
  const bg = await mic.evaluate((el) => getComputedStyle(el).backgroundColor)
  expect(bg).not.toBe('rgba(0, 0, 0, 0)')
})
