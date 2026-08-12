import { describe, it, expect, vi } from 'vitest'
import { useConfirm, usePrompt, resolveDialog, dialogState } from './useDialog.js'

describe('useDialog', () => {
  beforeEach(() => {
    const s = dialogState()
    s.visible = false
    s._resolve = null
  })

  it('useConfirm 打开确认弹窗并 resolve 布尔值', async () => {
    const confirm = useConfirm()
    const p = confirm('确定吗？', '标题')
    const s = dialogState()
    expect(s.visible).toBe(true)
    expect(s.type).toBe('confirm')
    expect(s.message).toBe('确定吗？')
    expect(s.title).toBe('标题')
    resolveDialog(true)
    await expect(p).resolves.toBe(true)
    expect(s.visible).toBe(false)
  })

  it('usePrompt 打开输入弹窗并 resolve 输入值', async () => {
    const prompt = usePrompt()
    const p = prompt('输入名称', '默认')
    const s = dialogState()
    expect(s.type).toBe('prompt')
    expect(s.defaultValue).toBe('默认')
    expect(s.title).toBe('')
    resolveDialog('新值')
    await expect(p).resolves.toBe('新值')
  })

  it('resolveDialog 无 pending resolve 时安全 noop', () => {
    const s = dialogState()
    s._resolve = null
    expect(() => resolveDialog('x')).not.toThrow()
    expect(s.visible).toBe(false)
  })
})
