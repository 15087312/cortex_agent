import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import Icon from './Icon.vue'

describe('Icon', () => {
  it('渲染 svg 与尺寸', () => {
    const w = mount(Icon, { props: { name: 'message', size: 20 }, global: { plugins: [createTestPinia()] } })
    const svg = w.find('svg')
    expect(svg.exists()).toBe(true)
    expect(svg.attributes('width')).toBe('20')
    expect(svg.attributes('height')).toBe('20')
    expect(w.html()).toContain('<path')
  })

  it('未知图标渲染空内容不崩溃', () => {
    const w = mount(Icon, { props: { name: 'not-exist' }, global: { plugins: [createTestPinia()] } })
    expect(w.find('svg').exists()).toBe(true)
    expect(w.find('g').html()).toContain('</g>')
  })

  it('默认尺寸 16', () => {
    const w = mount(Icon, { props: { name: 'x' }, global: { plugins: [createTestPinia()] } })
    expect(w.find('svg').attributes('width')).toBe('16')
  })
})
