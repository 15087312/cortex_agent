import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useI18n } from '@/i18n/init.js'
import { endpoints } from '@/api.js'

const LOCALE_KEY = 'cortex_locale'

export const useLocaleStore = defineStore('locale', () => {
  const locale = ref('zh')

  function _get(k) { try { return localStorage.getItem(k) } catch { return null } }
  function _set(k, v) { try { localStorage.setItem(k, v) } catch {} }

  function init() {
    locale.value = _get(LOCALE_KEY) || 'zh'
    apply()
  }

  function apply() {
    try { useI18n().global.locale.value = locale.value } catch {}
    try { document.documentElement.setAttribute('lang', locale.value) } catch {}
  }

  function setLocale(code) {
    locale.value = code
    _set(LOCALE_KEY, code)
    apply()
    // 同步后端：让 AI 的回复语言跟随用户界面语言（后端据此注入【用户语言】指令）
    try { endpoints.updateConfig('user_language', code).catch(() => {}) } catch {}
  }

  function toggle() {
    setLocale(locale.value === 'zh' ? 'en' : 'zh')
  }

  return { locale, init, setLocale, toggle, apply }
})