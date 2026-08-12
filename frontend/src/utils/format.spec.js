import { describe, it, expect, vi } from 'vitest'
import { formatTime } from './format.js'

describe('formatTime', () => {
  it('空值返回 -', () => {
    expect(formatTime(null)).toBe('-')
    expect(formatTime(undefined)).toBe('-')
    expect(formatTime('')).toBe('-')
  })

  it('无效时间戳回退为字符串截断', () => {
    expect(formatTime('not-a-date')).toBe('not-a-date')
  })

  it('有效时间戳本地化格式化', () => {
    const ts = new Date('2024-01-15T10:30:45').getTime()
    expect(formatTime(ts)).toBe(new Date(ts).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }))
  })
})
