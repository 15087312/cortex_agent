import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Perception from './Perception.vue'
import { routeFetch } from '@/test/helpers.js'

let wrapper = null
function mountPage() {
  wrapper = mount(Perception, {
    global: { plugins: [createPinia()] },
  })
  return wrapper
}

describe('Perception 页面', () => {
  afterEach(() => {
    // 卸载组件 → onUnmounted 清理轮询定时器
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })
  it('加载状态并渲染运行中指示与检测器', async () => {
    routeFetch([
      {
        match: '/management/perception',
        data: {
          data: {
            status: 'running',
            world_state: { active_app: 'Chrome', active_window: '测试' },
            pipeline: { screen: 'ok', file: 'error' },
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    await w.vm.$nextTick()
    expect(w.text()).toContain('Chrome')
    // 检测器列表：screen ok / file 失败
    const items = w.findAll('.pipeline-card')
    expect(items.length).toBeGreaterThanOrEqual(1)
    expect(items[0].text()).toContain('screen')
  })

  it('start/stop 调用对应端点并刷新', async () => {
    const calls = []
    routeFetch([
      { match: '/management/perception/start', data: () => { calls.push('start'); return { success: true } } },
      { match: '/management/perception/stop', data: () => { calls.push('stop'); return { success: true } } },
      { match: '/management/perception', data: { data: { status: 'running' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    await w.vm.$nextTick()
    const startBtn = w.findAll('button').find((b) => b.text().includes('启动'))
    if (startBtn) await startBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    const stopBtn = w.findAll('button').find((b) => b.text().includes('停止'))
    if (stopBtn) await stopBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(calls.some((c) => c === 'start') || calls.some((c) => c === 'stop')).toBe(true)
  })

  it('running computed 基于 status', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20)) // 等待 onMounted refresh 完成
    await w.vm.$nextTick()
    expect(w.vm.running).toBe(false)
    w.vm.status = { status: 'running' }
    await w.vm.$nextTick()
    expect(w.vm.running).toBe(true)
  })
})
