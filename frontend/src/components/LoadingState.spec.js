import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import LoadingState from './LoadingState.vue'

describe('LoadingState', () => {
  it('默认 spinner + 文本', () => {
    const w = mount(LoadingState, { global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('加载中...')
    expect(w.find('.loading-spinner').exists()).toBe(true)
  })

  it('自定义文本', () => {
    const w = mount(LoadingState, { props: { text: '思考中' }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('思考中')
  })

  it('skeleton 模式', () => {
    const w = mount(LoadingState, { props: { skeleton: true }, global: { plugins: [createTestPinia()] } })
    expect(w.findAll('.skeleton-line').length).toBeGreaterThan(0)
    expect(w.find('.loading-spinner').exists()).toBe(false)
  })
})
