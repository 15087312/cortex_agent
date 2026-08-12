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
