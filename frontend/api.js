const API = {
    _key: localStorage.getItem('cortex_api_key') || '',
    get _headers() {
        const h = {'Content-Type': 'application/json'};
        if (this._key) h['X-API-Key'] = this._key;
        return h;
    },
    setKey(k) { this._key = k; k ? localStorage.setItem('cortex_api_key', k) : localStorage.removeItem('cortex_api_key'); },
    async _fetch(method, path, body) {
        const opts = {method, headers: this._headers};
        if (body !== undefined) opts.body = JSON.stringify(body);
        try {
            const r = await fetch('/api' + path, opts);
            if (!r.ok) { const e = {status: r.status}; try { e.body = await r.json(); } catch { e.body = await r.text(); } throw e; }
            return await r.json();
        } catch (err) {
            if (err.status === 401 || err.status === 403) App.showToast('需要 API Key，请在设置页配置', 'error');
            throw err;
        }
    },
    get(p) { return this._fetch('GET', p); },
    post(p, b) { return this._fetch('POST', p, b); },
    put(p, b) { return this._fetch('PUT', p, b); },
    del(p) { return this._fetch('DELETE', p); },

    getHealth() { return this.get('/health'); },
    getDashboard() { return this.get('/management/dashboard'); },
    getModules() { return this.get('/management/modules'); },
    refreshModule(n) { return this.post('/management/modules/'+encodeURIComponent(n)+'/refresh'); },
    getMemoryEvents(limit,type,keyword) {
        let p = '/management/memory/events?limit='+(limit||50);
        if(type) p+='&type='+encodeURIComponent(type);
        if(keyword) p+='&keyword='+encodeURIComponent(keyword);
        return this.get(p);
    },
    getMemoryEvent(id) { return this.get('/management/memory/events/'+encodeURIComponent(id)); },
    createMemoryEvent(d) {
        const p = new URLSearchParams();
        if(d.fact) p.set('fact',d.fact); if(d.keywords) p.set('keywords',d.keywords);
        if(d.importance!=null) p.set('importance',d.importance); if(d.event_type) p.set('event_type',d.event_type);
        if(d.thought) p.set('thought',d.thought); if(d.lesson) p.set('lesson',d.lesson);
        return this.post('/management/memory/events?'+p.toString());
    },
    deleteMemoryEvent(id) { return this.del('/management/memory/events/'+encodeURIComponent(id)); },
    clearMemory() { return this.post('/management/memory/clear'); },
    getCausalGraph(t) { return this.get('/management/causal-graph'+(t?'?time_window='+encodeURIComponent(t):'')); },
    getCausalNode(id) { return this.get('/management/causal-graph/'+encodeURIComponent(id)); },
    getCausalTree(id,d) { return this.get('/management/causal-graph/tree/'+encodeURIComponent(id)+'?depth='+(d||3)); },
    getTools(s) { return this.get('/tools/'+(s?'?source='+encodeURIComponent(s):'')); },
    getToolInfo(n) { return this.get('/tools/info/'+encodeURIComponent(n)); },
    callTool(n,p) { return this.post('/tools/call',{tool_name:n,params:p||{}}); },
    getToolEvents(l) { return this.get('/tools/events?limit='+(l||50)); },
    getSecurityStatus() { return this.get('/security/status'); },
    getSecurityAudit(l) { return this.get('/security/audit?limit='+(l||50)); },
    setSecuritySwitch(l,e) { return this.post('/security/switch?level='+encodeURIComponent(l)+'&enable='+e); },
    getPerceptionStatus() { return this.get('/management/perception'); },
    startPerception() { return this.post('/management/perception/start'); },
    stopPerception() { return this.post('/management/perception/stop'); },
    getConfig() { return this.get('/config'); },
    updateConfig(k,v) { return this.put('/config/'+encodeURIComponent(k),{value:v}); },
    toggleCompanion() { return this.post('/config/toggle-companion-mode'); },
    getSessions() { return this.get('/stream/sessions'); },
    getManagementSessions() { return this.get('/management/sessions'); },
    getSessionDialog(id,limit) { return this.get('/management/sessions/'+encodeURIComponent(id)+'/dialog?limit='+(limit||100)); },
    getThinkingStatus() { return this.get('/management/thinking'); },
    getDatabase() { return this.get('/management/database'); },
    getSystemInfo() { return this.get('/'); },
    getInfoProcess() { return this.get('/management/info-process'); },
    getModels() { return this.get('/management/models'); },
    createSession() { return this.post('/stream/session'); },
};
