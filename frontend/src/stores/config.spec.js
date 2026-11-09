import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useConfigStore } from './config.js'
import { endpoints } from '@/api.js'
import { wsClient } from '@/ws/client.js'

describe('useConfigStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('loadConfig 填充配置', async () => {
    const spy = vi.spyOn(endpoints, 'config').mockResolvedValue({ data: { EXECUTION_MODE: 'edit' } })
    const c = useConfigStore()
    await c.loadConfig()
    expect(c.config.EXECUTION_MODE).toBe('edit')
    spy.mockRestore()
  })

  it('loadConfig 失败不抛', async () => {
    const spy = vi.spyOn(endpoints, 'config').mockRejectedValue(new Error('x'))
    const c = useConfigStore()
    await expect(c.loadConfig()).resolves.toBeUndefined()
    spy.mockRestore()
  })

  it('loadModelStatus 填充模型状态', async () => {
    const spy = vi.spyOn(endpoints, 'thinkingStatus').mockResolvedValue({ data: { models: { big: true } } })
    const c = useConfigStore()
    await c.loadModelStatus()
    expect(c.modelStatus).toEqual({ big: true })
    spy.mockRestore()
  })

  it('updateConfig 调用更新后重新加载', async () => {
    const spyUp = vi.spyOn(endpoints, 'updateConfig').mockResolvedValue({})
    const spyLd = vi.spyOn(endpoints, 'config').mockResolvedValue({ data: { K: 'v' } })
    const c = useConfigStore()
    await c.updateConfig('K', 'v')
    expect(spyUp).toHaveBeenCalledWith('K', 'v')
    expect(c.config.K).toBe('v')
    spyUp.mockRestore(); spyLd.mockRestore()
  })

  it('切换 CORTEX_MODE 会断开旧 WS，下一条消息按新模式重连', async () => {
    const spyUp = vi.spyOn(endpoints, 'updateConfig').mockResolvedValue({})
    const spyLd = vi.spyOn(endpoints, 'config').mockResolvedValue({ data: { CORTEX_MODE: 'agent' } })
    const spyDisconnect = vi.spyOn(wsClient, 'disconnectAll').mockImplementation(() => {})
    const c = useConfigStore()
    await c.updateConfig('CORTEX_MODE', 'agent')
    expect(spyDisconnect).toHaveBeenCalledTimes(1)
    expect(c.config.CORTEX_MODE).toBe('agent')
    spyUp.mockRestore(); spyLd.mockRestore(); spyDisconnect.mockRestore()
  })

  it('切换大小写不同的 cortex_mode 也能断开旧 WS', async () => {
    vi.spyOn(endpoints, 'updateConfig').mockResolvedValue({})
    vi.spyOn(endpoints, 'config').mockResolvedValue({ data: {} })
    const spyDisconnect = vi.spyOn(wsClient, 'disconnectAll').mockImplementation(() => {})
    await useConfigStore().updateConfig('cortex_mode', 'chatonly')
    expect(spyDisconnect).toHaveBeenCalledTimes(1)
  })

  it('普通配置更新不会断开 WS', async () => {
    vi.spyOn(endpoints, 'updateConfig').mockResolvedValue({})
    vi.spyOn(endpoints, 'config').mockResolvedValue({ data: {} })
    const spyDisconnect = vi.spyOn(wsClient, 'disconnectAll').mockImplementation(() => {})
    await useConfigStore().updateConfig('EXECUTION_MODE', 'yolo')
    expect(spyDisconnect).not.toHaveBeenCalled()
  })
})
