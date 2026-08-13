import { getApiKey } from '@/api.js'

const MAX_RETRY = 3

// 链路追踪：每次发送注入 trace_id / trace_seq（与 js/ws.js 对齐，供后端关联审计）
const _traceId = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID()
  : ('w' + Date.now() + Math.random().toString(36).slice(2))
let _traceSeq = 0

class WsClient {
  constructor() {
    this._conn = null
    this._listeners = {}
    this._attempt = 0
    this._shouldReconnect = false
    this._resolve = null
    this._reject = null
    this._timeoutId = null
    this._backendPort = null
    this._resolveBackendPort()
  }

  get connected() {
    return this._conn && this._conn.readyState === WebSocket.OPEN
  }

  _host() {
    // 直连后端（端口可能因占用自动回退，经 /backend-port 发现；Qt 静态代理 8765 无 WS 转发）
    return (window.location.hostname || 'localhost') + ':' + (this._backendPort || 8080)
  }

  _resolveBackendPort() {
    // 构造时预解析后端端口（fire-and-forget）；若首次连接时尚未返回，重试会用上真实端口
    try {
      fetch(window.location.origin + '/backend-port', { signal: AbortSignal.timeout(3000) })
        .then((r) => r.json())
        .then((j) => { if (j && j.port) this._backendPort = j.port })
        .catch(() => {})
    } catch {}
  }

  connect(sid) {
    this._shouldReconnect = true
    this._attempt = 0
    return new Promise((resolve, reject) => {
      this._resolve = resolve
      this._reject = reject
      this._doConnect(sid)
    })
  }

  _doConnect(sid) {
    if (this._conn) { this._conn.onclose = null; this._conn.onerror = null; this._conn.onmessage = null; try { this._conn.close() } catch {}; this._conn = null }
    // 浏览器 WebSocket 无法设置自定义 header，API Key 走 ?api_key= 查询参数（后端 WS 握手校验）
    const key = getApiKey ? getApiKey() : ''
    const auth = key ? `?api_key=${encodeURIComponent(key)}` : ''
    try {
      this._conn = new WebSocket(`ws://${this._host()}/stream/ws/${sid}${auth}`)
    } catch (e) {
      this._reject?.(e)
      this._scheduleRetry(sid)
      return
    }
    this._conn.onopen = () => {
      this._attempt = 0
      this._resolve?.()
      if (this._timeoutId) { clearTimeout(this._timeoutId); this._timeoutId = null }
    }
    this._conn.onmessage = (e) => {
      try { const d = JSON.parse(e.data); this._emit(d.type || d.event, d) } catch {}
    }
    this._conn.onclose = () => { if (this._shouldReconnect) this._scheduleRetry(sid) }
    this._conn.onerror = (e) => { console.error('WS error:', e) }
    this._timeoutId = setTimeout(() => { this._resolve?.() }, 8000)
  }

  _scheduleRetry(sid) {
    if (this._attempt >= MAX_RETRY) { this._reject?.('max retries'); return }
    const d = [1, 2, 4][this._attempt] * 1000
    this._attempt++
    setTimeout(() => this._doConnect(sid), d)
  }

  disconnect() {
    this._shouldReconnect = false
    if (this._conn) { this._conn.close(); this._conn = null }
    if (this._timeoutId) { clearTimeout(this._timeoutId); this._timeoutId = null }
  }

  send(data) {
    if (this._conn && this._conn.readyState === WebSocket.OPEN) {
      _traceSeq++
      const payload = { ...data, trace_id: _traceId, trace_seq: _traceSeq }
      this._conn.send(JSON.stringify(payload))
      return true
    }
    return false
  }

  on(ev, cb) {
    if (!this._listeners[ev]) this._listeners[ev] = []
    this._listeners[ev].push(cb)
  }

  off(ev, cb) {
    if (!this._listeners[ev]) return
    this._listeners[ev] = this._listeners[ev].filter(l => l !== cb)
  }

  _emit(ev, data) {
    (this._listeners[ev] || []).forEach(cb => cb(data))
    ;(this._listeners['all'] || []).forEach(cb => cb(data))
  }
}

export const wsClient = new WsClient()
export { WsClient }
