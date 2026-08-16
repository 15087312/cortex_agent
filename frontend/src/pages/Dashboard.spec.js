import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Dashboard from './Dashboard.vue'
import { routeFetch } from '@/test/helpers.js'

const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: pushMock }) }))

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

  it('copyApiBody 剪贴板写入失败回退（.catch 分支）', async () => {
    const w = mountPage()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    expect(() => w.vm.copyApiBody('x')).not.toThrow()
    vi.restoreAllMocks()
  })

  it('recentSessions 按 last_active 倒序排序（sort 比较器）', async () => {
    routeFetch([
      { match: '/management/dashboard', data: { data: {} } },
      { match: '/stream/sessions', data: { data: [
        { session_id: 'old', title: '旧', last_active: '2024-01-01T00:00:00' },
        { session_id: 'new', title: '新', last_active: '2024-02-01T00:00:00' },
      ] } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.recentSessions[0].session_id).toBe('new')
    expect(w.vm.recentSessions[1].session_id).toBe('old')
  })

  it('加载失败容错（端点 reject 回退）', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.dash).toBeNull()
    expect(w.vm.apiStatus).toBe('-')
    expect(w.vm.libs).toEqual([])
    expect(w.vm.sessions).toEqual([])
  })

  it('DOM：路由卡片/会话行/管理规则按钮触发 router.push', async () => {
    routeFetch([
      { match: '/management/dashboard', data: { data: {} } },
      { match: '/stream/sessions', data: { data: [{ session_id: 'abc123', title: '会话', last_active: '2024-01-01T00:00:00' }] } },
      { match: '/management/api-requests/stats', data: { data: {} } },
      { match: '/management/api-requests', data: { data: { items: [], total: 0 } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    pushMock.mockClear()
    // API 状态卡 → /chat
    await w.findAll('.health-card')[0].trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/chat')
    // 记忆卡 → /memory
    await w.findAll('.health-card')[2].trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/memory')
    // 最近会话行
    await w.find('.dash-session-row').trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/chat?session=abc123')
    // 管理规则按钮
    const manageBtn = w.findAll('button').find((b) => b.text().includes('管理规则'))
    await manageBtn.trigger('click')
    expect(pushMock).toHaveBeenCalledWith('/outreach')
  })

  it('DOM：API 日志筛选下拉 + 详情按钮 + 复制/关闭', async () => {
    let reqUrl = ''
    routeFetch([
      { match: '/management/dashboard', data: { data: {} } },
      { match: '/management/api-requests/stats', data: { data: { total: 1, by_method: { GET: 1 }, by_status: { '2': 1 } } } },
      { match: '/management/api-requests', data: (u) => { reqUrl = u; return { data: { items: [{ id: 1, method: 'POST', path: '/x', status: 200, time: 't', ms: 5, request_body: '{"a":1}', response_body: '{"ok":true}' }], total: 1 } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    // stats 渲染（by_method / by_status v-for）
    expect(w.text()).toContain('GET')
    // 筛选下拉 v-model + @change
    const selects = w.findAll('.dash-filter-bar select')
    await selects[0].setValue('POST')
    expect(w.vm.apiReqFilter.method).toBe('POST')
    await w.vm.$nextTick()
    expect(reqUrl).toContain('method=POST')
    await selects[2].setValue('1')
    expect(w.vm.apiReqFilter.since_hours).toBe(1)
    await w.vm.$nextTick()
    expect(reqUrl).toContain('since_hours=1')
    // 日志详情按钮
    const detailBtn = w.findAll('button').find((b) => b.text().includes('详情'))
    await detailBtn.trigger('click')
    await w.vm.$nextTick()
    expect(w.vm.apiDetail?.id).toBe(1)
    // 复制请求/返回按钮
    const copySpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    const copyBtns = w.findAll('.dash-detail-actions button')
    await copyBtns[0].trigger('click')
    expect(copySpy).toHaveBeenCalledWith('{"a":1}')
    await copyBtns[1].trigger('click')
    expect(copySpy).toHaveBeenCalledWith('{"ok":true}')
    // 关闭按钮
    await copyBtns[2].trigger('click')
    expect(w.vm.apiDetail).toBeNull()
    vi.restoreAllMocks()
  })
})
