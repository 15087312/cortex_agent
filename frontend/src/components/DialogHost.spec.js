import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestPinia } from '@/test/helpers.js'
import { useConfirm, usePrompt, dialogState } from '@/composables/useDialog.js'
import DialogHost from './DialogHost.vue'

describe('DialogHost', () => {
  it('confirm 弹窗：确定 resolve true', async () => {
    const w = mount(DialogHost, { global: { plugins: [createTestPinia()] } })
    const confirm = useConfirm()
    const p = confirm('确定删除？', '确认')
    await w.vm.$nextTick()
    expect(w.text()).toContain('确定删除？')
    await w.findAll('.modal-actions .btn')[1].trigger('click')
    await expect(p).resolves.toBe(true)
  })

  it('confirm 弹窗：取消 resolve false，点遮罩同样取消', async () => {
    const w = mount(DialogHost, { global: { plugins: [createTestPinia()] } })
    const confirm = useConfirm()
    const p = confirm('确定？')
    await w.vm.$nextTick()
    await w.findAll('.modal-actions .btn')[0].trigger('click')
    await expect(p).resolves.toBe(false)
  })

  it('prompt 弹窗：确定 resolve 输入值，回车提交', async () => {
    const w = mount(DialogHost, { global: { plugins: [createTestPinia()] } })
    const prompt = usePrompt()
    const p = prompt('输入名称', '默认值')
    await w.vm.$nextTick()
    const input = w.find('input')
    expect(input.element.value).toBe('默认值')
    await input.setValue('新名称')
    await input.trigger('keydown.enter')
    await expect(p).resolves.toBe('新名称')
  })

  it('prompt 弹窗：取消 resolve null，Esc 取消', async () => {
    const w = mount(DialogHost, { global: { plugins: [createTestPinia()] } })
    const prompt = usePrompt()
    const p = prompt('输入')
    await w.vm.$nextTick()
    await w.find('input').trigger('keydown.esc')
    await expect(p).resolves.toBeNull()
  })

  it('遮罩点击取消', async () => {
    const w = mount(DialogHost, { global: { plugins: [createTestPinia()] } })
    const confirm = useConfirm()
    const p = confirm('确定？')
    await w.vm.$nextTick()
    await w.find('.modal-overlay').trigger('click')
    await expect(p).resolves.toBe(false)
  })
})
