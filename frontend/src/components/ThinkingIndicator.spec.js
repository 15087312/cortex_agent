import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import ThinkingIndicator from './ThinkingIndicator.vue'

describe('ThinkingIndicator', () => {
  it('默认文本', () => {
    const w = mount(ThinkingIndicator, { global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('正在思考')
  })

  it('自定义 label', () => {
    const w = mount(ThinkingIndicator, { props: { label: '正在思考 5s' }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('正在思考 5s')
  })
})
