/* ══════════════════════════════════════════════
   UI 组件模板系统 — 替代内联 HTML 拼接
   所有函数返回 HTML 字符串
   ══════════════════════════════════════════════ */

const UI = {
    // ── 安全转义 ──
    e(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; },

    // ── 时间格式化 ──
    time(ts) {
        if (!ts) return '-';
        const d = new Date(ts);
        if (isNaN(d.getTime())) return String(ts).slice(0, 19).replace('T', ' ');
        return d.toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    },

    // ── 徽章 ──
    badge(text, color = 'gray') {
        return `<span class="badge badge-${color}">${this.e(text)}</span>`;
    },

    // ── 统计卡片 ──
    statCard(icon, value, label, onClick) {
        const click = onClick ? ` onclick="${this.jsStr(onClick)}"` : '';
        const cur = onClick ? ' cursor:pointer' : '';
        return `<div class="stat-card"${click} style="${cur}">
            ${icon ? `<div class="stat-icon">${icon}</div>` : ''}
            <div class="stat-value">${value}</div>
            <div class="stat-label">${this.e(label)}</div>
        </div>`;
    },

    // ── 统计网格（多卡片） ──
    statGrid(cards) {
        return `<div class="stat-grid">${cards.join('')}</div>`;
    },

    // ── 卡片包装 ──
    card(content, header) {
        const h = header ? `<div class="card-header">${header}</div>` : '';
        return `<div class="card">${h}${content}</div>`;
    },

    // ── 表格 ──
    table(headers, rows, emptyMsg = '暂无数据') {
        if (!rows || rows.length === 0) {
            return this.empty(emptyMsg);
        }
        const h = `<thead><tr>${headers.map(h => `<th>${this.e(h)}</th>`).join('')}</tr></thead>`;
        const b = `<tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>`;
        return `<table class="data-table">${h}${b}</table>`;
    },

    // ── 按钮 ──
    btn(text, onClick, type = '') {
        const cls = type ? `btn btn-${type}` : 'btn';
        return `<button class="${cls}" onclick="${this.jsStr(onClick)}">${this.e(text)}</button>`;
    },
    btnSm(text, onClick, type = '') {
        const cls = type ? `btn btn-${type} btn-sm` : 'btn btn-sm';
        return `<button class="${cls}" onclick="${this.jsStr(onClick)}">${this.e(text)}</button>`;
    },

    // ── 输入框 ──
    input(placeholder, id, value = '') {
        return `<input class="input" id="${this.e(id)}" placeholder="${this.e(placeholder)}" value="${this.e(value)}">`;
    },
    textarea(placeholder, id, value = '') {
        return `<textarea class="input" id="${this.e(id)}" placeholder="${this.e(placeholder)}" style="width:100%;min-height:60px;font-family:var(--font-mono)">${this.e(value)}</textarea>`;
    },
    select(id, options, selected = '') {
        const opts = options.map(([val, label]) =>
            `<option value="${this.e(val)}"${val === selected ? ' selected' : ''}>${this.e(label)}</option>`
        ).join('');
        return `<select class="input" id="${this.e(id)}">${opts}</select>`;
    },

    // ── 搜索栏 ──
    searchBar(inputId, buttonHtml) {
        return `<div class="search-bar">${this.input('搜索...', inputId)}${buttonHtml || ''}</div>`;
    },

    // ── 提示条 ──
    alert(message, type = 'error') {
        return `<div class="alert alert-${type}">${message}</div>`;
    },

    // ── JS 字符串转义（用于 onclick 等属性中的字符串参数） ──
    jsStr(s) {
        return String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\r/g, '');
    },

    // ── 加载态 ──
    spinner(size = '') {
        return `<div class="spinner${size ? ' spinner-'+size : ''}"></div>`;
    },
    loading(text = '加载中...') {
        return `<div class="loading-overlay">${this.spinner()}<span>${this.e(text)}</span></div>`;
    },

    // ── 空状态 ──
    empty(text = '暂无数据', icon = '📭', subtitle = '', actionBtn = '') {
        const sub = subtitle ? `<p class="empty-subtitle">${this.e(subtitle)}</p>` : '';
        const btn = actionBtn ? `<div class="empty-action">${actionBtn}</div>` : '';
        return `<div class="empty-state"><div class="empty-icon">${icon}</div><p class="empty-text">${this.e(text)}</p>${sub}${btn}</div>`;
    },

    // ── 弹窗 ──
    modal(content, title = '', width = '') {
        const w = width ? ` style="min-width:${width}"` : '';
        const t = title ? `<h3>${this.e(title)}</h3>` : '';
        return `<div class="modal-overlay" onclick="if(event.target===this)this.remove()">
            <div class="modal"${w}>${t}${content}</div>
        </div>`;
    },
    modalActions(buttons) {
        return `<div class="modal-actions">${buttons.join('')}</div>`;
    },

    // ── 骨架屏（Loading 占位） ──
    skeleton(lines = 3) {
        const items = Array.from({ length: lines }, () =>
            '<div class="skeleton" style="height:14px;margin-bottom:8px;width:' + (60 + Math.random() * 30) + '%"></div>'
        ).join('');
        return `<div class="card">${items}</div>`;
    },

    // ── 消息气泡 ──
    message(content, role = 'ai', idx = -1, actions = '') {
        const isU = role === 'user';
        const avatar = isU ? 'U' : 'AI';
        const cls = isU ? 'user' : 'ai';
        const idxAttr = idx >= 0 ? ` data-idx="${idx}"` : '';
        return `<div class="message ${cls}"${idxAttr}>
            <div class="message-avatar">${avatar}</div>
            <div class="message-bubble">${content}${actions ? `<div class="message-actions">${actions}</div>` : ''}</div>
        </div>`;
    },
};
