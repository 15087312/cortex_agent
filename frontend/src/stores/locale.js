import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useI18n } from '@/i18n/init.js'

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
    try { useI18n().value = locale.value } catch {}
    try { document.documentElement.setAttribute('lang', locale.value) } catch {}
  }

  function setLocale(code) {
    locale.value = code
    _set(LOCALE_KEY, code)
    apply()
  }

  function toggle() {
    setLocale(locale.value === 'zh' ? 'en' : 'zh')
  }

  return { locale, init, setLocale, toggle, apply }
})