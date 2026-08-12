import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createTestPinia, stubFetch } from '@/test/helpers.js'
import { endpoints } from '@/api.js'
import SessionList from './SessionList.vue'

const sessions = [
  { session_id: 'a1', title: 'Alpha', last_active: '2024-01-01T10:00:00', message_count: 3 },
  { session_id: 'b2', title: 'Beta', last_active: '2024-01-02T10:00:00', message_count: 0 },
]

describe('SessionList', () => {
  beforeEach(() => {
    stubFetch({ success: true, data: { count: 2 } })
  })

  function mountList(props = {}) {
    return mount(SessionList, {
      props: { sessions, activeId: 'a1', collapsed: false, ...props },
      global: { plugins: [createTestPinia()] },
    })
  }

  it('渲染会话标题与数量', () => {
    const w = mountList()
    expect(w.text()).toContain('Alpha')
    expect(w.text()).toContain('Beta')
    expect(w.text()).toContain('3 条')
  })

  it('点击会话 emit select', async () => {
    const w = mountList()
    const items = w.findAll('.session-item')
    await items[1].trigger('click')
    expect(w.emitted('select')[0]).toEqual(['b2'])
  })

  it('搜索过滤', async () => {
    const w = mountList()
    await w.find('.session-search input').setValue('beta')
    expect(w.findAll('.session-item')).toHaveLength(1)
    expect(w.text()).toContain('Beta')
  })

  it('批量模式勾选并删除', async () => {
    const batchSpy = vi.spyOn(endpoints, 'batchDeleteSessions').mockResolvedValue({ data: { count: 2 } })
    const w = mountList()
    const btnText = (b) => b.text()
    const btns = w.findAll('button')
    const manageBtn = btns.find(b => btnText(b).includes('批量'))
    await manageBtn.trigger('click') // 进入批量模式
    expect(w.text()).toContain('点会话勾选')
    const cb = w.find('.session-item input[type=checkbox]')
    await cb.trigger('click') // 原生 click 自动切换 checked 并触发 change
    expect(w.text()).toContain('删除(1)')
    const delBtn = w.findAll('button').find(b => btnText(b).includes('删除'))
    await delBtn.trigger('click') // 删除按钮（触发 confirm 确认框）
    const { resolveDialog } = await import('@/composables/useDialog.js')
    resolveDialog(true) // 用户确认删除
    await flushPromises()
    expect(batchSpy).toHaveBeenCalledWith(['a1'])
    batchSpy.mockRestore()
  })

  it('新建会话 emit new', async () => {
    const w = mountList()
    const btns = w.findAll('button')
    const newBtn = btns.find(b => b.text().includes('新建会话'))
    await newBtn.trigger('click')
    expect(w.emitted('new')).toHaveLength(1)
  })

  it('重命名/删除按钮 emit', async () => {
    const w = mountList()
    const firstActions = w.find('.session-item .session-item-actions')
    const btns = firstActions.findAll('button')
    await btns[0].trigger('click')
    expect(w.emitted('rename')[0]).toEqual(['a1'])
    await btns[1].trigger('click')
    expect(w.emitted('delete')[0]).toEqual(['a1'])
  })

  it('右键删除 emit delete', async () => {
    const w = mountList()
    await w.findAll('.session-item')[0].trigger('contextmenu')
    expect(w.emitted('delete')[0]).toEqual(['a1'])
  })

  it('收起模式显示展开按钮', async () => {
    const w = mountList({ collapsed: true })
    expect(w.find('.session-expand-btn').exists()).toBe(true)
    await w.find('.session-expand-btn').trigger('click')
    expect(w.emitted('update:collapsed')[0]).toEqual([false])
  })

  it('空会话显示提示', () => {
    const w = mountList()
    expect(w.find('.chat-sessions-empty').exists()).toBe(false)
    const w2 = mount(SessionList, {
      props: { sessions: [], activeId: null, collapsed: false },
      global: { plugins: [createTestPinia()] },
    })
    expect(w2.text()).toContain('暂无会话')
  })

  it('toggleAll 全选/取消全选', async () => {
    const w = mountList()
    // 进入批量模式
    await w.findAll('button').find(b => b.text().includes('批量')).trigger('click')
    // 初始未选 → 全选
    w.vm.toggleAll()
    expect(w.vm.selected['a1']).toBe(true)
    expect(w.vm.selected['b2']).toBe(true)
    // 已全选 → 取消全选
    w.vm.toggleAll()
    expect(Object.keys(w.vm.selected)).toHaveLength(0)
  })

  it('toggleAll 空列表不误选', () => {
    const w = mount(SessionList, {
      props: { sessions: [], activeId: null, collapsed: false },
      global: { plugins: [createTestPinia()] },
    })
    w.vm.toggleAll()
    expect(Object.keys(w.vm.selected)).toHaveLength(0)
  })
})
