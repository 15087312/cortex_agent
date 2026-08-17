import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import ThinkingStatusPanel from './ThinkingStatusPanel.vue'

describe('ThinkingStatusPanel', () => {
  it('无 runners 不渲染', () => {
    const w = mount(ThinkingStatusPanel, { props: { runners: [] }, global: { plugins: [createTestPinia()] } })
    expect(w.find('.think-panel').exists()).toBe(false)
  })

  it('渲染 runner 树与耗时', () => {
    const runners = [
      { model_id: 'L', name: '总指挥', status: 'thinking' },
      { model_id: 'S', name: '主管', status: 'idle', supervisor: 'L' },
    ]
    const w = mount(ThinkingStatusPanel, { props: { runners, elapsed: 12 }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('思考循环')
    expect(w.text()).toContain('12s')
    expect(w.text()).toContain('总指挥')
    expect(w.text()).toContain('主管')
  })

  it('上下文占用百分比', () => {
    const runners = [{ model_id: 'L', name: '总指挥', status: 'idle' }]
    const w = mount(ThinkingStatusPanel, { props: { runners, contextTokens: 5000, contextWindowSize: 10000 }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('50%')
  })

  it('错误 runner 显示横幅', () => {
    const runners = [{ model_id: 'L', name: '总指挥', status: 'error', status_detail: 'LLM 超时' }]
    const w = mount(ThinkingStatusPanel, { props: { runners }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('出错')
    expect(w.text()).toContain('LLM 超时')
  })
})


// ── 上下文容量展示边界（warn 70% / danger 90%） ──────────────────────────

function mountWith(ctxTokens, winSize) {
  const runners = [{ model_id: 'L', name: '总指挥', status: 'idle' }]
  return mount(ThinkingStatusPanel, {
    props: { runners, contextTokens: ctxTokens, contextWindowSize: winSize },
    global: { plugins: [createTestPinia()] },
  })
}

it('上下文 warn 阈值（70%+）标黄', () => {
  const w = mountWith(7000, 10000)
  expect(w.find('.context-usage-fill').classes()).toContain('warn')
})

it('上下文 danger 阈值（90%+）标红', () => {
  const w = mountWith(9500, 10000)
  expect(w.find('.context-usage-fill').classes()).toContain('danger')
})

it('上下文满格封顶 100%', () => {
  const w = mountWith(20000, 10000)
  expect(w.text()).toContain('100%')
})

it('无 context_tokens 时不显示上下文条', () => {
  const w = mountWith(0, 10000)
  expect(w.find('.context-usage').exists()).toBe(false)
})

it('无 context_window_size 时不显示上下文条', () => {
  const w = mountWith(500, 0)
  expect(w.find('.context-usage').exists()).toBe(false)
})
