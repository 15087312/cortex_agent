// ── 统一 API 封装 ──
// 合并自 api/client.js + api/endpoints.js + api/cortex.js

import { useToastStore } from '@/stores/toast.js'

const BASE = '/api'
const CORTEX_BASE = '/cortex-api'

let _memKey = null
let _initialized = false

function _init() {
  if (_initialized) return
  _initialized = true
  try { _memKey = sessionStorage.getItem('cortex_api_key') || null } catch { _memKey = null }
}

function _headers() {
  _init()
  const h = { 'Content-Type': 'application/json' }
  if (_memKey) h['X-API-Key'] = _memKey
  return h
}

// ═══════════════════════════════════════════
// 基础请求（Cortex 后端 :8000）
// ═══════════════════════════════════════════

async function cortexRequest(method, path, body) {
  const opts = { method, headers: _headers() }
  if (body !== undefined) opts.body = JSON.stringify(body)
  const r = await fetch(CORTEX_BASE + path, opts)
  if (!r.ok) {
    const e = { status: r.status }
    try { e.body = await r.json() } catch { e.body = await r.text() }
    throw e
  }
  return await r.json()
}

// ═══════════════════════════════════════════
// 全局请求（原有后端 :8080）
// ═══════════════════════════════════════════

export async function request(method, path, body, signal) {
  const opts = { method, headers: _headers() }
  if (body !== undefined) opts.body = JSON.stringify(body)
  if (signal) opts.signal = signal
  const r = await fetch(BASE + path, opts)
  if (!r.ok) {
    const e = { status: r.status }
    try { e.body = await r.json() } catch { e.body = await r.text() }
    throw e
  }
  return await r.json()
}

export function createController() {
  const controller = new AbortController()
  return {
    signal: controller.signal,
    abort: (reason) => controller.abort(reason || 'component unmounted'),
  }
}

export function getApiKey() {
  _init()
  return _memKey || ''
}

export function setApiKey(k) {
  _memKey = k || null
  try { k ? sessionStorage.setItem('cortex_api_key', k) : sessionStorage.removeItem('cortex_api_key') } catch {}
}

// ═══════════════════════════════════════════
// Cortex API（:8000）
// ═══════════════════════════════════════════

export function createSession() { return cortexRequest('POST', '/sessions') }
export function listSessions() { return cortexRequest('GET', '/sessions?limit=50') }
export function getMessages(sessionId, limit = 100) { return cortexRequest('GET', `/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}`) }
export function deleteSession(sessionId) { return cortexRequest('DELETE', `/sessions/${encodeURIComponent(sessionId)}`) }

// ═══════════════════════════════════════════
// 端点（原有后端 :8080）
// ═══════════════════════════════════════════

export const endpoints = {
  get: (path, signal) => request('GET', path, undefined, signal),

  health: (signal) => request('GET', '/health', undefined, signal),
  dashboard: (signal) => request('GET', '/management/dashboard', undefined, signal),
  modules: (signal) => request('GET', '/management/modules', undefined, signal),
  refreshModule: (n, signal) => request('POST', '/management/modules/' + encodeURIComponent(n) + '/refresh', undefined, signal),

  memoryEvents: (limit, type, keyword, signal) => {
    let p = '/management/memory/events?limit=' + (limit || 50)
    if (type) p += '&type=' + encodeURIComponent(type)
    if (keyword) p += '&keyword=' + encodeURIComponent(keyword)
    return request('GET', p, undefined, signal)
  },
  memoryEvent: (id, signal) => request('GET', '/management/memory/events/' + encodeURIComponent(id), undefined, signal),
  createMemoryEvent: (d, signal) => {
    const p = new URLSearchParams()
    if (d.fact) p.set('fact', d.fact)
    if (d.keywords) p.set('keywords', d.keywords)
    if (d.importance != null) p.set('importance', d.importance)
    if (d.event_type) p.set('event_type', d.event_type)
    if (d.thought) p.set('thought', d.thought)
    if (d.lesson) p.set('lesson', d.lesson)
    return request('POST', '/management/memory/events?' + p.toString(), undefined, signal)
  },
  deleteMemoryEvent: (id, signal) => request('DELETE', '/management/memory/events/' + encodeURIComponent(id), undefined, signal),
  clearMemory: (signal) => request('POST', '/management/memory/clear', undefined, signal),

  causalGraph: (t, signal) => request('GET', '/management/causal-graph' + (t ? '?time_window=' + encodeURIComponent(t) : ''), undefined, signal),
  causalNode: (id, signal) => request('GET', '/management/causal-graph/' + encodeURIComponent(id), undefined, signal),
  causalTree: (id, d, signal) => request('GET', '/management/causal-graph/tree/' + encodeURIComponent(id) + '?depth=' + (d || 3), undefined, signal),

  tools: (s, signal) => request('GET', '/tools/' + (s ? '?source=' + encodeURIComponent(s) : ''), undefined, signal),
  toolInfo: (n, signal) => request('GET', '/tools/info/' + encodeURIComponent(n), undefined, signal),
  callTool: (n, p, signal) => request('POST', '/tools/call', { tool_name: n, params: p || {} }, signal),
  toolEvents: (l, signal) => request('GET', '/tools/events?limit=' + (l || 50), undefined, signal),

  securityStatus: (signal) => request('GET', '/security/status', undefined, signal),
  securityAudit: (l, signal) => request('GET', '/security/audit?limit=' + (l || 50), undefined, signal),
  setSecuritySwitch: (l, e, signal) => request('POST', '/security/switch?level=' + encodeURIComponent(l) + '&enable=' + e, undefined, signal),

  perceptionStatus: (signal) => request('GET', '/management/perception', undefined, signal),
  startPerception: (signal) => request('POST', '/management/perception/start', undefined, signal),
  stopPerception: (signal) => request('POST', '/management/perception/stop', undefined, signal),

  config: (signal) => request('GET', '/config', undefined, signal),
  updateConfig: (k, v, signal) => request('PUT', '/config/' + encodeURIComponent(k), { value: v }, signal),
  toggleCompanion: (signal) => request('POST', '/config/toggle-companion-mode', undefined, signal),

  sessions: (signal) => request('GET', '/stream/sessions', undefined, signal),
  deleteSession: (id, signal) => request('DELETE', '/stream/session/' + encodeURIComponent(id), undefined, signal),
  managementSessions: (signal) => request('GET', '/management/sessions', undefined, signal),
  sessionDialog: (id, limit, signal) => request('GET', '/management/sessions/' + encodeURIComponent(id) + '/dialog?limit=' + (limit || 100), undefined, signal),

  thinkingStatus: (signal) => request('GET', '/management/thinking', undefined, signal),
  database: (signal) => request('GET', '/management/database', undefined, signal),
  systemInfo: (signal) => request('GET', '/', undefined, signal),
  infoProcess: (signal) => request('GET', '/management/info-process', undefined, signal),
  models: (signal) => request('GET', '/management/models', undefined, signal),
  createSession: (signal) => request('POST', '/stream/session', undefined, signal),
}
