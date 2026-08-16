import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import { endpoints } from '@/api.js'
import { useHealthStore } from '@/stores/health.js'
import StatusBar from './StatusBar.vue'

describe('StatusBar', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('healthy 状态显示系统健康', async () => {
    vi.spyOn(endpoints, 'health').mockResolvedValue({ data: { status: 'healthy', checks: {} } })
    vi.spyOn(endpoints, 'modules').mockResolvedValue({ data: { modules: [1] } })
    const w = mount(StatusBar, { global: { plugins: [createTestPinia()] } })
    await vi.advanceTimersByTimeAsync(0)
    expect(w.text()).toContain('系统健康')
  })

  it('degraded 状态显示系统降级', async () => {
    vi.spyOn(endpoints, 'health').mockResolvedValue({ data: { status: 'degraded', checks: {} } })
    vi.spyOn(endpoints, 'modules').mockRejectedValue(new Error('x'))
    const w = mount(StatusBar, { global: { plugins: [createTestPinia()] } })
    await vi.advanceTimersByTimeAsync(0)
    expect(w.text()).toContain('系统降级')
  })

  it('offline 状态显示未连接', async () => {
    vi.spyOn(endpoints, 'health').mockRejectedValue(new Error('x'))
    const w = mount(StatusBar, { global: { plugins: [createTestPinia()] } })
    await vi.advanceTimersByTimeAsync(0)
    expect(w.text()).toContain('未连接')
  })

  it('卸载时停止健康轮询（onUnmounted 清理）', async () => {
    vi.spyOn(endpoints, 'health').mockResolvedValue({ data: { status: 'healthy', checks: {} } })
    const pinia = createTestPinia()
    const healthStore = useHealthStore()
    const stopSpy = vi.spyOn(healthStore, 'stop')
    const w = mount(StatusBar, { global: { plugins: [pinia] } })
    await vi.advanceTimersByTimeAsync(0)
    w.unmount()
    expect(stopSpy).toHaveBeenCalled()
  })
})
