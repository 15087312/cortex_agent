import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Outreach from './Outreach.vue'
import { routeFetch } from '@/test/helpers.js'

let wrapper = null
function mountPage() {
  wrapper = mount(Outreach, {
    global: { plugins: [createPinia()] },
  })
  return wrapper
}

describe('Outreach 页面', () => {
  afterEach(() => {
    // 卸载组件 → onBeforeUnmount 清理 30s 轮询定时器
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })
  it('加载会话并映射 outreach 配置', async () => {
    routeFetch([
      {
        match: '/stream/sessions',
        data: {
          data: [
            {
              session_id: 's1',
              title: '会话1',
              metadata: {
                outreach: {
                  enabled: true,
                  cooldown_minutes: 15,
                  screen: { enabled: true, change_ratio: 0.7, probability: 0.4, check_interval_seconds: 20, cooldown_minutes: 10 },
                  idle: { enabled: true, idle_minutes: 45, probability: 0.6, check_interval_seconds: 90 },
                  schedule: { enabled: true, time: '08:00', jitter_minutes: 5 },
                  time_windows_enabled: true,
                  time_windows: [{ start: '09:00', end: '18:00', probability: 0.5 }],
                },
              },
            },
            { session_id: 's2', title: '会话2' },
          ],
        },
      },
      { match: '/stream/proactive-log?limit=50', data: { data: { logs: [{ id: 'l1' }], total: 1 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.sessions.length).toBe(2)
    const s1 = w.vm.sessions[0]
    expect(s1.enabled).toBe(true)
    expect(s1.cooldownMin).toBe(15)
    expect(s1.screenRatio).toBe(0.7)
    expect(s1.idleMinutes).toBe(45)
    expect(s1.scheduleTime).toBe('08:00')
    expect(s1.timeWindowsText).toBe('09:00-18:00@0.5')
    expect(w.vm.enabledCount).toBe(1)
    expect(w.vm.totalLogs).toBe(1)
  })

  it('saveConfig 序列化配置并提交', async () => {
    let body = null
    routeFetch([
      { match: '/stream/session/s1/outreach-config', data: (u, init) => { body = JSON.parse(init.body); return { success: true } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const s = {
      session_id: 's1',
      enabled: true,
      cooldownMin: 30,
      scheduleOn: true,
      scheduleTime: '10:00',
      scheduleJitter: 0,
      screenOn: true,
      screenRatio: 0.5,
      screenProb: 0.5,
      screenInterval: 30,
      screenCooldown: 30,
      idleOn: false,
      idleMinutes: 30,
      idleProb: 0.5,
      idleInterval: 60,
      windowsOn: true,
      timeWindowsText: '09:00-18:00@0.8,20:00-22:00',
    }
    await w.vm.saveConfig(s)
    await new Promise((r) => setTimeout(r, 10))
    expect(body).not.toBeNull()
    const cfg = body.outreach
    expect(cfg.enabled).toBe(true)
    expect(cfg.schedule.time).toBe('10:00')
    expect(cfg.screen.change_ratio).toBe(0.5)
    expect(cfg.idle.enabled).toBe(false)
    expect(cfg.time_windows).toEqual([
      { start: '09:00', end: '18:00', probability: 0.8 },
      { start: '20:00', end: '22:00' },
    ])
  })

  it('saveConfig 边界钳制（比例/时长）', async () => {
    let body = null
    routeFetch([
      { match: '/stream/session/s1/outreach-config', data: (u, init) => { body = JSON.parse(init.body); return { success: true } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    const s = {
      session_id: 's1', enabled: true, cooldownMin: -5,
      scheduleOn: false, screenOn: true, screenRatio: 3, screenProb: -1,
      screenInterval: -5, screenCooldown: -2, idleOn: true, idleMinutes: -3,
      idleProb: 9, idleInterval: -1, windowsOn: false, timeWindowsText: 'bad',
    }
    await w.vm.saveConfig(s)
    await new Promise((r) => setTimeout(r, 10))
    const cfg = body.outreach
    expect(cfg.cooldown_minutes).toBe(0)
    expect(cfg.screen.change_ratio).toBe(1)
    expect(cfg.screen.probability).toBe(0)
    expect(cfg.screen.check_interval_seconds).toBe(1)
    expect(cfg.screen.cooldown_minutes).toBe(0)
    expect(cfg.idle.idle_minutes).toBe(0)
    expect(cfg.idle.probability).toBe(1)
    expect(cfg.idle.check_interval_seconds).toBe(1)
    expect(cfg.time_windows).toEqual([])
  })

  it('reasonLabels 映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.reasonLabels.schedule).toBe('定点发送')
    expect(w.vm.reasonLabels.screen).toBe('屏幕变化')
  })

  it('loadAll 失败不崩溃', async () => {
    routeFetch([
      { match: '/stream/sessions', data: () => { throw new Error('net') } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.loading).toBe(false)
    expect(w.vm.sessions).toHaveLength(0)
  })

  it('模板交互：展开/收起会话规则', async () => {
    routeFetch([
      {
        match: '/stream/sessions',
        data: { data: [{ session_id: 's1', title: '会话1', metadata: { outreach: { enabled: true } } }] },
      },
      { match: '/stream/proactive-log?limit=50', data: { data: { logs: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.sessions[0]._open).toBe(false)
    // 点击头部展开
    await w.find('.outreach-head').trigger('click')
    expect(w.vm.sessions[0]._open).toBe(true)
    // 展开后显示保存按钮
    const saveBtn = w.findAll('button').find((b) => b.text().includes('保存'))
    expect(saveBtn.exists()).toBe(true)
  })

  it('模板交互：切换启用开关触发 saveConfig', async () => {
    let saved = 0
    routeFetch([
      {
        match: '/stream/sessions',
        data: { data: [{ session_id: 's1', title: '会话1', metadata: { outreach: { enabled: false } } }] },
      },
      { match: '/stream/proactive-log?limit=50', data: { data: { logs: [], total: 0 } } },
      { match: '/stream/session/s1/outreach-config', data: () => { saved++; return { success: true } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    const cb = w.find('.outreach-head input[type=checkbox]')
    await cb.setValue(true)
    await new Promise((r) => setTimeout(r, 20))
    expect(saved).toBe(1)
    expect(w.vm.sessions[0].enabled).toBe(true)
  })

  it('卸载时清理轮询定时器', async () => {
    routeFetch([
      { match: '/stream/sessions', data: { data: [] } },
      { match: '/stream/proactive-log?limit=50', data: { data: { logs: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    w.unmount()
    // 不崩溃即通过（onBeforeUnmount 清理 timer）
  })
})
