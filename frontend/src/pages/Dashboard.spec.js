import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Dashboard from './Dashboard.vue'
import { routeFetch } from '@/test/helpers.js'

let wrapper = null
function mountPage() {
  wrapper = mount(Dashboard, {
    global: {
      plugins: [createPinia()],
      mocks: { $router: { push: () => {} } },
    },
  })
  return wrapper
}

describe('Dashboard 页面', () => {
  afterEach(() => {
    // 卸载组件 → onBeforeUnmount 清理 30s 轮询定时器
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })
  it('加载仪表盘数据并渲染统计', async () => {
    routeFetch([
      { match: '/management/dashboard', data: { data: { modules: { memory: 'healthy', vision: 'degraded' } } } },
      { match: '/health', data: { data: { status: 'healthy' } } },
      { match: '/config/memory-libs', data: { data: { libs: [{ event_count: 5 }] } } },
      { match: '/stream/sessions', data: { data: [{ session_id: 's1', title: 'T', last_active: '2024-01-01T00:00:00', metadata: { outreach: { enabled: true } } }] } },
      { match: '/management/perception', data: { data: { status: 'running', world_state: { active_app: 'Chrome' } } } },
      { match: '/stream/proactive-log?limit=5', data: { data: { logs: [{ id: 'l1' }] } } },
      { match: '/management/api-requests/stats', data: { data: { total: 3 } } },
      { match: '/management/api-requests', data: { data: { items: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    const text = w.text()
    expect(text).toContain('健康')
    expect(text).toContain('Chrome')
    expect(w.vm.moduleOk).toBe('1/2')
    expect(w.vm.totalEvents).toBe(5)
    expect(w.vm.totalSessions).toBe(1)
    expect(w.vm.enabledOutreach).toBe(1)
    expect(w.vm.perceptionRunning).toBe(true)
  })

  it('API 请求日志分页与筛选', async () => {
    let reqUrl = ''
    routeFetch([
      { match: '/management/dashboard', data: { data: {} } },
      { match: '/management/api-requests/stats', data: { data: { total: 1 } } },
      { match: '/management/api-requests', data: (u) => { reqUrl = u; return { data: { items: [{ path: '/x', id: 1 }], total: 1 } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(reqUrl).toContain('limit=50')
    expect(reqUrl).toContain('offset=0')
    w.vm.apiReqNext()
    await new Promise((r) => setTimeout(r, 10))
    expect(reqUrl).toContain('offset=50')
    w.vm.apiReqPrev()
    await new Promise((r) => setTimeout(r, 10))
    expect(reqUrl).toContain('offset=0')
    // 筛选
    w.vm.apiReqFilter.method = 'GET'
    w.vm.applyApiFilter()
    await new Promise((r) => setTimeout(r, 10))
    expect(reqUrl).toContain('method=GET')
  })

  it('API 详情打开/关闭与 formatBody', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.openApiDetail({ id: 1, body: '{"a":1}' })
    await w.vm.$nextTick()
    expect(w.vm.apiDetail).not.toBeNull()
    expect(w.vm.formatBody('{"a":1}')).toContain('"a"')
    expect(w.vm.formatBody('not-json')).toBe('not-json')
    expect(w.vm.formatBody('')).toBe('（无记录）')
    w.vm.closeApiDetail()
    expect(w.vm.apiDetail).toBeNull()
  })

  it('topPaths 统计高频路径', () => {
    const w = mountPage()
    w.vm.apiReq = { items: [{ path: '/a' }, { path: '/a' }, { path: '/b' }], total: 3 }
    const tp = w.vm.topPaths
    expect(tp.arr[0].path).toBe('/a')
    expect(tp.arr[0].n).toBe(2)
  })

  it('copyApiBody 复制请求体到剪贴板', async () => {
    const w = mountPage()
    const writeSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    w.vm.copyApiBody('{"x":1}')
    await new Promise((r) => setTimeout(r, 10))
    expect(writeSpy).toHaveBeenCalledWith('{"x":1}')
    vi.restoreAllMocks()
  })
})
