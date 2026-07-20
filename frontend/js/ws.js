const WS = {
    _conn: null, _sessionId: null, _listeners: {}, _attempt: 0, _maxRetry: 3, _shouldReconnect: false, _connectResolve: null, _connectReject: null,
    get connected() { return this._conn && this._conn.readyState === WebSocket.OPEN; },
    connect(sid) {
        this._sessionId = sid; this._shouldReconnect = true; this._attempt = 0;
        return new Promise((resolve, reject) => {
            this._connectResolve = resolve; this._connectReject = reject;
            this._doConnect();
        });
    },
    _doConnect() {
        if (!this._sessionId) { this._connectReject?.('no session'); return; }
        try { this._conn = new WebSocket('ws://localhost:8080/stream/ws/'+this._sessionId); } catch (e) { this._connectReject?.(e); this._scheduleRetry(); return; }
        this._conn.onopen = () => { this._attempt = 0; this._connectResolve?.(); };
        this._conn.onmessage = (e) => { try { const d = JSON.parse(e.data); this._dispatch(d.type||d.event, d); } catch {} };
        this._conn.onclose = () => { if (this._shouldReconnect) this._scheduleRetry(); };
        this._conn.onerror = (e) => { console.error('WS error:', e); };
        // Timeout: resolve anyway after 8s so page doesn't hang
        this._timeoutId = setTimeout(() => { this._connectResolve?.(); }, 8000);
    },
    _scheduleRetry() {
        if (this._attempt >= this._maxRetry) { this._connectReject?.('max retries'); return; }
        const d = [1,2,4][this._attempt]*1000; this._attempt++;
        setTimeout(() => this._doConnect(), d);
    },
    _scheduleRetry() {
        if (this._attempt >= this._maxRetry) return;
        const d = [1,2,4][this._attempt]*1000; this._attempt++;
        setTimeout(() => this._doConnect(), d);
    },
    disconnect() { this._shouldReconnect = false; if (this._conn) { this._conn.close(); this._conn = null; } },
    send(data) { return this._conn && this._conn.readyState === WebSocket.OPEN ? !!(this._conn.send(JSON.stringify(data))) : false; },
    on(ev, cb) { if (!this._listeners[ev]) this._listeners[ev] = []; this._listeners[ev].push(cb); },
    off(ev, cb) { if (!this._listeners[ev]) return; this._listeners[ev] = this._listeners[ev].filter(l=>l!==cb); },
    _dispatch(ev, data) { (this._listeners[ev]||[]).forEach(cb=>cb(data)); (this._listeners['all']||[]).forEach(cb=>cb(data)); },
};
