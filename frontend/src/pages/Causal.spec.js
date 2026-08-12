import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Causal from './Causal.vue'
import { routeFetch } from '@/test/helpers.js'

function mountPage() {
  return mount(Causal, {
    global: { plugins: [createPinia()] },
  })
}

describe('Causal 页面', () => {
  it('加载因果图并计算布局', async () => {
    routeFetch([
      {
        match: '/management/causal-graph',
        data: {
          data: {
            nodes: [
              { id: 'n1', type: 'root', event_count: 10, label: '根因' },
              { id: 'n2', type: 'cause', event_count: 2, label: '原因' },
              { id: 'n3', type: 'effect', event_count: 5, label: '结果' },
            ],
            edges: [{ from: 'n1', to: 'n3' }],
            stats: { total_nodes: 3 },
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.nodes.length).toBe(3)
    expect(w.vm.stats.total_nodes).toBe(3)
    expect(Object.keys(w.vm.positions).length).toBe(3)
    expect(w.vm.displayNodes.length).toBe(3)
    expect(w.vm.zoom).toBe(1)
  })

  it('nodeColor/nodeStroke/nodeRadius 映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.nodeColor('root')).toBe('#22C55E')
    expect(w.vm.nodeColor('unknown')).toBe('#94A3B8')
    expect(w.vm.nodeStroke('cause')).toBe('#D97706')
    expect(w.vm.nodeRadius({ event_count: 0 })).toBe(10)
    expect(w.vm.nodeRadius({ event_count: 100 })).toBe(22) // 上限 10+12
  })

  it('hover 高亮 connectedNodeIds/opacity', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.displayNodes = [{ id: 'a', label: 'a' }, { id: 'b', label: 'b' }, { id: 'c', label: 'c' }]
    w.vm.displayEdges = [{ from: 'a', to: 'b' }]
    w.vm.hoveredNode = 'a'
    const ids = w.vm.connectedNodeIds
    expect(ids.has('a')).toBe(true)
    expect(ids.has('b')).toBe(true)
    expect(ids.has('c')).toBe(false)
    expect(w.vm.nodeOpacity({ id: 'a' })).toBe(1)
    expect(w.vm.nodeOpacity({ id: 'c' })).toBe(0.15)
    expect(w.vm.edgeOpacity({ from: 'a', to: 'b' })).toBe(0.9)
    expect(w.vm.edgeOpacity({ from: 'b', to: 'c' })).toBe(0.06)
    expect(w.vm.edgeWidth({ from: 'a', to: 'b' })).toBe(2.5)
  })

  it('onWheel 缩放与 resetView', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.graphContainer = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 900, height: 500 }) }
    const before = w.vm.viewBox
    w.vm.onWheel({ preventDefault: () => {}, deltaY: 100, clientX: 450, clientY: 250 })
    expect(w.vm.zoom).toBeGreaterThan(1)
    expect(w.vm.viewBox.w).toBeGreaterThan(before.w) // deltaY>0 → scale 1.12 → viewBox 放大
    w.vm.resetView()
    expect(w.vm.zoom).toBe(1)
    expect(w.vm.viewBox.w).toBe(900)
  })

  it('handleShowTree 加载详情与因果树', async () => {
    routeFetch([
      { match: '/management/causal-graph/n1', data: { data: { id: 'n1', label: '根因' } } },
      { match: '/management/causal-graph/tree/n1', data: { data: { trace_up: [], trace_down: [] } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    await w.vm.handleShowTree('n1')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.detail?.label).toBe('根因')
    expect(w.vm.tree).not.toBeNull()
    expect(w.vm.treeLoading).toBe(false)
  })

  it('nodeBadgeClass 映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.nodeBadgeClass('root')).toBe('badge-green')
    expect(w.vm.nodeBadgeClass('cause')).toBe('badge-yellow')
    expect(w.vm.nodeBadgeClass('effect')).toBe('badge-blue')
  })

  it('画布拖拽：节点上按下不启动、空白处按下启动并平移视口', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.viewBox = { x: 100, y: 50, w: 900, h: 500 }
    w.vm.graphContainer = { getBoundingClientRect: () => ({ width: 900, height: 500 }) }
    // 落在 <g>（节点）上 → 不启动拖拽
    const gEl = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    w.vm.onMouseDown({ target: { closest: () => gEl }, clientX: 0, clientY: 0 })
    expect(w.vm.isDragging).toBe(false)
    // 空白处按下 → 启动拖拽
    w.vm.onMouseDown({ target: { closest: () => null }, clientX: 100, clientY: 100 })
    expect(w.vm.isDragging).toBe(true)
    // 移动 100px → 视口反向平移
    w.vm.onMouseMove({ clientX: 200, clientY: 200 })
    expect(w.vm.viewBox.x).toBe(0)
    expect(w.vm.viewBox.y).toBe(-50)
    w.vm.onMouseUp()
    expect(w.vm.isDragging).toBe(false)
  })
})
