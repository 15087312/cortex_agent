// Cortex WebSocket 客户端 → :8080 (via Vite proxy)
const MAX_RETRY = 3
const PING_INTERVAL = 30000

class CortexWsClient {
  constructor() {
    this._conn = null
    this._listeners = {}
    this._attempt = 0
    this._shouldReconnect = false
    this._pingTimer = null
    this._retryTimer = null
    this._sessionId = null
    this._resolve = null
    this._reject = null
  }

  get connected() {
    return this._conn && this._conn.readyState === WebSocket.OPEN
  }

  connect(sessionId) {
    this._clearRetryTimer()
    this._cleanup()
    this._sessionId = sessionId
    this._shouldReconnect = true
    this._attempt = 0
    return new Promise((resolve, reject) => {
      this._resolve = resolve
      this._reject = reject
      this._doConnect()
    })
  }

  _doConnect() {
    this._cleanup()
    const sid = this._sessionId
    if (!sid) { this._reject?.('no session id'); return }
    try {
      const host = window.location.host
      this._conn = new WebSocket(`ws://${host}/stream/ws/${sid}`)
    } catch (e) {
      this._reject?.(e)
      this._scheduleRetry()
      return
    }
    this._conn.onopen = () => {
      this._attempt = 0
      this._resolve?.()
      this._startPing()
    }
    this._conn.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data)
        this._emit(d.type, d)
      } catch {}
    }
    this._conn.onclose = () => {
      this._stopPing()
      if (this._shouldReconnect) this._scheduleRetry()
    }
    this._conn.onerror = () => {}
  }

  _scheduleRetry() {
    if (this._attempt >= MAX_RETRY) {
      this._reject?.('max retries')
      this._reject = null
      return
    }
    const delay = [1000, 2000, 4000][this._attempt]
    this._attempt++
    this._retryTimer = setTimeout(() => this._doConnect(), delay)
  }

  _clearRetryTimer() {
    if (this._retryTimer) { clearTimeout(this._retryTimer); this._retryTimer = null }
  }

  _startPing() {
    this._stopPing()
    this._pingTimer = setInterval(() => {
      if (this.connected) this.send({ type: 'ping' })
    }, PING_INTERVAL)
  }

  _stopPing() {
    if (this._pingTimer) { clearInterval(this._pingTimer); this._pingTimer = null }
  }

  _cleanup() {
    if (this._conn) {
      this._conn.onclose = null
      this._conn.onerror = null
      this._conn.onmessage = null
      try { this._conn.close() } catch {}
      this._conn = null
    }
    this._stopPing()
  }

  disconnect() {
    this._shouldReconnect = false
    this._clearRetryTimer()
    this._reject?.('disconnected')
    this._reject = null
    this._cleanup()
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
    ;(this._listeners[ev] || []).forEach(cb => cb(data))
  }
}

export const cortexWs = new CortexWsClient()