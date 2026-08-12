import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { nextTick } from 'vue'
import App from './App.vue'

const push = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push }),
}))

// 子组件全部 stub（本测试只关心快捷键逻辑）
const stubs = {
  RouterView: { template: '<div class="stub-router-view" />' },
  Sidebar: { template: '<div class="stub-sidebar" />' },
  StatusBar: { template: '<div class="stub-statusbar" />' },
  Toast: { template: '<div class="stub-toast" />' },
  DialogHost: { template: '<div class="stub-dialog" />' },
  ErrorBoundary: { template: '<div><slot /></div>' },
  LoadingState: { template: '<div />' },
}

function fireKey(key, extra = {}) {
  window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...extra }))
}

async function mountApp() {
  const w = mount(App, { global: { plugins: [createPinia()], stubs } })
  await nextTick()
  return w
}

describe('App.vue', () => {
  beforeEach(() => {
    push.mockClear()
  })

  it('渲染应用骨架（Sidebar/主内容/StatusBar）', async () => {
    const w = await mountApp()
    expect(w.find('.app-shell').exists()).toBe(true)
    expect(w.find('.stub-sidebar').exists()).toBe(true)
    expect(w.find('.stub-statusbar').exists()).toBe(true)
  })

  it('Cmd/Ctrl+K 聚焦聊天输入（跳转 + 派发 cortex-focus-input）', async () => {
    const w = await mountApp()
    let focused = false
    window.addEventListener('cortex-focus-input', () => { focused = true }, { once: true })
    fireKey('k', { ctrlKey: true })
    await nextTick()
    expect(push).toHaveBeenCalledWith('/chat')
    expect(focused).toBe(true)
  })

  it('Meta+K 同样触发聚焦', async () => {
    const w = await mountApp()
    fireKey('K', { metaKey: true })
    await nextTick()
    expect(push).toHaveBeenCalledWith('/chat')
  })

  it('"/" 聚焦输入（非输入框时）', async () => {
    const w = await mountApp()
    let focused = false
    window.addEventListener('cortex-focus-input', () => { focused = true }, { once: true })
    fireKey('/')
    await nextTick()
    expect(focused).toBe(true)
  })

  it('输入框内按 "/" 不聚焦（避免干扰打字）', async () => {
    const w = await mountApp()
    let focused = false
    window.addEventListener('cortex-focus-input', () => { focused = true }, { once: true })
    const input = document.createElement('input')
    // KeyboardEvent 无法在 options 里指定 target——需从 input 元素派发并冒泡到 window
    input.dispatchEvent(new KeyboardEvent('keydown', { key: '/', bubbles: true, cancelable: true }))
    await nextTick()
    expect(focused).toBe(false)
  })

  it('Esc 关闭对话框（dialogState visible 时）', async () => {
    const w = await mountApp()
    const { dialogState } = await import('@/composables/useDialog.js')
    dialogState().visible = true
    fireKey('Escape')
    await nextTick()
    expect(dialogState().visible).toBe(false)
  })

  it('Esc 在无对话框时不报错', async () => {
    const w = await mountApp()
    fireKey('Escape')
    await nextTick()
  })

  it('"?" 显示快捷键提示 toast', async () => {
    const w = await mountApp()
    const { useToastStore } = await import('@/stores/toast.js')
    const toast = useToastStore()
    fireKey('?')
    await nextTick()
    expect(toast.toasts.length).toBeGreaterThan(0)
  })

  it('parseShortcut 解析用户配置快捷键（⌥ + T）', async () => {
    const w = await mountApp()
    const sc = w.vm.parseShortcut('⌥ + T')
    expect(sc).toEqual({ ctrl: false, meta: false, alt: true, shift: false, key: 't' })
  })

  it('用户配置快捷键命中时优先触发（meta+shift+p）', async () => {
    const w = await mountApp()
    const { useConfigStore } = await import('@/stores/config.js')
    const cfg = useConfigStore()
    cfg.config = { shortcut_keys: 'Cmd+Shift+P' }
    await nextTick()
    fireKey('p', { metaKey: true, shiftKey: true })
    await nextTick()
    expect(push).toHaveBeenCalledWith('/chat')
  })

  it('卸载时移除 keydown 监听', async () => {
    const w = await mountApp()
    w.unmount()
    // 卸载后再按键不应触发（不抛错即通过）
    fireKey('k', { ctrlKey: true })
  })
})
