import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const theme = ref('light')

  function _get(k) { try { return localStorage.getItem(k) } catch { return null } }
  function _set(k, v) { try { localStorage.setItem(k, v) } catch {} }

  function init() {
    theme.value = _get('cortex_theme') || 'light'
    apply()
  }

  function apply() {
    document.body.setAttribute('data-theme', theme.value)
  }

  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    _set('cortex_theme', theme.value)
    apply()
  }

  const isDark = computed(() => theme.value === 'dark')

  return { theme, init, toggle, isDark }
})
