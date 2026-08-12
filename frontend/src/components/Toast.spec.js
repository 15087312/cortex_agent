import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import { useToastStore } from '@/stores/toast.js'
import Toast from './Toast.vue'

describe('Toast', () => {
  let pinia
  beforeEach(() => {
    pinia = createTestPinia()
    vi.useFakeTimers()
  })
  afterEach(() => vi.useRealTimers())

  it('渲染 toast 列表并可点击关闭', async () => {
    const wrapper = mount(Toast, { global: { plugins: [pinia] } })
    const toast = useToastStore()
    toast.show('消息1', 'success')
    toast.show('消息2', 'error')
    await wrapper.vm.$nextTick()
    const alerts = wrapper.findAll('.alert')
    expect(alerts).toHaveLength(2)
    expect(wrapper.text()).toContain('消息1')
    expect(wrapper.text()).toContain('消息2')
    expect(alerts[0].classes()).toContain('alert-success')
    expect(alerts[1].classes()).toContain('alert-error')
    await alerts[0].trigger('click')
    expect(toast.toasts).toHaveLength(1)
  })

  it('自动超时移除', async () => {
    const wrapper = mount(Toast, { global: { plugins: [pinia] } })
    const toast = useToastStore()
    toast.show('x')
    vi.advanceTimersByTime(3500)
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.alert')).toHaveLength(0)
  })
})
