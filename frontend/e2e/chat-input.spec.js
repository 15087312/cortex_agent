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

test('技能菜单向上展开且不被输入框裁剪', async ({ page }) => {
  await page.route('**/api/management/skills', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        data: {
          skills: [
            { id: 'ui_design', name: 'UI 设计', description: '设计界面', enabled: true },
            { id: 'code_review', name: '代码审查', description: '审查代码', enabled: true },
          ],
        },
      }),
    })
  })
  await page.goto('/e2e/chat-input.html')
  // 冒烟页默认把输入框放在页面顶部；往下推，模拟聊天页输入框贴底、上方有空间。
  await page.addStyleTag({ content: '.chat-input-area { margin-top: 240px; }' })
  await page.locator('.chat-skill-picker > button').click()
  const menu = page.locator('.chat-skill-menu')
  await expect(menu).toBeVisible()
  await expect(menu.locator('.chat-skill-item')).toHaveCount(3)

  // 输入区/输入框不能裁剪向上展开的菜单。
  const overflow = await page.evaluate(() => ({
    area: getComputedStyle(document.querySelector('.chat-input-area')).overflow,
    wrapper: getComputedStyle(document.querySelector('.chat-input-wrapper')).overflow,
  }))
  expect(overflow.area).toBe('visible')
  expect(overflow.wrapper).toBe('visible')

  const geometry = await page.evaluate(() => {
    const menuEl = document.querySelector('.chat-skill-menu')
    const wrapperEl = document.querySelector('.chat-input-wrapper')
    const m = menuEl.getBoundingClientRect()
    const w = wrapperEl.getBoundingClientRect()
    const hit = document.elementFromPoint(m.left + m.width / 2, m.top + 8)
    return {
      menuTop: m.top,
      wrapperTop: w.top,
      menuHit: !!hit?.closest('.chat-skill-menu'),
    }
  })
  // 菜单确实越过输入框上边界，且该区域命中的是菜单而不是被裁剪后的底层元素。
  expect(geometry.menuTop).toBeLessThan(geometry.wrapperTop)
  expect(geometry.menuHit).toBe(true)

  await menu.locator('.chat-skill-item').nth(1).click()
  await page.locator('.chat-input-field').fill('做一个界面')
  await page.locator('.chat-send-btn').click()
  await expect(page.locator('#last-payload')).toContainText('"skillId":"ui_design"')
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
