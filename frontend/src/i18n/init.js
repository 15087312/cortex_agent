import { createI18n } from 'vue-i18n'
import { locales } from './index.js'

let _i18n = null

export function useI18n() {
  if (!_i18n) {
    _i18n = createI18n({
      legacy: false,
      locale: 'zh',
      fallbackLocale: 'en',
      messages: Object.fromEntries(
        Object.entries(locales).map(([code, l]) => [code, l.messages]),
      ),
    })
  }
  return _i18n
}

export function install(app) {
  const i18n = useI18n()
  app.use(i18n)
  return i18n
}

export function setLocale(code) {
  useI18n().global.locale.value = code
}