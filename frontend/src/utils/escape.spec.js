import { describe, it, expect } from 'vitest'
import { escapeHtml } from './escape.js'

describe('escapeHtml', () => {
  it('转义 < > & 特殊字符（innerHTML 序列化的实际行为）', () => {
    expect(escapeHtml('<script>alert(1)</script>')).toBe('&lt;script&gt;alert(1)&lt;/script&gt;')
    expect(escapeHtml('a & b')).toBe('a &amp; b')
  })

  it('引号保持原样（jsdom innerHTML 不转义引号，与浏览器一致）', () => {
    // 注意：textContent→innerHTML 序列化只转义 < > &，不转义引号
    expect(escapeHtml('"quoted"')).toBe('"quoted"')
  })

  it('空字符串/非字符串输入不崩溃', () => {
    expect(escapeHtml('')).toBe('')
    expect(escapeHtml(null)).toBe('')
    expect(escapeHtml(undefined)).toBe('')
  })
})
