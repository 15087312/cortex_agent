// ── 统一 API 封装 ──
// 合并自 api/client.js + api/endpoints.js + api/cortex.js

import { useToastStore } from '@/stores/toast.js'

const BASE = '/api'

let _memKey = null
let _initialized = false

// 链路追踪：每次请求附带 X-Trace-Id / X-Request-Seq（与 js/api.js 对齐）
let _traceId = (typeof crypto !== 'undefined' && crypto.randomUUID)
  ? crypto.randomUUID()
  : ('t' + Date.now() + Math.random().toString(36).slice(2))
let _traceSeq = 0

function _init() {
  if (_initialized) return
  _initialized = true
  // API Key 仅存内存，不落 localStorage/sessionStorage（安全：避免 XSS 窃取持久化密钥）。
  // 刷新/重启后由 autoDetectApiKey() 从 /config/api-key 自动拉取（开发环境明文、生产仅回环）。
  // 注意：不重置 _memKey——若调用方已通过 setApiKey 设置过 key（如启动时），
  // 首次惰性初始化不得覆盖它。
}

function _headers() {
  _init()
  _traceSeq++
  const h = {
    'Content-Type': 'application/json',
    'X-Trace-Id': _traceId,
    'X-Request-Seq': String(_traceSeq),
  }
  if (_memKey) h['X-API-Key'] = _memKey
  return h
}

// ═══════════════════════════════════════════
// API key 自动检测 — 从后端 /config/api-key 拉取，免手动录入
// ═══════════════════════════════════════════

let _autoDetectPromise = null

/**
 * 启动时调用：若本地无 key，则从后端自动拉取（开发/测试环境后端返回 key）。
 * 幂等：并发调用共享同一个 Promise。返回最终生效的 key（可能为空串）。
 */
export async function autoDetectApiKey() {
  _init()
  if (_memKey) return _memKey
  if (!_autoDetectPromise) {
    _autoDetectPromise = (async () => {
      try {
        const r = await fetch(BASE + '/config/api-key')
        if (!r.ok) throw new Error('HTTP ' + r.status)
        const j = await r.json()
        const k = (j?.data?.api_key) || ''
        if (k) setApiKey(k)
        return k
      } catch (err) {
        // 失败不缓存（后端启动慢时首次可能失败），下次调用自动重试
        _autoDetectPromise = null
        return ''
      }
    })()
  }
  return _autoDetectPromise
}

// ═══════════════════════════════════════════
// 全局请求（统一后端 :8080，经 vite /api 代理剥前缀 → /stream/*）
// ═══════════════════════════════════════════

export async function request(method, path, body, signal) {
  const opts = { method, headers: _headers() }
  if (body !== undefined) opts.body = JSON.stringify(body)
  if (signal) opts.signal = signal
  const r = await fetch(BASE + path, opts)
  if (!r.ok) {
    const e = { status: r.status }
    try { e.body = await r.json() } catch { e.body = await r.text() }
    if (r.status === 401 || r.status === 403) {
      try {
        const { useToastStore } = await import('@/stores/toast.js')
        useToastStore().show('需要 API Key，请在设置页配置', 'error')
      } catch {}
    }
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
  // 仅内存持有；刷新页面后由 autoDetectApiKey() 自动恢复，无需持久化
  _memKey = k || null
}

// ═══════════════════════════════════════════
// Cortex 会话 API → chat_gateway /stream/* 端点（统一后端 :8080）
// ═══════════════════════════════════════════

// ═══════════════════════════════════════════
// 端点
// ═══════════════════════════════════════════

export const endpoints = {
  get: (path, signal) => request('GET', path, undefined, signal),

  health: (signal) => request('GET', '/health', undefined, signal),
  latestVersion: (signal) => request('GET', '/system/latest-version', undefined, signal),
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
  installVoiceDeps: () => request('POST', '/management/install-voice-deps'),
  visionModels: (signal) => request('GET', '/management/vision-models', undefined, signal),
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

  personas: (signal) => request('GET', '/config/personas', undefined, signal),
  updatePersona: (role, prompt, systemOverride, signal) => request('PUT', '/config/persona/' + encodeURIComponent(role), { value: prompt, system_override: systemOverride ?? null }, signal),
  memoryLibs: (signal) => request('GET', '/config/memory-libs', undefined, signal),
  createMemoryLib: (name) => request('POST', '/config/memory-libs', { name }),
  switchMemoryLib: (name) => request('PUT', '/config/memory-libs/current', { name }),
  renameMemoryLib: (oldName, newName) => request('PUT', '/config/memory-libs/rename', { old_name: oldName, new_name: newName }),
  deleteMemoryLib: (name) => request('DELETE', '/config/memory-libs/' + encodeURIComponent(name)),

  sessions: (signal) => request('GET', '/stream/sessions', undefined, signal),
  deleteSession: (id, signal) => request('DELETE', '/stream/session/' + encodeURIComponent(id), undefined, signal),
  batchDeleteSessions: (ids, signal) => request('POST', '/stream/sessions/batch-delete', { session_ids: ids }, signal),
  updateSessionTitle: (id, title, signal) => request('PUT', '/stream/session/' + encodeURIComponent(id) + '/title', { title }, signal),
  getOutreachConfig: (id, signal) => request('GET', '/stream/session/' + encodeURIComponent(id) + '/outreach-config', undefined, signal),
  setOutreachConfig: (id, cfg, signal) => request('PUT', '/stream/session/' + encodeURIComponent(id) + '/outreach-config', { outreach: cfg }, signal),
  proactiveLogs: (limit, signal) => request('GET', '/stream/proactive-log?limit=' + (limit || 50), undefined, signal),
  deleteMessage: (sid, mid, signal) => request('DELETE', '/stream/sessions/' + encodeURIComponent(sid) + '/messages/' + encodeURIComponent(mid), undefined, signal),
  updateMessage: (sid, mid, content, signal) => request('PUT', '/stream/sessions/' + encodeURIComponent(sid) + '/messages/' + encodeURIComponent(mid), { content }, signal),
  sessionMessages: (id, limit, signal) => request('GET', '/stream/sessions/' + encodeURIComponent(id) + '/messages?limit=' + (limit || 100), undefined, signal),
  managementSessions: (signal) => request('GET', '/management/sessions', undefined, signal),
  sessionDialog: (id, limit, signal) => request('GET', '/management/sessions/' + encodeURIComponent(id) + '/dialog?limit=' + (limit || 100), undefined, signal),

  thinkingStatus: (signal) => request('GET', '/management/thinking', undefined, signal),
  database: (signal) => request('GET', '/management/database', undefined, signal),
  systemInfo: (signal) => request('GET', '/', undefined, signal),
  infoProcess: (signal) => request('GET', '/management/info-process', undefined, signal),
  models: (signal) => request('GET', '/management/models', undefined, signal),
  createSession: (signal) => request('POST', '/stream/session', undefined, signal),
}
