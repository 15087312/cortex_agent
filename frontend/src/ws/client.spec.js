import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { WsClient } from './client.js'
import { getApiKey, setApiKey } from '@/api.js'

class FakeWebSocket {
  static OPEN = 1
  static instances = []
  constructor(url) {
    this.url = url
    this.readyState = 0 // CONNECTING
    this.onopen = null
    this.onclose = null
    this.onmessage = null
    this.onerror = null
    this.sent = []
    FakeWebSocket.instances.push(this)
  }
  close() { this.readyState = 3 }
  send(data) { this.sent.push(data) }
  _open() { this.readyState = 1; this.onopen?.() }
  _msg(data) { this.onmessage?.({ data: JSON.stringify(data) }) }
  _close() { this.readyState = 3; this.onclose?.() }
}

describe('WsClient', () => {
  let client

  beforeEach(() => {
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    vi.useFakeTimers()
    client = new WsClient()
    setApiKey('')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    client.disconnectAll()
  })

  it('connected 未连接时为 falsy', () => {
    expect(client.connected).toBeFalsy()
  })

  it('connect 建立连接并 resolve（api_key 参数）', async () => {
    setApiKey('sek')
    const p = client.connect('sess1')
    const ws = FakeWebSocket.instances[0]
    expect(ws.url).toBe('ws://localhost:8080/stream/ws/sess1?api_key=sek')
    ws._open()
    await expect(p).resolves.toBeUndefined()
    expect(client.connected).toBe(true)
  })

  it('connect 无 key 时不带查询参数', async () => {
    const p = client.connect('sess1')
    expect(FakeWebSocket.instances[0].url).toBe('ws://localhost:8080/stream/ws/sess1')
    FakeWebSocket.instances[0]._open()
    await p
  })

  it('onmessage 解析 JSON 并广播事件', async () => {
    const p = client.connect('s1')
    FakeWebSocket.instances[0]._open()
    await p
    const cb = vi.fn()
    client.on('thinking', cb)
    FakeWebSocket.instances[0]._msg({ type: 'thinking', content: 'x' })
    expect(cb).toHaveBeenCalledWith(expect.objectContaining({ content: 'x' }))
    client.off('thinking', cb)
    FakeWebSocket.instances[0]._msg({ type: 'thinking', content: 'y' })
    expect(cb).toHaveBeenCalledTimes(1)
  })

  it('非法 JSON 静默忽略', async () => {
    const p = client.connect('s1')
    FakeWebSocket.instances[0]._open()
    await p
    const cb = vi.fn()
    client.on('all', cb)
    FakeWebSocket.instances[0].onmessage({ data: '{bad json' })
    expect(cb).not.toHaveBeenCalled()
  })

  it('send 在 OPEN 时注入 trace 并返回 true', async () => {
    const p = client.connect('s1')
    FakeWebSocket.instances[0]._open()
    await p
    const ok = client.send('s1', { type: 'input', content: 'hi' })
    expect(ok).toBe(true)
    const payload = JSON.parse(FakeWebSocket.instances[0].sent[0])
    expect(payload.type).toBe('input')
    expect(payload.content).toBe('hi')
    expect(payload.trace_id).toBeTruthy()
  })

  it('send 未连接返回 false', () => {
    expect(client.send('nope', { type: 'input' })).toBe(false)
  })

  it('connect 8s 超时兜底 resolve（不依赖 onopen）', async () => {
    const p = client.connect('s1')
    expect(FakeWebSocket.instances.length).toBe(1)
    // 不触发 _open()，推进 8s 兜底 timer
    vi.advanceTimersByTime(8000)
    await expect(p).resolves.toBeUndefined()
  })

  it('连接断开后自动重连（带退避）', async () => {
    const p = client.connect('s1')
    FakeWebSocket.instances[0]._open()
    await p
    FakeWebSocket.instances[0]._close()
    expect(FakeWebSocket.instances.length).toBe(1)
    vi.advanceTimersByTime(1000)
    expect(FakeWebSocket.instances.length).toBe(2)
  })

  it('超过最大重试次数 reject', async () => {
    const p = client.connect('s1')
    // 推进退避：1000 → 2000 → 4000，第三次重试后仍失败 → reject
    // 注意每次 _doConnect 也会启动 8s 兜底 timer，需在 8s 内完成
    FakeWebSocket.instances[0]._close()
    vi.advanceTimersByTime(1000)
    FakeWebSocket.instances[FakeWebSocket.instances.length - 1]._close()
    vi.advanceTimersByTime(2000)
    FakeWebSocket.instances[FakeWebSocket.instances.length - 1]._close()
    vi.advanceTimersByTime(4000)
    FakeWebSocket.instances[FakeWebSocket.instances.length - 1]._close()
    await expect(p).rejects.toBe('max retries')
  })

  it('disconnect 停止重连并关闭连接', async () => {
    const p = client.connect('s1')
    FakeWebSocket.instances[0]._open()
    await p
    const ws = FakeWebSocket.instances[0]
    const closeSpy = vi.spyOn(ws, 'close')
    client.disconnectAll()
    expect(closeSpy).toHaveBeenCalled()
    // 断开后 onclose 不应触发重连
    const count = FakeWebSocket.instances.length
    ws._close()
    vi.advanceTimersByTime(5000)
    expect(FakeWebSocket.instances.length).toBe(count)
  })
})
