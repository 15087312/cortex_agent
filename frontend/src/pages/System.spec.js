import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import System from './System.vue'
import { routeFetch } from '@/test/helpers.js'

let wrapper = null
function mountPage() {
  wrapper = mount(System, {
    global: { plugins: [createPinia()] },
  })
  return wrapper
}

const ROOT = (u) => u.endsWith('/api/')

describe('System 页面', () => {
  afterEach(() => {
    // 卸载组件 → onBeforeUnmount 清理 30s 轮询定时器
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })
  it('加载系统/思考/数据库/健康信息并渲染', async () => {
    routeFetch([
      { match: ROOT, data: { data: { version: '1.2.3', name: 'CortexAgent' } } },
      { match: '/management/thinking', data: { data: { status: 'healthy' } } },
      { match: '/management/database', data: { data: { type: 'sqlite', tables: ['events'], cache: { hits: 3 } } } },
      { match: '/health', data: { data: { status: 'healthy', version: '1.2.3' } } },
      { match: '/attention/status', data: { data: { status: 'healthy' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    const text = w.text()
    expect(text).toContain('1.2.3')
    expect(text).toContain('CortexAgent')
    expect(text).toContain('sqlite')
    expect(text).toContain('healthy')
  })

  it('单端点失败不影响整体渲染', async () => {
    routeFetch([
      { match: ROOT, data: { data: { version: '9.9' } } },
      { match: '/health', data: { data: { status: 'healthy' } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.text()).toContain('9.9')
  })

  it('badgeClass 与 attBadge 映射', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    expect(w.vm.badgeClass(true)).toBe('badge-green')
    expect(w.vm.badgeClass(false)).toBe('badge-red')
    expect(w.vm.attBadge('healthy')).toBe('badge-green')
    expect(w.vm.attBadge(true)).toBe('badge-red')
    expect(w.vm.attBadge(undefined)).toBe('badge-gray')
  })
})
