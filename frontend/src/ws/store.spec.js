import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useWsStore } from './store.js'
import { wsClient } from './client.js'

describe('useWsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('startStreaming/appendStreaming/finishStreaming 流式状态', () => {
    const s = useWsStore()
    s.startStreaming()
    expect(s.isStreaming).toBe(true)
    s.appendStreaming('hello ')
    s.appendStreaming('world')
    const final = s.finishStreaming()
    expect(final).toBe('hello world')
    expect(s.isStreaming).toBe(false)
    expect(s.streamingContent).toBe('')
  })

  it('finishStreaming 可覆盖内容', () => {
    const s = useWsStore()
    s.startStreaming()
    s.appendStreaming('a')
    expect(s.finishStreaming('b')).toBe('b')
  })

  it('reset 清空状态', () => {
    const s = useWsStore()
    s.startStreaming()
    s.appendStreaming('x')
    s.reset()
    expect(s.isStreaming).toBe(false)
    expect(s.streamingContent).toBe('')
  })

  it('connect/disconnect 代理到 wsClient', () => {
    const s = useWsStore()
    const connectSpy = vi.spyOn(wsClient, 'connect').mockResolvedValue()
    const disconnectSpy = vi.spyOn(wsClient, 'disconnect').mockImplementation(() => {})
    s.connect('s1')
    expect(connectSpy).toHaveBeenCalledWith('s1')
    s.disconnect()
    expect(disconnectSpy).toHaveBeenCalled()
    expect(s.isConnected).toBe(false)
    vi.restoreAllMocks()
  })
})
