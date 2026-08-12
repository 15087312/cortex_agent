import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import SessionSettings from './SessionSettings.vue'

const outreachData = {
  data: {
    outreach: {
      enabled: true,
      cooldown_minutes: 15,
      schedule: { enabled: true, time: '14:00', jitter_minutes: 5 },
      screen: { enabled: false, change_ratio: 0.5, probability: 0.5, check_interval_seconds: 30, cooldown_minutes: 30 },
      idle: { enabled: true, idle_minutes: 45, probability: 0.3, check_interval_seconds: 60 },
      time_windows_enabled: true,
      time_windows: [{ start: '09:00', end: '12:00', probability: 0.5 }],
    },
  },
}

const ok = (data) => ({ ok: true, status: 200, json: async () => data, text: async () => '' })

function defaultRoutes(overrides = {}) {
  return vi.fn(async (url, opts) => {
    if (url.includes('/outreach-config')) return ok(overrides.outreach ?? outreachData)
    if (url.includes('/tasks')) return ok(overrides.tasks ?? { data: { tasks: { tasks: [] } } })
    if (url.includes('/orchestration')) return ok({ data: { agents: [] } })
    return ok({})
  })
}

function mountSettings(fetchMock) {
  globalThis.fetch = fetchMock || defaultRoutes()
  return mount(SessionSettings, {
    props: { sessionId: 's1', title: '测试会话' },
    global: { plugins: [createTestPinia()] },
  })
}

