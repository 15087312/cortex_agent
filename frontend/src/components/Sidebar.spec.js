import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createTestPinia } from '@/test/helpers.js'
import { useThemeStore } from '@/stores/theme.js'
import pkg from '../../package.json'
import Sidebar from './Sidebar.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', redirect: '/chat' },
      { path: '/chat', component: { template: '<div>chat</div>' } },
      { path: '/dashboard', component: { template: '<div>dash</div>' } },
      { path: '/settings', component: { template: '<div>set</div>' } },
    ],
  })
}

describe('Sidebar', () => {
  let router
  beforeEach(() => {
    router = makeRouter()
    localStorage.clear()
  })

  it('渲染导航项与版本', async () => {
    const w = mount(Sidebar, { global: { plugins: [createTestPinia(), router] } })
    await router.isReady()
    expect(w.text()).toContain('Cortex Agent')
    expect(w.text()).toContain('对话')
    expect(w.text()).toContain('设置')
    // 版本号从 package.json 读取（与 vite define __APP_VERSION__ 同源），发版不破测试
    expect(w.text()).toContain('v' + pkg.version)
  })

  it('点击导航跳转', async () => {
    const w = mount(Sidebar, { global: { plugins: [createTestPinia(), router] } })
    await router.isReady()
    const items = w.findAll('.nav-item')
    const chat = items.find(i => i.text().includes('对话'))
    await chat.trigger('click')
    expect(router.currentRoute.value.path).toBe('/chat')
  })

  it('开发者分组默认折叠，点击展开', async () => {
    const w = mount(Sidebar, { global: { plugins: [createTestPinia(), router] } })
    await router.isReady()
    expect(w.text()).not.toContain('因果图')
    await w.find('.dev-toggle').trigger('click')
    expect(w.text()).toContain('因果图')
    expect(w.text()).toContain('安全')
  })

  it('主题切换按钮调用 store', async () => {
    const w = mount(Sidebar, { global: { plugins: [createTestPinia(), router] } })
    await router.isReady()
    const theme = useThemeStore()
    theme.init()
    await w.find('.theme-toggle').trigger('click')
    expect(theme.isDark).toBe(true)
  })

  it('当前路由高亮 active', async () => {
    await router.push('/dashboard')
    const w = mount(Sidebar, { global: { plugins: [createTestPinia(), router] } })
    await router.isReady()
    const items = w.findAll('.nav-item')
    const dash = items.find(i => i.text().includes('仪表盘'))
    expect(dash.classes()).toContain('active')
  })
})
