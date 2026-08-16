import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import Memory from './Memory.vue'
import Modal from '@/components/Modal.vue'
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
    const { useToastStore } = await import('@/stores/toast.js')
    expect(useToastStore().toasts.some((t) => t.msg.includes('删除失败'))).toBe(true)
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
    const { useToastStore } = await import('@/stores/toast.js')
    expect(useToastStore().toasts.some((t) => t.msg.includes('清空失败'))).toBe(true)
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
    const { useToastStore } = await import('@/stores/toast.js')
    expect(useToastStore().toasts.some((t) => t.msg.includes('创建失败'))).toBe(true)
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

  it('DOM：搜索按钮触发查询 + 新建按钮打开 Modal 并创建', async () => {
    const calls = []
    routeFetch([
      { match: '/management/memory/events?limit=50', data: () => { calls.push('search'); return { data: { events: [], total: 0 } } } },
      { match: '/management/memory/events', data: (u, init) => {
        if (init?.method === 'POST') { calls.push('create'); return { success: true } }
        return { data: { events: [], total: 0 } }
      } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    // 搜索按钮 → 触发 loadData
    const searchBtn = w.findAll('button').find((b) => b.text().includes('搜索'))
    await searchBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 20))
    expect(calls).toContain('search')
    // 新建按钮 → 打开 Modal
    const newBtn = w.findAll('button').find((b) => b.text().includes('新建'))
    await newBtn.trigger('click')
    expect(w.vm.showCreate).toBe(true)
    // 填入内容并点创建 → POST
    const textarea = w.find('textarea')
    await textarea.setValue('DOM 创建记忆')
    const createBtn = w.findAll('button').find((b) => b.text().trim() === '创建')
    await createBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 20))
    expect(calls).toContain('create')
    expect(w.vm.showCreate).toBe(false)
  })

  it('DOM：视图切换按钮 + 列表详情/删除按钮 + 时间线删除', async () => {
    const calls = []
    routeFetch([
      { match: '/management/memory/events?limit=50', data: { data: { events: [{ id: 'e1', fact: '事件A', time: '2024-01-01T10:00:00', importance: 0.5, type: 'fact' }], total: 1 } } },
      { match: '/management/memory/events/e1', data: (u, init) => {
        if (init?.method === 'DELETE') { calls.push('del'); return { success: true } }
        return { data: { id: 'e1', fact: '事件A', type: 'fact', importance: 0.5, time: '2024-01-01T10:00:00' } }
      } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    // 列表→时间线 按钮
    const timelineBtn = w.findAll('button').find((b) => b.text().trim() === '时间线')
    await timelineBtn.trigger('click')
    expect(w.vm.viewMode).toBe('timeline')
    expect(w.find('.memory-timeline').exists()).toBe(true)
    // 时间线删除按钮（@click.stop）
    const tDel = w.find('.memory-timeline-item button')
    await tDel.trigger('click')
    await new Promise((r) => setTimeout(r, 0))
    resolveDialog(true)
    await new Promise((r) => setTimeout(r, 10))
    expect(calls).toContain('del')
    // 切回列表
    const listBtn = w.findAll('button').find((b) => b.text().trim() === '列表')
    await listBtn.trigger('click')
    expect(w.vm.viewMode).toBe('list')
    // 列表详情按钮
    const detailBtn = w.findAll('button').find((b) => b.text().includes('详情'))
    await detailBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.detailEvent?.fact).toBe('事件A')
    // 详情 Modal 关闭按钮
    const closeBtn = w.findAll('button').find((b) => b.text().trim() === '关闭')
    await closeBtn.trigger('click')
    expect(w.vm.detailEvent).toBeNull()
  })

  it('DOM：搜索输入与类型下拉绑定 filter', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const keywordInput = w.find('.search-input')
    await keywordInput.setValue('架构')
    expect(w.vm.filter.keyword).toBe('架构')
    const sel = w.find('.search-bar select')
    await sel.setValue('thought')
    expect(w.vm.filter.type).toBe('thought')
  })

  it('DOM：新建 Modal 字段编辑 + close 事件 + 详情 keywords 渲染', async () => {
    routeFetch([
      { match: '/management/memory/events?limit=50', data: { data: { events: [], total: 0 } } },
      { match: '/management/memory/events/e1', data: { data: { id: 'e1', fact: 'X', type: 'fact', importance: 0.5, keywords: ['k1', 'k2'], time: '2024-01-01T00:00:00' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.showCreate = true
    await w.vm.$nextTick()
    const modal = w.findComponent(Modal)
    // 新建 Modal 内类型下拉 / 关键词 / 重要性
    const selects = modal.findAll('select')
    await selects[0].setValue('thought')
    expect(w.vm.newEvent.event_type).toBe('thought')
    const inputs = modal.findAll('input')
    await inputs[0].setValue('k1,k2')
    await inputs[1].setValue('0.9')
    expect(w.vm.newEvent.keywords).toBe('k1,k2')
    expect(w.vm.newEvent.importance).toBe(0.9)
    // Modal @close 事件
    await modal.vm.$emit('close')
    await w.vm.$nextTick()
    expect(w.vm.showCreate).toBe(false)
    // 详情 keywords v-for
    await w.vm.handleDetail('e1')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.find('.detail-table').text()).toContain('k1')
    expect(w.find('.detail-table').text()).toContain('k2')
  })
})