describe('SessionSettings', () => {
  it('加载主动搭话配置并渲染', async () => {
    const w = mountSettings()
    await flushPromises()
    expect(w.text()).toContain('会话设置：测试会话')
    expect(w.text()).toContain('开启主动搭话')
    const enabledCb = w.find('input[type=checkbox]')
    expect(enabledCb.element.checked).toBe(true)
  })

  it('保存主动搭话配置', async () => {
    const putCalls = []
    const fetchMock = vi.fn(async (url, opts) => {
      if (opts?.method === 'PUT' && url.includes('/outreach-config')) {
        putCalls.push([url, opts])
        return ok({ success: true })
      }
      if (url.includes('/outreach-config')) return ok(outreachData)
      if (url.includes('/tasks')) return ok({ data: { tasks: { tasks: [] } } })
      return ok({ data: { agents: [] } })
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const saveBtn = w.findAll('button').find(b => b.text().includes('保存主动搭话'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(putCalls.length).toBe(1)
    const body = JSON.parse(putCalls[0][1].body)
    expect(body.outreach.enabled).toBe(true)
    expect(body.outreach.cooldown_minutes).toBe(15)
    expect(body.outreach.schedule.time).toBe('14:00')
    expect(body.outreach.time_windows[0].start).toBe('09:00')
  })

  it('定时任务 tab：添加任务并保存', async () => {
    const putCalls = []
    const fetchMock = vi.fn(async (url, opts) => {
      if (opts?.method === 'PUT' && url.includes('/tasks')) {
        putCalls.push([url, opts])
        return ok({ success: true })
      }
      if (url.includes('/outreach-config')) return ok(outreachData)
      if (url.includes('/tasks')) return ok({ data: { tasks: { tasks: [] } } })
      return ok({ data: { agents: [] } })
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const tasksTab = w.findAll('.seg button').find(b => b.text().includes('定时任务'))
    await tasksTab.trigger('click')
    const addBtn = w.findAll('button').find(b => b.text().includes('添加任务'))
    await addBtn.trigger('click')
    // 新任务渲染出类型选择与提示词输入（placeholder 不计入 text，检查 textarea 存在）
    expect(w.find('textarea').exists()).toBe(true)
    expect(w.findAll('select').length).toBeGreaterThanOrEqual(2)
    const saveBtn = w.findAll('button').find(b => b.text().includes('保存定时任务'))
    await saveBtn.trigger('click')
    await flushPromises()
    expect(putCalls.length).toBe(1)
    const body = JSON.parse(putCalls[0][1].body)
    expect(body.tasks.tasks.length).toBe(1)
    expect(body.tasks.tasks[0].schedule).toBe('09:00')
  })

  it('已有任务按类型解析并展示', async () => {
    const fetchMock = defaultRoutes({
      tasks: { data: { tasks: { tasks: [{ id: 't1', time: '09:00', schedule: '09:00', enabled: true, action: 'chat' }] } } },
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const tasksTab = w.findAll('.seg button').find(b => b.text().includes('定时任务'))
    await tasksTab.trigger('click')
    // 任务已渲染：显示时间输入与启用开关（id 不直接展示）
    const timeInput = w.find('input[placeholder="HH:MM"]')
    expect(timeInput.exists()).toBe(true)
    expect(timeInput.element.value).toBe('09:00')
  })

  it('interval 任务类型切换生成 interval schedule', async () => {
    const fetchMock = defaultRoutes({
      tasks: { data: { tasks: { tasks: [{ id: 't1', time: '09:00', schedule: '09:00', enabled: true, action: 'chat' }] } } },
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const tasksTab = w.findAll('.seg button').find(b => b.text().includes('定时任务'))
    await tasksTab.trigger('click')
    // 切换类型为 interval
    const select = w.find('select')
    await select.setValue('interval')
    expect(w.text()).toContain('分钟')
  })

  it('关闭按钮 emit close', async () => {
    const w = mountSettings()
    await flushPromises()
    const closeBtn = w.findAll('button').find(b => b.text().includes('关闭'))
    await closeBtn.trigger('click')
    expect(w.emitted('close')).toHaveLength(1)
  })

  it('加载失败不崩溃', async () => {
    globalThis.fetch = vi.fn(async () => { throw new Error('net') })
    const w = mountSettings()
    await flushPromises()
    expect(w.text()).toContain('会话设置')
  })

  it('saveTasks 归一化多类型任务 schedule', async () => {
    const putCalls = []
    const fetchMock = vi.fn(async (url, opts) => {
      if (opts?.method === 'PUT' && url.includes('/tasks')) {
        putCalls.push([url, opts])
        return ok({ success: true })
      }
      if (url.includes('/outreach-config')) return ok(outreachData)
      if (url.includes('/tasks')) return ok({ data: { tasks: { tasks: [] } } })
      return ok({ data: { agents: [] } })
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    // 直接构造 3 个不同类型任务（避免 DOM v-for 竞态）
    w.vm.tasks = [
      { id: 't1', type: 'daily', time: '09:00', schedule: '09:00', enabled: true, action: 'chat', prompt: '' },
      { id: 't2', type: 'interval', every_minutes: 30, schedule: { kind: 'interval', every_minutes: 30 }, enabled: true, action: 'chat', prompt: '' },
      { id: 't3', type: 'cron', expr: '* * * * *', schedule: { kind: 'cron', expr: '* * * * *' }, enabled: true, action: 'chat', prompt: '' },
    ]
    await w.vm.saveTasks()
    await flushPromises()
    const body = JSON.parse(putCalls[0][1].body)
    expect(body.tasks.tasks).toHaveLength(3)
    const scheds = body.tasks.tasks.map(t => t.schedule)
    expect(scheds[0]).toBe('09:00')
    expect(scheds[1]).toEqual({ kind: 'interval', every_minutes: 30 })
    expect(scheds[2]).toEqual({ kind: 'cron', expr: '* * * * *' })
  })

  it('removeTask 移除任务', async () => {
    const fetchMock = defaultRoutes({
      tasks: { data: { tasks: { tasks: [{ id: 't1', time: '09:00', schedule: '09:00', enabled: true, action: 'chat' }] } } },
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const tasksTab = w.findAll('.seg button').find(b => b.text().includes('定时任务'))
    await tasksTab.trigger('click')
    expect(w.vm.tasks).toHaveLength(1)
    w.vm.removeTask(0)
    expect(w.vm.tasks).toHaveLength(0)
  })

  it('onTypeChange 更新 schedule 结构', () => {
    const w = mountSettings()
    const task = { type: 'once', at: '12:00' }
    w.vm.onTypeChange(task)
    expect(task.schedule).toEqual({ kind: 'once', at: '12:00' })
    task.type = 'interval'
    task.every_minutes = 15
    w.vm.onTypeChange(task)
    expect(task.schedule).toEqual({ kind: 'interval', every_minutes: 15 })
    task.type = 'cron'
    task.expr = '0 9 * * *'
    w.vm.onTypeChange(task)
    expect(task.schedule).toEqual({ kind: 'cron', expr: '0 9 * * *' })
    task.type = 'daily'
    task.time = '08:00'
    w.vm.onTypeChange(task)
    expect(task.schedule).toBe('08:00')
  })

  it('taskType 识别字符串与对象 schedule', () => {
    const w = mountSettings()
    expect(w.vm.taskType({ schedule: '09:00' })).toBe('daily')
    expect(w.vm.taskType({ schedule: { kind: 'interval' } })).toBe('interval')
    expect(w.vm.taskType({ schedule: { kind: 'once' } })).toBe('once')
    expect(w.vm.taskType({ schedule: { kind: 'cron' } })).toBe('cron')
    expect(w.vm.taskType({ time: '10:00' })).toBe('daily')
    expect(w.vm.taskType({})).toBe('daily')
  })

  it('scheduleOf 输出各类型 schedule 结构', () => {
    const w = mountSettings()
    expect(w.vm.scheduleOf({ schedule: '09:00', time: '09:00' })).toBe('09:00')
    expect(w.vm.scheduleOf({ schedule: { kind: 'interval' }, every_minutes: 20 })).toEqual({ kind: 'interval', every_minutes: 20 })
    expect(w.vm.scheduleOf({ schedule: { kind: 'once' }, at: '15:00' })).toEqual({ kind: 'once', at: '15:00' })
    expect(w.vm.scheduleOf({ schedule: { kind: 'cron' }, expr: '* * * * *' })).toEqual({ kind: 'cron', expr: '* * * * *' })
  })

  it('statusBadge 状态映射', () => {
    const w = mountSettings()
    expect(w.vm.statusBadge(null)).toBeNull()
    expect(w.vm.statusBadge('success')).toEqual({ color: '#3fb950', label: '成功' })
    expect(w.vm.statusBadge('error')).toEqual({ color: '#f85149', label: '错误' })
    expect(w.vm.statusBadge('unknown')).toEqual({ color: '#8b949e', label: 'unknown' })
  })

  it('DOM：保存主动搭话按钮触发 PUT 且序列化正确', async () => {
    const putCalls = []
    const fetchMock = vi.fn(async (url, opts) => {
      if (opts?.method === 'PUT' && url.includes('/outreach-config')) {
        putCalls.push([url, opts])
        return ok({ success: true })
      }
      if (url.includes('/outreach-config')) return ok(outreachData)
      if (url.includes('/tasks')) return ok({ data: { tasks: { tasks: [] } } })
      return ok({ data: { agents: [] } })
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const saveBtn = w.findAll('button').find((b) => b.text().includes('保存主动搭话'))
    expect(saveBtn.exists()).toBe(true)
    await saveBtn.trigger('click')
    await flushPromises()
    expect(putCalls.length).toBe(1)
    const body = JSON.parse(putCalls[0][1].body)
    expect(body.outreach.enabled).toBe(true)
    expect(body.outreach.cooldown_minutes).toBe(15)
    expect(body.outreach.schedule.time).toBe('14:00')
    expect(body.outreach.screen.enabled).toBe(false)
    expect(body.outreach.time_windows).toEqual([{ start: '09:00', end: '12:00', probability: 0.5 }])
  })

  it('DOM：切换 tab 到定时任务并渲染成功状态徽章', async () => {
    const fetchMock = defaultRoutes({
      tasks: { data: { tasks: { tasks: [{ id: 't1', time: '09:00', schedule: '09:00', enabled: true, action: 'chat', last_status: 'success', last_run: '2024-01-01 09:00' }] } } },
    })
    const w = mountSettings(fetchMock)
    await flushPromises()
    const tasksTab = w.findAll('.seg button').find((b) => b.text().includes('定时任务'))
    await tasksTab.trigger('click')
    // 成功徽章渲染（statusBadge 模板分支）
    expect(w.text()).toContain('成功')
    expect(w.text()).toContain('2024-01-01 09:00')
  })
})
