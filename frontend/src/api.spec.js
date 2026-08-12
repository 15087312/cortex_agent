import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import {
  request, createController, getApiKey, setApiKey, autoDetectApiKey, endpoints,
} from './api.js'

describe('api.js', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    setApiKey('')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  describe('request', () => {
    it('GET 请求拼接 BASE 前缀', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ data: 1 }) }))
      vi.stubGlobal('fetch', fetchMock)
      const r = await request('GET', '/health')
      expect(fetchMock).toHaveBeenCalledWith('/api/health', expect.objectContaining({ method: 'GET' }))
      expect(r).toEqual({ data: 1 })
    })

    it('携带 body 与 signal', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      const ac = new AbortController()
      await request('POST', '/x', { a: 1 }, ac.signal)
      const [, opts] = fetchMock.mock.calls[0]
      expect(JSON.parse(opts.body)).toEqual({ a: 1 })
      expect(opts.signal).toBe(ac.signal)
    })

    it('非 2xx 抛错并附带 body', async () => {
      const fetchMock = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({ error: 'boom' }) }))
      vi.stubGlobal('fetch', fetchMock)
      await expect(request('GET', '/x')).rejects.toMatchObject({ status: 500, body: { error: 'boom' } })
    })

    it('401/403 弹 toast 提示', async () => {
      const fetchMock = vi.fn(async () => ({ ok: false, status: 401, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      const { useToastStore } = await import('@/stores/toast.js')
      const toast = useToastStore()
      await expect(request('GET', '/x')).rejects.toMatchObject({ status: 401 })
      expect(toast.toasts.length).toBeGreaterThan(0)
    })

    it('携带 X-API-Key 头（已设置 key 时）', async () => {
      setApiKey('secret')
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      await request('GET', '/x')
      const [, opts] = fetchMock.mock.calls[0]
      expect(opts.headers['X-API-Key']).toBe('secret')
    })
  })

  describe('createController', () => {
    it('返回 signal 与 abort', () => {
      const c = createController()
      expect(c.signal).toBeInstanceOf(AbortSignal)
      expect(c.abort).toBeTypeOf('function')
      expect(() => c.abort()).not.toThrow()
    })
  })

  describe('key management', () => {
    // _autoDetectPromise 是模块级缓存，跨测试会污染 → 用 resetModules + 动态 import 隔离
    async function freshApi() {
      vi.resetModules()
      const mod = await import('./api.js')
      setActivePinia(createPinia())
      return mod
    }

    it('setApiKey/getApiKey 内存读写', () => {
      expect(getApiKey()).toBe('')
      setApiKey('k1')
      expect(getApiKey()).toBe('k1')
      setApiKey('')
      expect(getApiKey()).toBe('')
    })

    it('autoDetectApiKey 从后端拉取并缓存', async () => {
      const mod = await freshApi()
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ data: { api_key: 'auto' } }) }))
      vi.stubGlobal('fetch', fetchMock)
      const k = await mod.autoDetectApiKey()
      expect(k).toBe('auto')
      expect(mod.getApiKey()).toBe('auto')
    })

    it('autoDetectApiKey 已存在 key 时直接返回', async () => {
      const mod = await freshApi()
      mod.setApiKey('existing')
      const fetchMock = vi.fn()
      vi.stubGlobal('fetch', fetchMock)
      const k = await mod.autoDetectApiKey()
      expect(k).toBe('existing')
      expect(fetchMock).not.toHaveBeenCalled()
    })

    it('autoDetectApiKey 失败返回空串且可重试', async () => {
      const mod = await freshApi()
      const fetchMock = vi.fn(async () => { throw new Error('net') })
      vi.stubGlobal('fetch', fetchMock)
      const k = await mod.autoDetectApiKey()
      expect(k).toBe('')
      // 再次调用会重新 fetch（失败不缓存）
      await mod.autoDetectApiKey()
      expect(fetchMock).toHaveBeenCalledTimes(2)
    })
  })

  describe('endpoints', () => {
    it('构建正确的请求路径', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      await endpoints.health()
      expect(fetchMock.mock.calls[0][0]).toBe('/api/health')
      await endpoints.memoryEvents(10, 'fact', 'kw')
      expect(fetchMock.mock.calls[1][0]).toBe('/api/management/memory/events?limit=10&type=fact&keyword=kw')
      await endpoints.causalTree('n1', 2)
      expect(fetchMock.mock.calls[2][0]).toBe('/api/management/causal-graph/tree/n1?depth=2')
      await endpoints.callTool('exec_command', { command: 'ls' })
      expect(fetchMock.mock.calls[3][0]).toBe('/api/tools/call')
      const [, opts] = fetchMock.mock.calls[3]
      expect(JSON.parse(opts.body)).toEqual({ tool_name: 'exec_command', params: { command: 'ls' } })
    })

    it('updateConfig 构造 value 载荷', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      await endpoints.updateConfig('EXECUTION_MODE', 'yolo')
      expect(fetchMock.mock.calls[0][0]).toBe('/api/config/EXECUTION_MODE')
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ value: 'yolo' })
    })

    it('createMemoryEvent 序列化查询参数', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      await endpoints.createMemoryEvent({ fact: 'f', keywords: 'a,b', importance: 0.5, event_type: 'thought' })
      const url = fetchMock.mock.calls[0][0]
      expect(url).toContain('/api/management/memory/events?')
      expect(url).toContain('fact=f')
      expect(url).toContain('importance=0.5')
    })

    it('deleteMessage/updateMessage 路径', async () => {
      const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }))
      vi.stubGlobal('fetch', fetchMock)
      await endpoints.deleteMessage('s1', 'm1')
      expect(fetchMock.mock.calls[0][0]).toBe('/api/stream/sessions/s1/messages/m1')
      await endpoints.updateMessage('s1', 'm1', 'new')
      expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ content: 'new' })
    })
  })
})
