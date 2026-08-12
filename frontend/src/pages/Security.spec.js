import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Security from './Security.vue'
import { routeFetch } from '@/test/helpers.js'

let wrapper = null
function mountPage() {
  wrapper = mount(Security, {
    global: { plugins: [createPinia()] },
  })
  return wrapper
}

describe('Security 页面', () => {
  afterEach(() => {
    // 卸载组件 → onBeforeUnmount 清理 30s 轮询定时器
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })
  it('加载安全状态与审计日志并统计拦截数', async () => {
    routeFetch([
      { match: '/security/status', data: { data: { state: { L0: true, L1: false, L2: true } } } },
      {
        match: '/security/audit?limit=50',
        data: {
          data: {
            logs: [
              { action: 'exec', passed: false, content: 'rm -rf /' },
              { action: 'read', result: true, content: 'ok' },
              { action: 'write', result: '通过', content: 'fine' },
            ],
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const text = w.text()
    expect(w.vm.enabledCount).toBe(2)
    expect(w.vm.totalLogs).toBe(3)
    expect(w.vm.blocked).toBe(1) // 只有 passed:false 那条
    expect(text).toContain('rm -rf /')
  })

  it('切换安全级别开关调用对应端点', async () => {
    const calls = []
    routeFetch([
      { match: '/security/switch?level=', data: () => { calls.push('switch'); return { success: true } } },
      { match: '/security/status', data: { data: { state: { L0: true } } } },
      { match: '/security/audit?limit=50', data: { data: { logs: [] } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const toggles = w.findAll('button').filter((b) => b.text().includes('开') || b.text().includes('关') || b.text().includes('启用'))
    if (toggles.length) {
      await toggles[0].trigger('click')
      await new Promise((r) => setTimeout(r, 10))
      expect(calls).toContain('switch')
    } else {
      // 直接调用内部方法
      await w.vm.handleToggle('L3', true)
      await new Promise((r) => setTimeout(r, 10))
      expect(calls).toContain('switch')
    }
  })

  it('passed/actionOf/contentOf 解析', () => {
    const w = mountPage()
    expect(w.vm.passed({ passed: true })).toBe(true)
    expect(w.vm.passed({ result: '通过' })).toBe(true)
    expect(w.vm.passed({})).toBe(false)
    expect(w.vm.actionOf({ action: 'a', type: 'b' })).toBe('a')
    expect(w.vm.contentOf({ content: 'x'.repeat(100) })).toHaveLength(80)
  })
})
