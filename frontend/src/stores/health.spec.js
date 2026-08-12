import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useHealthStore } from './health.js'
import { endpoints } from '@/api.js'

describe('useHealthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('check 健康状态 + 模块数', async () => {
    const spyH = vi.spyOn(endpoints, 'health').mockResolvedValue({ data: { status: 'healthy', checks: { a: 'ok' } } })
    const spyM = vi.spyOn(endpoints, 'modules').mockResolvedValue({ data: { modules: [1, 2, 3] } })
    const h = useHealthStore()
    await h.check()
    expect(h.status).toBe('healthy')
    expect(h.backendText).toContain('healthy')
    expect(h.moduleCount).toBe('3')
    spyH.mockRestore(); spyM.mockRestore()
  })

  it('check degraded 状态', async () => {
    const spyH = vi.spyOn(endpoints, 'health').mockResolvedValue({ data: { status: 'degraded', checks: {} } })
    const spyM = vi.spyOn(endpoints, 'modules').mockRejectedValue(new Error('x'))
    const h = useHealthStore()
    await h.check()
    expect(h.status).toBe('degraded')
    spyH.mockRestore(); spyM.mockRestore()
  })

  it('check 失败置 offline', async () => {
    const spyH = vi.spyOn(endpoints, 'health').mockRejectedValue(new Error('x'))
    const h = useHealthStore()
    await h.check()
    expect(h.status).toBe('offline')
    expect(h.backendText).toBe('')
    spyH.mockRestore()
  })

  it('startPolling 定时检查 + stop 清理', async () => {
    const spyH = vi.spyOn(endpoints, 'health').mockResolvedValue({ data: { status: 'healthy', checks: {} } })
    const spyM = vi.spyOn(endpoints, 'modules').mockResolvedValue({ data: { modules: [] } })
    const h = useHealthStore()
    h.startPolling()
    await Promise.resolve()
    const c1 = spyH.mock.calls.length
    vi.advanceTimersByTime(15000)
    expect(spyH.mock.calls.length).toBeGreaterThan(c1)
    h.stop()
    const c2 = spyH.mock.calls.length
    vi.advanceTimersByTime(15000)
    expect(spyH.mock.calls.length).toBe(c2)
    spyH.mockRestore(); spyM.mockRestore()
  })
})
