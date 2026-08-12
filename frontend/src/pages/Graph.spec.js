import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Graph from './Graph.vue'
import { routeFetch } from '@/test/helpers.js'

function mountPage() {
  return mount(Graph, {
    global: { plugins: [createPinia()] },
  })
}

describe('Graph 页面', () => {
  it('加载会话并自动选择第一个加载图谱', async () => {
    let graphUrl = ''
    routeFetch([
      { match: '/stream/sessions', data: { data: [{ session_id: 's1', title: '会话1', last_active: '2024-01-02T00:00:00' }] } },
      { match: '/stream/session/s1/graph', data: (u) => { graphUrl = u; return { data: { graph: { nodes: [{ id: 'a', tier: 'user', label: '用户' }], edges: [] } } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.selected).toBe('s1')
    expect(graphUrl).toContain('/stream/session/s1/graph')
    expect(w.vm.graph.nodes.length).toBe(1)
  })

  it('layout 分层布局（已知+未知 tier）', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.graph = {
      nodes: [
        { id: 'a', tier: 'user', label: '用户节点' },
        { id: 'b', tier: 'large', label: '总指挥节点' },
        { id: 'c', tier: 'weird', label: '未知节点' },
      ],
      edges: [{ from: 'a', to: 'b', type: '呼唤' }],
    }
    await w.vm.$nextTick()
    const l = w.vm.layout
    expect(Object.keys(l.pos).length).toBe(3)
    expect(l.pos.a).toBeTruthy()
    expect(l.pos.c).toBeTruthy()
    // 未知 tier 也有位置
    expect(w.vm.viewBox).toContain('0 0')
  })

  it('edgeShapes 生成错开边', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.graph = {
      nodes: [
        { id: 'a', tier: 'user', label: '用户节点' },
        { id: 'b', tier: 'large', label: '总指挥节点' },
      ],
      edges: [{ from: 'a', to: 'b', type: '呼唤' }],
    }
    await w.vm.$nextTick()
    const shapes = w.vm.edgeShapes
    expect(shapes.length).toBe(1)
    expect(shapes[0].fromLabel).toBe('用户节点')
    expect(shapes[0].hint).toContain('呼唤')
  })

  it('tierOf 映射与未知回退', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.tierOf('user').label).toBe('用户')
    expect(w.vm.tierOf('large').label).toBe('总指挥')
    expect(w.vm.tierOf('nope').label).toBe('未知')
  })

  it('onSessionChange 切换会话加载图谱', async () => {
    let url = ''
    routeFetch([
      { match: '/stream/session/s2/graph', data: (u) => { url = u; return { data: { graph: { nodes: [], edges: [] } } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.selected = 's2'
    await w.vm.onSessionChange()
    await new Promise((r) => setTimeout(r, 10))
    expect(url).toContain('s2')
  })
})
