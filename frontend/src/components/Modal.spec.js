import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import Modal from './Modal.vue'

describe('Modal', () => {
  it('渲染标题与默认 slot', () => {
    const w = mount(Modal, {
      props: { title: '弹窗标题' },
      slots: { default: '内容' },
      global: { plugins: [createTestPinia()] },
    })
    expect(w.text()).toContain('弹窗标题')
    expect(w.text()).toContain('内容')
  })

  it('无标题不渲染 h3', () => {
    const w = mount(Modal, { slots: { default: 'x' }, global: { plugins: [createTestPinia()] } })
    expect(w.find('h3').exists()).toBe(false)
  })

  it('点击遮罩 emit close', async () => {
    const w = mount(Modal, { slots: { default: 'x' }, global: { plugins: [createTestPinia()] } })
    await w.find('.modal-overlay').trigger('click')
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('渲染 actions slot', () => {
    const w = mount(Modal, {
      slots: { default: 'x', actions: '<button class="act-btn">确定</button>' },
      global: { plugins: [createTestPinia()] },
    })
    expect(w.find('.act-btn').exists()).toBe(true)
  })
})
