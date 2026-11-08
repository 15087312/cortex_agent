import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import fs from 'node:fs'
import nodePath from 'node:path'

// 后端配置同步用 mock，避免真实 fetch
const updateConfig = vi.fn(() => Promise.resolve({}))
vi.mock('@/api.js', () => ({ endpoints: { updateConfig: (...a) => updateConfig(...a) } }))

import zh from './locales/zh.js'
import en from './locales/en.js'
import { locales } from './index.js'
import { useI18n, install, setLocale } from './init.js'
import { useLocaleStore } from '@/stores/locale.js'

// 把嵌套 messages 拍平成 `a.b.c` 键，便于逐键对比两套语言
function flatten(obj, prefix = '', out = []) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) flatten(v, key, out)
    else out.push([key, v])
  }
  return out
}

const zhFlat = flatten(zh)
const enFlat = flatten(en)
const zhKeys = zhFlat.map(([k]) => k)
const enKeys = enFlat.map(([k]) => k)

describe('i18n 语言包一致性', () => {
  it('注册 zh / en 两套语言', () => {
    expect(Object.keys(locales)).toEqual(['zh', 'en'])
    expect(locales.zh.name).toBe('简体中文')
    expect(locales.en.name).toBe('English')
  })

  it('zh 与 en 的键集合完全一致（缺键会导致界面回退英文/显示 key）', () => {
    expect(zhKeys.filter(k => !enKeys.includes(k))).toEqual([])
    expect(enKeys.filter(k => !zhKeys.includes(k))).toEqual([])
  })

  it('所有文案均为非空字符串', () => {
    for (const [key, value] of [...zhFlat, ...enFlat]) {
      expect(typeof value, key).toBe('string')
      expect(value.trim(), key).not.toBe('')
    }
  })

  it('中文包不残留常见乱码/错字（帳/顔 等繁体或误录）', () => {
    const bad = zhFlat.filter(([, v]) => /帳|顔|囘/.test(v))
    expect(bad).toEqual([])
  })

  it('占位符在两套语言中一致（{name} / {count} 等）', () => {
    const ph = s => (String(s).match(/\{[a-zA-Z_]+\}/g) || []).sort()
    const enMap = new Map(enFlat)
    const mismatched = zhFlat
      .filter(([k]) => enMap.has(k))
      .filter(([k, v]) => JSON.stringify(ph(v)) !== JSON.stringify(ph(enMap.get(k))))
      .map(([k]) => k)
    expect(mismatched).toEqual([])
  })
})

describe('界面静态文案均可解析', () => {
  // 扫描 src 下所有 .vue/.js（排除语言包与测试），收集 $t('x') / t('x') 的静态键
  function walk(dir, out = []) {
    for (const name of fs.readdirSync(dir)) {
      const p = nodePath.join(dir, name)
      const st = fs.statSync(p)
      if (st.isDirectory()) {
        if (name !== 'i18n') walk(p, out)
      } else if (/\.(vue|js)$/.test(name) && !p.endsWith('.spec.js')) {
        out.push(p)
      }
    }
    return out
  }

  it('页面/组件里用到的 key 在 zh 与 en 中都存在（拼错会直接显示原始 key）', () => {
    const files = walk(nodePath.resolve(process.cwd(), 'src'))
    const used = new Set()
    for (const f of files) {
      const src = fs.readFileSync(f, 'utf8')
      const re = /(?<![\w.])\$?t\(\s*['"`]([^'"`$]+)['"`]/g
      let m
      while ((m = re.exec(src))) used.add(m[1])
    }
    // 动态拼接的键（如 t(`runnerNode.status.${s}`)）以点结尾，不参与静态校验
    const statics = [...used].filter(k => !k.endsWith('.'))
    expect(statics.length).toBeGreaterThan(500)
    const zhSet = new Set(zhKeys)
    const enSet = new Set(enKeys)
    const missing = statics.filter(k => !zhSet.has(k) || !enSet.has(k)).sort()
    expect(missing).toEqual([])
  })
})

describe('i18n init', () => {
  it('useI18n 返回单例，默认 zh 且 en 兜底', () => {
    const a = useI18n()
    const b = useI18n()
    expect(a).toBe(b)
    expect(a.global.locale.value).toBe('zh')
    expect(a.global.fallbackLocale.value).toBe('en')
  })

  it('install 注册到 app 并返回实例', () => {
    const used = []
    const app = { use: (p) => used.push(p) }
    const inst = install(app)
    expect(used).toContain(inst)
  })

  it('setLocale 切换全局语言', () => {
    setLocale('en')
    expect(useI18n().global.locale.value).toBe('en')
    setLocale('zh')
    expect(useI18n().global.locale.value).toBe('zh')
  })
})

describe('useLocaleStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    updateConfig.mockClear()
  })

  afterEach(() => {
    localStorage.clear()
  })

  it('init 默认 zh 并同步 <html lang>', () => {
    const s = useLocaleStore()
    s.init()
    expect(s.locale).toBe('zh')
    expect(document.documentElement.getAttribute('lang')).toBe('zh')
  })

  it('init 读取 localStorage 中的语言', () => {
    localStorage.setItem('cortex_locale', 'en')
    const s = useLocaleStore()
    s.init()
    expect(s.locale).toBe('en')
    expect(useI18n().global.locale.value).toBe('en')
  })

  it('setLocale 持久化、应用到 i18n 与 html lang，并同步后端', async () => {
    const s = useLocaleStore()
    s.init()
    s.setLocale('en')
    expect(localStorage.getItem('cortex_locale')).toBe('en')
    expect(useI18n().global.locale.value).toBe('en')
    expect(document.documentElement.getAttribute('lang')).toBe('en')
    expect(updateConfig).toHaveBeenCalledWith('user_language', 'en')
  })

  it('toggle 在中英之间来回切换', () => {
    const s = useLocaleStore()
    s.init()
    s.toggle()
    expect(s.locale).toBe('en')
    s.toggle()
    expect(s.locale).toBe('zh')
  })

  it('后端同步失败不影响本地切换', async () => {
    updateConfig.mockImplementationOnce(() => Promise.reject(new Error('boom')))
    const s = useLocaleStore()
    s.init()
    expect(() => s.setLocale('en')).not.toThrow()
    expect(s.locale).toBe('en')
  })
})
