import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { setActivePinia, createPinia } from 'pinia'
import { useWakeLock } from './useWakeLock.js'
import { useConfigStore } from '@/stores/config.js'

function makeHarness() {
  return defineComponent({
    setup() {
      const w = useWakeLock()
      return { ...w, activeRef: w.active }
    },
    template: '<div />',
  })
}

describe('useWakeLock', () => {
  let releaseSpy
  let wrapper

  beforeEach(() => {
    setActivePinia(createPinia())
    releaseSpy = vi.fn(async () => {})
    const sentinel = { release: releaseSpy, addEventListener: vi.fn() }
    vi.stubGlobal('navigator', {
      wakeLock: { request: vi.fn(async () => sentinel) },
    })
    // jsdom 默认 visibilityState 为 'visible'，无需设置
  })

  afterEach(() => {
    wrapper?.unmount()
    vi.unstubAllGlobals()
  })

  it('不支持 wakeLock 时 supported=false', async () => {
    vi.stubGlobal('navigator', {})
    wrapper = mount(makeHarness())
    await Promise.resolve()
    expect(wrapper.vm.supported).toBe(false)
  })

  it('配置开启时请求锁并置 active', async () => {
    const store = useConfigStore()
    store.config = { prevent_sleep: true }
    wrapper = mount(makeHarness())
    await Promise.resolve()
    await Promise.resolve()
    expect(navigator.wakeLock.request).toHaveBeenCalledWith('screen')
    expect(wrapper.vm.active).toBe(true)
  })

  it('配置关闭时释放锁', async () => {
    const store = useConfigStore()
    store.config = { prevent_sleep: false }
    wrapper = mount(makeHarness())
    await Promise.resolve()
    store.config = { prevent_sleep: true }
    await Promise.resolve()
    await Promise.resolve()
    store.config = { prevent_sleep: false }
    await Promise.resolve()
    await Promise.resolve()
    expect(releaseSpy).toHaveBeenCalled()
    expect(wrapper.vm.active).toBe(false)
  })

  it('request 失败时 active=false', async () => {
    vi.stubGlobal('navigator', { wakeLock: { request: vi.fn(async () => { throw new Error('denied') }) } })
    const store = useConfigStore()
    store.config = { prevent_sleep: true }
    wrapper = mount(makeHarness())
    await Promise.resolve()
    await Promise.resolve()
    expect(wrapper.vm.active).toBe(false)
  })

  it('页面重新可见时重新获取锁', async () => {
    const store = useConfigStore()
    store.config = { prevent_sleep: true }
    wrapper = mount(makeHarness())
    await Promise.resolve()
    await Promise.resolve()
    const calls = navigator.wakeLock.request.mock.calls.length
    document.dispatchEvent(new Event('visibilitychange'))
    await Promise.resolve()
    await Promise.resolve()
    expect(navigator.wakeLock.request.mock.calls.length).toBeGreaterThan(calls)
  })
})
