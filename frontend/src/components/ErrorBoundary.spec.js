import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createTestPinia } from '@/test/helpers.js'
import ErrorBoundary from './ErrorBoundary.vue'

const Boom = defineComponent({
  name: 'Boom',
  setup() {
    throw new Error('渲染崩溃')
  },
})

const Wrapper = defineComponent({
  components: { ErrorBoundary, Boom },
  template: '<ErrorBoundary><Boom /></ErrorBoundary>',
})

describe('ErrorBoundary', () => {
  it('正常子组件渲染 slot', () => {
    const w = mount(defineComponent({
      components: { ErrorBoundary },
      template: '<ErrorBoundary><div>正常内容</div></ErrorBoundary>',
    }), { global: { plugins: [createTestPinia()] } })
    expect(w.text()).toContain('正常内容')
  })

  it('子组件抛错时显示错误，重试会重新渲染子组件', async () => {
    const onError = vi.spyOn(console, 'error').mockImplementation(() => {})
    const w = mount(Wrapper, { global: { plugins: [createTestPinia()] } })
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('页面加载失败')
    expect(w.text()).toContain('渲染崩溃')
    // 重试：重新渲染子组件；Boom 仍抛错 → 错误再次捕获（错误边界正确行为）
    await w.find('button').trigger('click')
    await new Promise(r => setTimeout(r, 0))
    expect(w.text()).toContain('页面加载失败')
    expect(w.text()).toContain('渲染崩溃')
    onError.mockRestore()
  })

})
