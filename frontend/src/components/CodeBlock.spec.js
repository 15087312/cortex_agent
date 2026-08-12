import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import CodeBlock from './CodeBlock.vue'

describe('CodeBlock', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('渲染语言标签与复制按钮', () => {
    const w = mount(CodeBlock, { props: { language: 'js', code: 'const x=1' }, global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('js')
    expect(w.text()).toContain('复制')
  })

  it('有高亮 HTML 时渲染之', () => {
    const w = mount(CodeBlock, { props: { language: 'js', code: 'x', highlightedHtml: '<span class="hl">x</span>' }, global: { plugins: [createTestPinia()] } })
    expect(w.find('.code-block .hl').exists()).toBe(true)
  })

  it('无高亮时渲染 pre>code', () => {
    const w = mount(CodeBlock, { props: { language: '', code: 'plain' }, global: { plugins: [createTestPinia()] } })
    expect(w.find('pre code').text()).toBe('plain')
  })

  it('点击复制成功显示已复制，2s 后恢复', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true, writable: true })
    const w = mount(CodeBlock, { props: { language: 'js', code: 'code', highlightedHtml: '' }, global: { plugins: [createTestPinia()] } })
    await w.find('.copy-btn').trigger('click')
    expect(writeText).toHaveBeenCalledWith('code')
    expect(w.text()).toContain('已复制')
    vi.advanceTimersByTime(2000)
    expect(w.text()).toContain('复制')
    const orig = globalThis.navigator.clipboard
    Object.defineProperty(navigator, 'clipboard', { value: orig, configurable: true, writable: true })
  })

  it('复制失败静默处理', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn(async () => { throw new Error('x') }) }, configurable: true, writable: true })
    const w = mount(CodeBlock, { props: { language: '', code: 'c', highlightedHtml: '' }, global: { plugins: [createTestPinia()] } })
    await w.find('.copy-btn').trigger('click')
    expect(w.text()).toContain('复制')
    Object.defineProperty(navigator, 'clipboard', { value: globalThis.navigator.clipboard, configurable: true, writable: true })
  })
})
