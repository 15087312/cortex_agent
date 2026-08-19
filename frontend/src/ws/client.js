import { getApiKey } from '@/api.js'

const MAX_RETRY = 3

// 链路追踪：每次发送注入 trace_id / trace_seq（与 js/ws.js 对齐，供后端关联审计）
const _traceId = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID()
  : ('w' + Date.now() + Math.random().toString(36).slice(2))
let _traceSeq = 0

// 多会话并行连接：每个会话一条独立 WS（后端按 /stream/ws/{sid} 路由，
// 前端据此让多个会话同时处理互不干扰）。
class WsClient {
  constructor() {
    this._conns = {}  // sid -> { conn, shouldReconnect, attempt, resolve, reject, timeoutId }
    this._listeners = {}
    this._backendPort = null
    this._resolveBackendPort()
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

  // 兼容旧接口：是否有任意活跃连接
  get connected() {
    return Object.values(this._conns).some(r => r.conn && r.conn.readyState === WebSocket.OPEN)
  }

  isConnected(sid) {
    const r = this._conns[sid]
    return !!(r && r.conn && r.conn.readyState === WebSocket.OPEN)
  }

  connect(sid) {
    if (!sid) return Promise.resolve()
    if (this.isConnected(sid)) return Promise.resolve()
    const rec = this._conns[sid] || { shouldReconnect: true, attempt: 0, resolve: null, reject: null, timeoutId: null }
    rec.shouldReconnect = true
    this._conns[sid] = rec
    return new Promise((resolve, reject) => {
      rec.resolve = resolve
      rec.reject = reject
      this._doConnect(sid, rec)
    })
  }

  _doConnect(sid, rec) {
    if (rec.conn) { rec.conn.onclose = null; rec.conn.onerror = null; rec.conn.onmessage = null; try { rec.conn.close() } catch {}; rec.conn = null }
    // 浏览器 WebSocket 无法设置自定义 header，API Key 走 ?api_key= 查询参数（后端 WS 握手校验）
    const key = getApiKey ? getApiKey() : ''
    const auth = key ? `?api_key=${encodeURIComponent(key)}` : ''
    let conn
    try {
      conn = new WebSocket(`ws://${this._host()}/stream/ws/${sid}${auth}`)
    } catch (e) {
      rec.reject?.(e)
      this._scheduleRetry(sid, rec)
      return
    }
    rec.conn = conn
    conn.onopen = () => {
      rec.attempt = 0
      rec.resolve?.()
      rec.resolve = null
      if (rec.timeoutId) { clearTimeout(rec.timeoutId); rec.timeoutId = null }
    }
    conn.onmessage = (e) => {
      try { const d = JSON.parse(e.data); this._emit(d.type || d.event, d) } catch {}
    }
    conn.onclose = () => { if (rec.shouldReconnect) this._scheduleRetry(sid, rec) }
    conn.onerror = (e) => { console.error('WS error:', e) }
    rec.timeoutId = setTimeout(() => { rec.resolve?.(); rec.resolve = null }, 8000)
  }

  _scheduleRetry(sid, rec) {
    if (rec.attempt >= MAX_RETRY) { rec.reject?.('max retries'); rec.reject = null; return }
    const d = [1, 2, 4][rec.attempt] * 1000
    rec.attempt++
    setTimeout(() => this._doConnect(sid, rec), d)
  }

  disconnect(sid) {
    const rec = this._conns[sid]
    if (!rec) return
    rec.shouldReconnect = false
    if (rec.conn) { rec.conn.close(); rec.conn = null }
    if (rec.timeoutId) { clearTimeout(rec.timeoutId); rec.timeoutId = null }
    delete this._conns[sid]
  }

  disconnectAll() {
    Object.keys(this._conns).forEach(sid => this.disconnect(sid))
  }

  send(sid, data) {
    const rec = this._conns[sid]
    if (rec && rec.conn && rec.conn.readyState === WebSocket.OPEN) {
      _traceSeq++
      const payload = { ...data, trace_id: _traceId, trace_seq: _traceSeq }
      rec.conn.send(JSON.stringify(payload))
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