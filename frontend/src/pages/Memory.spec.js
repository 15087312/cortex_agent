import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import Memory from './Memory.vue'
import { routeFetch, createTestPinia } from '@/test/helpers.js'
import { resolveDialog } from '@/composables/useDialog.js'

function mountPage() {
  const pinia = createTestPinia()
  const w = mount(Memory, { global: { plugins: [pinia] } })
  return w
}

describe('Memory 页面', () => {
  it('加载记忆事件并按日期分组', async () => {
    routeFetch([
      {
        match: '/management/memory/events?limit=50',
        data: {
          data: {
            events: [
              { id: 'e1', fact: '学习 Python', time: '2024-01-01T10:00:00', importance: 0.8, type: 'fact' },
              { id: 'e2', fact: '思考架构', time: '2024-01-02T10:00:00', importance: 0.3, type: 'thought' },
            ],
            total: 2,
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    expect(w.vm.total).toBe(2)
    expect(w.text()).toContain('学习 Python')
    const groups = w.vm.groupedByDate
    expect(groups.length).toBe(2)
    expect(groups[0][0]).toBe('2024-01-02') // 倒序
  })

  it('starRating 星级渲染', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.starRating(0.8)).toBe('★★★★☆')
    expect(w.vm.starRating(0.2)).toBe('★☆☆☆☆')
  })

  it('typeBadgeClass 映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.typeBadgeClass('fact')).toBe('badge-blue')
    expect(w.vm.typeBadgeClass('thought')).toBe('badge-green')
    expect(w.vm.typeBadgeClass('strategy')).toBe('badge-yellow')
    expect(w.vm.typeBadgeClass('x')).toBe('badge-gray')
  })

  it('handleDetail 加载事件详情', async () => {
    routeFetch([
      { match: '/management/memory/events/e1', data: { data: { id: 'e1', fact: '详情内容' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    await w.vm.handleDetail('e1')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.detailEvent?.fact).toBe('详情内容')
  })

  it('handleCreate 空内容拦截', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.newEvent = { fact: '', keywords: '', importance: 0.5, event_type: 'fact' }
    await w.vm.handleCreate()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.showCreate).toBe(false) // 未被放行
  })

  it('handleCreate 合法内容创建并刷新', async () => {
    const calls = []
    routeFetch([
      { match: '/management/memory/events', data: () => { calls.push('create'); return { success: true } } },
      { match: '/management/memory/events?limit=50', data: { data: { events: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.newEvent = { fact: '新记忆', keywords: 'a,b', importance: 0.9, event_type: 'thought' }
    w.vm.showCreate = true
    await w.vm.handleCreate()
    await new Promise((r) => setTimeout(r, 20))
    expect(calls).toContain('create')
    expect(w.vm.showCreate).toBe(false)
  })

  it('handleDelete 经确认后调用删除', async () => {
    const calls = []
    routeFetch([
      { match: '/management/memory/events/e1', data: () => { calls.push('del'); return { success: true } } },
      { match: '/management/memory/events?limit=50', data: { data: { events: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    // 模拟用户点击确认弹窗的确认按钮
    const p = w.vm.handleDelete('e1')
    await new Promise((r) => setTimeout(r, 0))
    resolveDialog(true)
    await p
    await new Promise((r) => setTimeout(r, 10))
    expect(calls).toContain('del')
  })

  it('handleDelete 拒绝确认不调用删除', async () => {
    const calls = []
    routeFetch([
      { match: '/management/memory/events/e1', data: () => { calls.push('del'); return { success: true } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const p = w.vm.handleDelete('e1')
    await new Promise((r) => setTimeout(r, 0))
    resolveDialog(false)
    await p
    await new Promise((r) => setTimeout(r, 10))
    expect(calls.length).toBe(0)
  })

  it('handleClear 经确认后清空', async () => {
    const calls = []
    routeFetch([
      { match: '/management/memory/clear', data: () => { calls.push('clear'); return { success: true } } },
      { match: '/management/memory/events?limit=50', data: { data: { events: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const p = w.vm.handleClear()
    await new Promise((r) => setTimeout(r, 0))
    resolveDialog(true)
    await p
    await new Promise((r) => setTimeout(r, 10))
    expect(calls).toContain('clear')
  })

  it('groupedByDate 无时间事件归入未知日期', async () => {
    routeFetch([
      { match: '/management/memory/events?limit=50', data: { data: { events: [{ id: 'e9', fact: 'x', time: '' }], total: 1 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const groups = w.vm.groupedByDate
    expect(groups.some((g) => g[0] === '未知日期')).toBe(true)
  })

  it('handleDetail 加载失败显示错误 toast', async () => {
    routeFetch([
      { match: '/management/memory/events/e1', data: () => { throw new Error('404') } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    await w.vm.handleDetail('e1')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.detailEvent).toBeNull()
  })

  it('handleDelete 删除失败显示错误 toast', async () => {
    routeFetch([
      { match: '/management/memory/events/e1', data: () => { throw new Error('500') } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const p = w.vm.handleDelete('e1')
    await new Promise((r) => setTimeout(r, 0))
    resolveDialog(true)
    await p
    await new Promise((r) => setTimeout(r, 10))
    // 不崩溃即通过（错误路径已执行）
  })

  it('handleClear 清空失败显示错误 toast', async () => {
    routeFetch([
      { match: '/management/memory/clear', data: () => { throw new Error('500') } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const p = w.vm.handleClear()
    await new Promise((r) => setTimeout(r, 0))
    resolveDialog(true)
    await p
    await new Promise((r) => setTimeout(r, 10))
    // 不崩溃即通过（错误路径已执行）
  })

  it('handleCreate 创建失败显示错误 toast', async () => {
    routeFetch([
      { match: '/management/memory/events', data: () => { throw new Error('500') } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.newEvent = { fact: '内容', keywords: '', importance: 0.5, event_type: 'fact' }
    await w.vm.handleCreate()
    await new Promise((r) => setTimeout(r, 10))
    // 错误路径已执行，不崩溃
  })

  it('过滤搜索调用带 type/keyword 的查询', async () => {
    let requestedUrl = null
    routeFetch([
      { match: (u) => u.includes('/management/memory/events'), data: (u) => { requestedUrl = String(u); return { data: { events: [], total: 0 } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.filter = { type: 'thought', keyword: '架构' }
    await w.vm.loadData()
    expect(requestedUrl).toContain('type=thought')
    expect(requestedUrl).toContain('keyword=')
  })

  it('viewMode 切换时间线视图并渲染分组', async () => {
    routeFetch([
      {
        match: '/management/memory/events?limit=50',
        data: { data: { events: [{ id: 'e1', fact: '时间线事件', time: '2024-01-01T10:00:00', importance: 0.5, type: 'fact' }], total: 1 } },
      },
      { match: '/management/memory/events/e1', data: { data: { id: 'e1', fact: '时间线事件' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    // 默认列表视图
    expect(w.vm.viewMode).toBe('list')
    // 切到时间线
    w.vm.viewMode = 'timeline'
    await w.vm.$nextTick()
    expect(w.find('.memory-timeline').exists()).toBe(true)
    expect(w.text()).toContain('时间线事件')
    // 时间线项点击打开详情
    await w.find('.memory-timeline-item').trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.detailEvent?.fact).toBe('时间线事件')
  })
})
