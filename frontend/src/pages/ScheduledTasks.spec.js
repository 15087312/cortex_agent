import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import ScheduledTasks from './ScheduledTasks.vue'
import { routeFetch } from '@/test/helpers.js'

function mountPage() {
  return mount(ScheduledTasks, {
    global: { plugins: [createPinia()] },
  })
}

describe('ScheduledTasks 页面', () => {
  it('加载会话与任务并标注类型', async () => {
    routeFetch([
      { match: '/management/orchestration', data: { data: { agents: [{ role: 'a1' }] } } },
      { match: '/stream/sessions', data: { data: [{ session_id: 's1', title: '会话A', last_active: '2024-01-02T00:00:00' }] } },
      {
        match: '/stream/session/s1/tasks',
        data: {
          data: {
            tasks: {
              tasks: [
                { id: 't1', time: '09:00', schedule: '09:00', action: 'chat', prompt: '早上好' },
                { id: 't2', schedule: { kind: 'interval', every_minutes: 30 } },
                { id: 't3', schedule: { kind: 'cron', expr: '* * * * *' } },
              ],
            },
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.selected).toBe('s1')
    expect(w.vm.tasks.length).toBe(3)
    expect(w.vm.tasks[0].type).toBe('daily')
    expect(w.vm.tasks[1].type).toBe('interval')
    expect(w.vm.tasks[2].type).toBe('cron')
    expect(w.text()).toContain('会话A')
    expect(w.text()).toContain('保存任务')
  })

  it('addTask/removeTask/onTypeChange 逻辑', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.tasks = []
    w.vm.addTask()
    expect(w.vm.tasks.length).toBe(1)
    const t = w.vm.tasks[0]
    expect(t.type).toBe('daily')
    t.type = 'interval'
    w.vm.onTypeChange(t)
    expect(t.schedule.kind).toBe('interval')
    t.type = 'once'
    w.vm.onTypeChange(t)
    expect(t.schedule.kind).toBe('once')
    t.type = 'cron'
    w.vm.onTypeChange(t)
    expect(t.schedule.kind).toBe('cron')
    w.vm.removeTask(0)
    expect(w.vm.tasks.length).toBe(0)
  })

  it('saveTasks 发送归一化载荷并 toast 成功', async () => {
    let body = null
    routeFetch([
      {
        match: '/stream/session/s1/tasks',
        data: (u, init) => {
          if (init && init.method === 'PUT') { body = JSON.parse(init.body); return { success: true } }
          return { data: { tasks: { tasks: [] } } }
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.selected = 's1'
    w.vm.tasks = [{ id: 't1', time: '09:00', schedule: '09:00', every_minutes: 30, at: '09:00', expr: '* * * * *', enabled: true, action: 'chat', prompt: 'hi', type: 'daily' }]
    await w.vm.saveTasks()
    await new Promise((r) => setTimeout(r, 10))
    expect(body).not.toBeNull()
    expect(body.tasks.tasks[0].schedule).toBe('09:00')
    expect(body.tasks.tasks[0].enabled).toBe(true)
    expect(w.vm.saving).toBe(false)
  })

  it('scheduleOf 类型映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.scheduleOf({ type: 'interval', every_minutes: 5, schedule: { kind: 'interval' } })).toEqual({ kind: 'interval', every_minutes: 5 })
    expect(w.vm.scheduleOf({ type: 'cron', expr: 'x', schedule: { kind: 'cron' } })).toEqual({ kind: 'cron', expr: 'x' })
    expect(w.vm.scheduleOf({ type: 'daily', time: '08:00', schedule: '09:00' })).toBe('08:00')
  })

  it('statusBadge 颜色映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.statusBadge('success')).toEqual({ color: '#3fb950', label: '成功' })
    expect(w.vm.statusBadge('error')).toEqual({ color: '#f85149', label: '错误' })
    expect(w.vm.statusBadge('pending')).toEqual({ color: '#8b949e', label: 'pending' })
    expect(w.vm.statusBadge(undefined)).toBeNull()
  })

  it('taskType 兜底分支', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.taskType({ schedule: '09:00' })).toBe('daily')
    expect(w.vm.taskType({ schedule: { kind: 'weird' } })).toBe('daily')
    expect(w.vm.taskType({ time: '10:00' })).toBe('daily')
    expect(w.vm.taskType({})).toBe('daily')
  })

  it('scheduleOf 兜底返回 09:00', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.scheduleOf({ type: 'daily' })).toBe('09:00')
  })

  it('onTypeChange daily 与兜底分支', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    const t = { type: 'daily', time: '07:30' }
    w.vm.onTypeChange(t)
    expect(t.schedule).toBe('07:30')
    const t2 = { type: 'daily' }
    w.vm.onTypeChange(t2)
    expect(t2.schedule).toBe('09:00')
  })

  it('loadTasks 未选会话直接返回', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    w.vm.selected = ''
    await w.vm.loadTasks()
    expect(w.vm.tasks).toHaveLength(0)
  })

  it('loadSessions 失败不崩溃且清空 loading', async () => {
    routeFetch([]) // 全部请求返回空
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.loading).toBe(false)
    expect(w.vm.sessions).toHaveLength(0)
  })

  it('saveTasks 失败显示错误 toast', async () => {
    routeFetch([
      { match: '/stream/session/s1/tasks', data: { success: false, error: { message: '后端错误' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    w.vm.selected = 's1'
    w.vm.tasks = [{ id: 't1', type: 'daily', schedule: '09:00', enabled: true, action: 'chat' }]
    await w.vm.saveTasks()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.saving).toBe(false)
  })

  it('loadTasks 请求失败时清空任务列表（catch 分支）', async () => {
    routeFetch([
      { match: '/stream/session/s1/tasks', data: () => { throw new Error('net') } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.selected = 's1'
    w.vm.tasks = [{ id: 'old' }]
    await w.vm.loadTasks()
    expect(w.vm.tasks).toHaveLength(0)
  })

  it('多会话按 last_active 倒序 + select 切换会话触发 loadTasks', async () => {
    const taskReq = []
    routeFetch([
      { match: '/management/orchestration', data: { data: { agents: [] } } },
      {
        match: '/stream/sessions',
        data: { data: [
          { session_id: 's2', title: '会话B', last_active: '2024-01-03T00:00:00' },
          { session_id: 's1', title: '会话A', last_active: '2024-01-02T00:00:00' },
        ] },
      },
      { match: '/stream/session/s1/tasks', data: () => { taskReq.push('s1'); return { data: { tasks: { tasks: [] } } } } },
      { match: '/stream/session/s2/tasks', data: () => { taskReq.push('s2'); return { data: { tasks: { tasks: [{ id: 't2', time: '10:00', schedule: '10:00' }] } } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    // 倒序：s2 最新排第一且默认选中
    expect(w.vm.sessions[0].session_id).toBe('s2')
    expect(w.vm.selected).toBe('s2')
    expect(taskReq).toContain('s2')
    // DOM 切换下拉框 → @change 触发 loadTasks
    const sel = w.find('select')
    await sel.setValue('s1')
    await new Promise((r) => setTimeout(r, 30))
    expect(taskReq).toContain('s1')
    expect(w.vm.selected).toBe('s1')
  })

  it('DOM：任务类型切换触发 onTypeChange 并渲染对应输入', async () => {
    routeFetch([
      { match: '/management/orchestration', data: { data: { agents: [{ role: 'chief', name: '总指挥' }] } } },
      { match: '/stream/sessions', data: { data: [{ session_id: 's1', title: '会话A', last_active: '2024-01-02T00:00:00' }] } },
      { match: '/stream/session/s1/tasks', data: { data: { tasks: { tasks: [] } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    // 点「添加任务」→ DOM 渲染 daily 类型输入（HH:MM）
    const addBtn = w.findAll('button').find((b) => b.text().includes('添加任务'))
    await addBtn.trigger('click')
    await w.vm.$nextTick()
    expect(w.vm.tasks).toHaveLength(1)
    expect(w.find('input[placeholder="HH:MM"]').exists()).toBe(true)
    // 切换类型 select → interval → schedule 结构更新 + 渲染分钟输入
    const typeSel = w.findAll('select')[1]
    await typeSel.setValue('interval')
    await w.vm.$nextTick()
    expect(w.vm.tasks[0].type).toBe('interval')
    expect(w.vm.tasks[0].schedule.kind).toBe('interval')
    expect(w.text()).toContain('分钟')
    // 切换 cron → 渲染表达式输入
    await typeSel.setValue('cron')
    await w.vm.$nextTick()
    expect(w.vm.tasks[0].schedule.kind).toBe('cron')
    expect(w.find('input[placeholder="分 时 日 月 周"]').exists()).toBe(true)
  })

  it('DOM：任务输入字段编辑 + 启用开关 + 下拉 + prompt + 删除按钮', async () => {
    routeFetch([
      { match: '/management/orchestration', data: { data: { agents: [{ role: 'chief', name: '总指挥' }] } } },
      { match: '/stream/sessions', data: { data: [{ session_id: 's1', title: '会话A', last_active: '2024-01-02T00:00:00' }] } },
      { match: '/stream/session/s1/tasks', data: { data: { tasks: { tasks: [] } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    const addBtn = w.findAll('button').find((b) => b.text().includes('添加任务'))
    await addBtn.trigger('click')
    await w.vm.$nextTick()
    // daily 时间输入 + @change 回写 schedule
    const timeInput = w.find('input[placeholder="HH:MM"]')
    await timeInput.setValue('08:30')
    await w.vm.$nextTick()
    expect(w.vm.tasks[0].time).toBe('08:30')
    expect(w.vm.tasks[0].schedule).toBe('08:30')
    // interval 分钟输入
    const typeSel = w.findAll('select')[1]
    await typeSel.setValue('interval')
    await w.vm.$nextTick()
    const minInput = w.find('input[type="number"]')
    await minInput.setValue('15')
    await w.vm.$nextTick()
    expect(w.vm.tasks[0].every_minutes).toBe(15)
    expect(w.vm.tasks[0].schedule.kind).toBe('interval')
    expect(w.vm.tasks[0].schedule.every_minutes).toBe(15)
    // once at 输入
    await typeSel.setValue('once')
    await w.vm.$nextTick()
    await w.find('input[placeholder="HH:MM"]').setValue('10:00')
    await w.vm.$nextTick()
    expect(w.vm.tasks[0].schedule.kind).toBe('once')
    expect(w.vm.tasks[0].schedule.at).toBe('10:00')
    // cron expr
    await typeSel.setValue('cron')
    await w.vm.$nextTick()
    await w.find('input[placeholder="分 时 日 月 周"]').setValue('0 9 * * *')
    await w.vm.$nextTick()
    expect(w.vm.tasks[0].schedule.kind).toBe('cron')
    expect(w.vm.tasks[0].schedule.expr).toBe('0 9 * * *')
    // 启用开关
    const cb = w.find('input[type="checkbox"]')
    await cb.setValue(false)
    expect(w.vm.tasks[0].enabled).toBe(false)
    // action / agent_type 下拉
    await w.findAll('select')[2].setValue('chat')
    await w.findAll('select')[3].setValue('chief')
    expect(w.vm.tasks[0].agent_type).toBe('chief')
    // prompt textarea
    await w.find('.task-textarea').setValue('定时提醒')
    expect(w.vm.tasks[0].prompt).toBe('定时提醒')
    // 删除按钮
    await w.find('.task-controls .danger').trigger('click')
    expect(w.vm.tasks).toHaveLength(0)
  })
})
