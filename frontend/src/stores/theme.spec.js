import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useThemeStore } from './theme.js'

describe('useThemeStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('init 默认 light 并应用', () => {
    const t = useThemeStore()
    t.init()
    expect(t.theme).toBe('light')
    expect(document.body.getAttribute('data-theme')).toBe('light')
  })

  it('init 从 localStorage 读取', () => {
    localStorage.setItem('cortex_theme', 'dark')
    const t = useThemeStore()
    t.init()
    expect(t.theme).toBe('dark')
    expect(t.isDark).toBe(true)
  })

  it('toggle 切换主题并持久化', () => {
    const t = useThemeStore()
    t.init()
    t.toggle()
    expect(t.theme).toBe('dark')
    expect(localStorage.getItem('cortex_theme')).toBe('dark')
    expect(document.body.getAttribute('data-theme')).toBe('dark')
    t.toggle()
    expect(t.theme).toBe('light')
  })
})
