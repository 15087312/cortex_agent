import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createTestPinia, stubFetch } from '@/test/helpers.js'
import ModelSelector from './ModelSelector.vue'

describe('ModelSelector', () => {
  it('有 sessionId 时加载图谱节点并渲染标签', async () => {
    stubFetch({ data: { graph: { nodes: [{ tier: 'large', label: '总指挥' }, { tier: 'expert', label: '代码专家' }, { tier: 'user', label: '用户' }] } } })
    const w = mount(ModelSelector, { props: { sessionId: 's1' }, global: { plugins: [createTestPinia()] } })
    await flushPromises()
    const tags = w.findAll('.chat-model-tag')
    expect(tags).toHaveLength(2) // user 被过滤
    expect(w.text()).toContain('总指挥')
    expect(w.text()).toContain('代码专家')
  })

  it('图谱无记录回退总指挥', async () => {
    stubFetch({ data: { graph: { nodes: [] } } })
    const w = mount(ModelSelector, { props: { sessionId: 's1' }, global: { plugins: [createTestPinia()] } })
    await flushPromises()
    expect(w.text()).toContain('总指挥')
  })

  it('无 sessionId 渲染默认模型', () => {
    const w = mount(ModelSelector, { props: { sessionId: '' }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('默认模型')
  })

  it('加载失败回退总指挥', async () => {
    stubFetch(() => { throw new Error('net') }, { ok: false, status: 500 })
    const w = mount(ModelSelector, { props: { sessionId: 's1' }, global: { plugins: [createTestPinia()] } })
    await flushPromises()
    expect(w.text()).toContain('总指挥')
  })
})
