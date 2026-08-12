import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import RunnerNode from './RunnerNode.vue'

describe('RunnerNode', () => {
  it('渲染节点状态与详细信息', () => {
    const node = {
      name: '总指挥',
      model_id: 'large_primary',
      tier: 'large',
      status: 'thinking',
      react_loop: { turn: 2, max: 5, tool: 'read_file' },
      think_loop: { round: 1, max: 3 },
      children: [],
    }
    const w = mount(RunnerNode, { props: { node }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('总指挥')
    expect(w.text()).toContain('large_primary')
    expect(w.text()).toContain('思考中')
    expect(w.text()).toContain('工具循环 2/5 · read_file')
    expect(w.text()).toContain('思考 1/3')
  })

  it('错误状态与 waiting_delegation 详情', () => {
    const w = mount(RunnerNode, {
      props: { node: { name: '专家', model_id: 'm', status: 'error', status_detail: '超时', children: [] } },
      global: { plugins: [createTestPinia()] },
    })
    expect(w.text()).toContain('出错')
    expect(w.text()).toContain('超时')
    const w2 = mount(RunnerNode, {
      props: { node: { name: '主管', model_id: 'm2', status: 'waiting_delegation', status_detail: '等待返回', children: [] } },
      global: { plugins: [createTestPinia()] },
    })
    expect(w2.text()).toContain('等待委托')
    expect(w2.text()).toContain('等待返回')
  })

  it('递归渲染子节点', () => {
    const node = {
      name: '总指挥', model_id: 'L', tier: 'large', status: 'idle', children: [
        { name: '主管', model_id: 'S', tier: 'supervisor', status: 'completed', children: [] },
      ],
    }
    const w = mount(RunnerNode, { props: { node }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('主管')
  })

  it('active_skill 徽章', () => {
    const w = mount(RunnerNode, {
      props: { node: { name: 'n', model_id: 'm', status: 'idle', active_skill: 'code_review', children: [] } },
      global: { plugins: [createTestPinia()] },
    })
    expect(w.text()).toContain('code_review')
  })
})
