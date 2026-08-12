import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useToastStore } from './toast.js'

describe('useToastStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('show 追加 toast 并在 3.5s 后自动移除', () => {
    const t = useToastStore()
    t.show('消息', 'success')
    expect(t.toasts).toHaveLength(1)
    expect(t.toasts[0].msg).toBe('消息')
    expect(t.toasts[0].type).toBe('success')
    vi.advanceTimersByTime(3500)
    expect(t.toasts).toHaveLength(0)
  })

  it('dismiss 手动移除', () => {
    const t = useToastStore()
    t.show('a')
    const id = t.toasts[0].id
    t.dismiss(id)
    expect(t.toasts).toHaveLength(0)
  })

  it('show 多次生成递增 id', () => {
    const t = useToastStore()
    t.show('a')
    t.show('b')
    expect(t.toasts[0].id).toBe(1)
    expect(t.toasts[1].id).toBe(2)
  })
})
