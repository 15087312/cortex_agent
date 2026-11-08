import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import ProcessPanel from './ProcessPanel.vue'
import TrajectoryTimeline from './TrajectoryTimeline.vue'

function mountPanel(props = {}) {
  return mount(ProcessPanel, { props, global: { plugins: [createTestPinia()] } })
}

describe('ProcessPanel', () => {
  beforeEach(() => {
    createTestPinia()
  })

  it('优先使用 live 模式的 store 轨迹', () => {
    const w = mountPanel({ live: true })
    const tl = w.findComponent(TrajectoryTimeline)
    expect(tl.exists()).toBe(true)
    expect(tl.props('entries')).toEqual([])
    expect(tl.props('title')).toBe('思考轨迹')
  })

  it('非 live 优先渲染历史 trajectory', () => {
    const trajectory = [
      { kind: 'thinking', title: 'a', ts: 2 },
      { kind: 'done', title: 'b', ts: 1 },
    ]
    const w = mountPanel({ trajectory })
    expect(w.findComponent(TrajectoryTimeline).props('entries')).toHaveLength(2)
  })

  it('无 trajectory 时按空行切分 content 回退为 thinking 条目', () => {
    const w = mountPanel({ content: '第一段\n\n第二段\n\n' })
    const entries = w.findComponent(TrajectoryTimeline).props('entries')
    expect(entries).toEqual([
      { kind: 'thinking', title: '第一段', ts: 0 },
      { kind: 'thinking', title: '第二段', ts: 0 },
    ])
  })

  it('空 trajectory 数组仍回退到 content', () => {
    const w = mountPanel({ trajectory: [], content: '只有一段' })
    const entries = w.findComponent(TrajectoryTimeline).props('entries')
    expect(entries).toEqual([{ kind: 'thinking', title: '只有一段', ts: 0 }])
  })

  it('无任何数据源时条目为空', () => {
    const w = mountPanel({})
    expect(w.findComponent(TrajectoryTimeline).props('entries')).toEqual([])
  })

  it('open 透传给时间线', () => {
    const w = mountPanel({ open: true, trajectory: [{ kind: 'thinking', title: 'a', ts: 1 }] })
    expect(w.findComponent(TrajectoryTimeline).props('open')).toBe(true)
  })
})
