// ── 组件测试共享 helpers ──
import { setActivePinia, createPinia } from 'pinia'
import { config } from '@vue/test-utils'

export function createTestPinia() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return pinia
}

export function stubFetch(data, { ok = true, status = 200 } = {}) {
  const fn = typeof data === 'function' ? data : async () => data
  globalThis.fetch = async () => ({
    ok,
    status,
    json: async () => (await fn()),
    text: async () => '',
  })
  return globalThis.fetch
}

export const silenceVueWarnings = () => {
  config.global.config.warnHandler = () => {}
}

// URL 路由式 fetch mock：按 URL 子串匹配返回对应 JSON
// routes: [{ match: '/health', data: {...} }]  —— match 可以是字符串子串或函数(url, init)=>bool；
// data 可以是对象或函数(url, init)；未命中返回 { ok: true, json: () => ({}) }
export function routeFetch(routes) {
  const fn = async (url, init) => {
    const u = String(url)
    for (const r of routes) {
      const matched = typeof r.match === 'function' ? r.match(u, init) : u.includes(r.match)
      if (matched) {
        const data = typeof r.data === 'function' ? await r.data(u, init) : r.data
        return {
          ok: r.ok !== false,
          status: r.status || 200,
          json: async () => data,
          text: async () => (typeof data === 'string' ? data : JSON.stringify(data)),
        }
      }
    }
    return { ok: true, status: 200, json: async () => ({}), text: async () => '' }
  }
  globalThis.fetch = fn
  return fn
}
