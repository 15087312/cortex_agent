const App = {
    _currentRoute: 'dashboard', _pages: {}, _intervals: [], _backendOk: null, _backendCheckTime: 0,
    init() { this.setupNav(); this.updateHealth(); this.navigate('chat');
        this.initTheme(); this.setupKeyboard();
        setInterval(() => { if(Date.now()-this._backendCheckTime>30000) this.updateHealth(); }, 15000); },
    initTheme() {
        const saved = localStorage.getItem('cortex_theme') || 'light';
        document.body.setAttribute('data-theme', saved);
        const btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = saved === 'dark' ? '☀️' : '🌙';
        // Switch highlight.js theme
        this._switchHljsTheme(saved);
        // Delegate toggle
        document.addEventListener('click', e => {
            if (e.target.id === 'themeToggle') this.toggleTheme();
        });
    },
    toggleTheme() {
        const current = document.body.getAttribute('data-theme') || 'light';
        const next = current === 'dark' ? 'light' : 'dark';
        document.body.setAttribute('data-theme', next);
        localStorage.setItem('cortex_theme', next);
        const btn = document.getElementById('themeToggle');
        if (btn) btn.textContent = next === 'dark' ? '☀️' : '🌙';
        this._switchHljsTheme(next);
    },
    _switchHljsTheme(theme) {
        const link = document.getElementById('hljsTheme');
        if (!link) return;
        link.href = theme === 'dark'
            ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css'
            : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css';
    },
    setupNav() {
        document.querySelectorAll('.nav-item[data-route]').forEach(el => {
            el.addEventListener('click', () => this.navigate(el.dataset.route));
        });
    },
    navigate(route, params) {
        if (this._currentRoute && this._pages[this._currentRoute] && this._pages[this._currentRoute].destroy)
            this._pages[this._currentRoute].destroy();
        this._intervals.forEach(clearInterval); this._intervals = [];
        document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
        const nav = document.querySelector('.nav-item[data-route="'+route+'"]');
        if (nav) nav.classList.add('active');
        this._currentRoute = route;
        if (!this._pages[route]) {
            document.getElementById('pageBody').innerHTML = '<div class="empty-state"><div class="empty-icon">&#x1F6A7;</div><p>页面正在建设中</p></div>';
            document.getElementById('pageTitle').textContent = route;
            document.getElementById('headerActions').innerHTML = '';
            return;
        }
        document.getElementById('pageBody').innerHTML = '<div class="loading-overlay"><div class="spinner"></div><span>加载中...</span></div>';
        const p = this._pages[route];
        document.getElementById('pageTitle').textContent = p.title || route;
        document.getElementById('headerActions').innerHTML = p.headerActions || '';
        p.init(params);
    },
    register(name, page) { this._pages[name] = page; },
    startHealthCheck() {
        this.updateHealth();
        setInterval(() => this.updateHealth(), 15000);
    },
    async updateHealth() {
        try {
            const r = await API.getHealth();
            const d = r.data;
            this._backendOk = true; this._backendCheckTime = Date.now();
            document.getElementById('healthDot').className = 'status-dot ' + (d.status === 'healthy' ? 'online' : 'degraded');
            document.getElementById('healthText').textContent = d.status === 'healthy' ? '系统健康' : '系统降级';
            document.getElementById('statusBackend').textContent = 'API: ' + (d.status || '-');
        } catch {
            this._backendOk = false; this._backendCheckTime = Date.now();
            document.getElementById('healthDot').className = 'status-dot offline';
            document.getElementById('healthText').textContent = '未连接';
            document.getElementById('statusBackend').textContent = '';
        }
    },
    showToast(msg, type) {
        const container = document.getElementById('toastContainer') || (()=>{
            const c = document.createElement('div'); c.id = 'toastContainer';
            c.style.cssText = 'position:fixed;top:16px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none';
            document.body.appendChild(c); return c;
        })();
        const a = document.createElement('div');
        a.className = 'alert alert-'+type;
        a.textContent = msg;
        a.style.cssText = 'margin:0;pointer-events:auto;box-shadow:var(--shadow-md);animation:slideUp var(--duration-slow) var(--ease-out);max-width:360px';
        container.appendChild(a);
        setTimeout(() => { a.style.opacity = '0'; a.style.transition = 'opacity var(--duration-slow)'; setTimeout(() => a.remove(), 300); }, 3500);
    },
    setInterval(fn, ms) { const id = setInterval(fn, ms); this._intervals.push(id); return id; },
    renderBody(html) { document.getElementById('pageBody').innerHTML = html; },
    escapeHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; },
    setupKeyboard() {
        document.addEventListener('keydown', e => {
            const tag = document.activeElement?.tagName;
            const isInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
            // Ctrl+K or Cmd+K → search/focus nav (only if not in input)
            if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                if (!isInput) {
                    e.preventDefault();
                    const chatInput = document.querySelector('.chat-input-field');
                    if (chatInput) { chatInput.focus(); chatInput.scrollIntoView({ behavior: 'smooth' }); return; }
                }
            }
            // Escape → close modals, blur inputs
            if (e.key === 'Escape') {
                const modal = document.querySelector('.modal-overlay');
                if (modal) { e.preventDefault(); modal.remove(); return; }
                if (isInput) { e.preventDefault(); document.activeElement.blur(); return; }
            }
            // ? → show shortcuts help
            if (e.key === '?' && !isInput) {
                e.preventDefault();
                App._showShortcuts();
            }
            // / → focus chat input from anywhere
            if (e.key === '/' && !isInput && !e.ctrlKey && !e.metaKey) {
                const chatInput = document.querySelector('.chat-input-field');
                if (chatInput) { e.preventDefault(); chatInput.focus(); return; }
            }
        });
    },
    _shortcutsHelp: null,
    _showShortcuts() {
        if (this._shortcutsHelp) { this._shortcutsHelp.remove(); this._shortcutsHelp = null; return; }
        const shortcuts = [
            ['/','聚焦输入框'],
            ['Ctrl+K','聚焦输入框（非输入态）'],
            ['Escape','关闭弹窗 / 取消聚焦'],
            ['?','切换快捷键帮助'],
        ];
        const html = UI.card(
            shortcuts.map(([k,d]) => `<div style="display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid var(--border-light)"><kbd style="background:var(--bg-tertiary);padding:2px 8px;border-radius:4px;font-family:var(--font-mono);font-size:12px;border:1px solid var(--border)">${UI.e(k)}</kbd><span style="color:var(--text-muted)">${UI.e(d)}</span></div>`).join(''),
            '⌨️ 快捷键'
        );
        document.body.insertAdjacentHTML('beforeend', UI.modal(html, '', '400px'));
        this._shortcutsHelp = document.querySelector('.modal-overlay:last-child');
    },
    formatTime(ts) {
        if (!ts) return '-';
        const d = new Date(ts);
        if (isNaN(d.getTime())) return String(ts).slice(0,19).replace('T',' ');
        return d.toLocaleString('zh-CN', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    },
};
document.addEventListener('DOMContentLoaded', () => App.init());
