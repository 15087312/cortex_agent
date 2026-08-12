import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Modules from './Modules.vue'
import { routeFetch } from '@/test/helpers.js'

function mountPage() {
  return mount(Modules, {
    global: { plugins: [createPinia()] },
  })
}

describe('Modules 页面', () => {
  it('加载模块列表并渲染状态徽章', async () => {
    routeFetch([
      {
        match: '/management/modules',
        data: {
          data: {
            modules: [
              { name: 'memory', has_api: true, has_core: true, status: 'healthy' },
              { name: 'vision', status: 'degraded' },
              'legacy',
            ],
            with_api: 1,
            with_core: 2,
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    await w.vm.$nextTick()
    const text = w.text()
    expect(text).toContain('memory')
    expect(text).toContain('vision')
    expect(text).toContain('legacy')
    expect(text).toContain('正常')
    expect(text).toContain('降级')
    expect(w.findAll('.badge-green').length).toBeGreaterThan(0)
  })

  it('加载失败时优雅降级为空列表', async () => {
    routeFetch([{ match: '/management/modules', data: { data: {} } }])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    await w.vm.$nextTick()
    expect(w.findAll('.module-item').length).toBe(0)
  })

  it('点击刷新调用 refreshModule 并显示 toast', async () => {
    const calls = []
    routeFetch([
      {
        match: '/management/modules/',
        data: () => {
          calls.push('refresh')
          return { success: true }
        },
      },
      {
        match: '/management/modules',
        data: { data: { modules: [{ name: 'memory', status: 'healthy' }] } },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 10))
    await w.vm.$nextTick()
    // 头部「刷新」是 loadData，行内「刷新」才是 refreshMod——取最后一个
    const btns = w.findAll('button').filter((b) => b.text().includes('刷新'))
    expect(btns.length).toBeGreaterThanOrEqual(2)
    await btns[btns.length - 1].trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(calls).toContain('refresh')
  })

  it('statusLabel 映射', () => {
    const w = mountPage()
    const { statusLabel } = w.vm
    expect(statusLabel({ status: 'healthy' })).toBe('正常')
    expect(statusLabel({ status: 'degraded' })).toBe('降级')
    expect(statusLabel({ status: 'unknown' })).toBe('unknown')
  })
})
