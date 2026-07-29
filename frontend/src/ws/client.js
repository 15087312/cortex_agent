const MAX_RETRY = 3

class WsClient {
  constructor() {
    this._conn = null
    this._listeners = {}
    this._attempt = 0
    this._shouldReconnect = false
    this._resolve = null
    this._reject = null
    this._timeoutId = null
  }

  get connected() {
    return this._conn && this._conn.readyState === WebSocket.OPEN
  }

  _host() {
    try { return window.location.hostname || 'localhost' } catch { return 'localhost' }
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
    try {
      this._conn = new WebSocket(`ws://${this._host()}:8080/stream/ws/${sid}`)
    } catch (e) {
      this._reject?.(e)
      this._scheduleRetry(sid)
      return
    }
    this._conn.onopen = () => { this._attempt = 0; this._resolve?.() }
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
      this._conn.send(JSON.stringify(data))
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
