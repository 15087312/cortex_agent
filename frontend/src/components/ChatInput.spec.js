import { mount } from '@vue/test-utils'
import { describe, it, expect, afterEach, vi } from 'vitest'
import ChatInput from '@/components/ChatInput.vue'

// jsdom 无 FileReader：用同步假实现模拟 dataURL 读取
class FakeFileReader {
  onload = null
  readAsDataURL() {
    this.onload?.({ target: { result: 'data:image/png;base64,AAABB=' } })
  }
}

function makeFile(name, type) {
  return new File(['dummy'], name, { type })
}

function mountInput() {
  return mount(ChatInput, {
    props: { processing: false, hint: '' },
    global: { stubs: { Icon: true } },
  })
}

async function addImage(wrapper, name = 't.png', type = 'image/png') {
  vi.stubGlobal('FileReader', FakeFileReader)
  const fileInput = wrapper.find('input[type="file"]')
  Object.defineProperty(fileInput.element, 'files', {
    value: [makeFile(name, type)],
    configurable: true,
  })
  await fileInput.trigger('change')
}

async function sendClick(wrapper, text) {
  if (text !== undefined) await wrapper.find('textarea').setValue(text)
  await wrapper.find('button.chat-send-btn').trigger('click')
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChatInput 发送载荷契约', () => {
  it('纯文本发送：attachments 为空数组', async () => {
    const wrapper = mountInput()
    await sendClick(wrapper, 'hello')
    const payload = wrapper.emitted('send')[0][0]
    expect(payload.text).toBe('hello')
    expect(payload.attachments).toEqual([])
    wrapper.unmount()
  })

  it('空输入不触发发送', async () => {
    const wrapper = mountInput()
    await sendClick(wrapper, '')
    expect(wrapper.emitted('send')).toBeUndefined()
    wrapper.unmount()
  })

  it('图片附件发送为 {type, name, data} 字典（历史 bug 回归：不得丢字段）', async () => {
    const wrapper = mountInput()
    await addImage(wrapper)
    await sendClick(wrapper, '看看这张图')
    const payload = wrapper.emitted('send')[0][0]
    expect(payload.attachments).toEqual([
      { type: 'image/png', name: 't.png', data: 'data:image/png;base64,AAABB=' },
    ])
    wrapper.unmount()
  })

  it('仅图片不带文字也能发送（历史 bug 回归：不得拦截）', async () => {
    const wrapper = mountInput()
    await addImage(wrapper)
    await sendClick(wrapper, '')
    const payload = wrapper.emitted('send')[0][0]
    expect(payload.text).toBe('')
    expect(payload.attachments).toHaveLength(1)
    wrapper.unmount()
  })

  it('多附件逐项保留 type/name/data', async () => {
    const wrapper = mountInput()
    await addImage(wrapper, 'a.png', 'image/png')
    await addImage(wrapper, 'b.jpg', 'image/jpeg')
    await sendClick(wrapper, '两张图')
    const payload = wrapper.emitted('send')[0][0]
    expect(payload.attachments).toHaveLength(2)
    expect(payload.attachments[0].type).toBe('image/png')
    expect(payload.attachments[0].name).toBe('a.png')
    expect(payload.attachments[1].type).toBe('image/jpeg')
    expect(payload.attachments[1].name).toBe('b.jpg')
    wrapper.unmount()
  })
})

describe('ChatInput 交互细节', () => {
  it('Enter 发送、Shift+Enter 不发送（换行）', async () => {
    const wrapper = mountInput()
    const ta = wrapper.find('textarea')
    await ta.setValue('回车发送')
    await ta.trigger('keydown', { key: 'Enter', shiftKey: false })
    expect(wrapper.emitted('send')).toHaveLength(1)
    // Shift+Enter
    await ta.setValue('换行')
    await ta.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toHaveLength(1)
    wrapper.unmount()
  })

  it('processing 时发送按钮禁用且 handleSend 不触发', async () => {
    const wrapper = mount(ChatInput, {
      props: { processing: true, hint: '思考中...' },
      global: { stubs: { Icon: true } },
    })
    const btn = wrapper.find('button.chat-send-btn')
    expect(btn.attributes('disabled')).toBeDefined()
    await wrapper.find('textarea').setValue('内容')
    await btn.trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()
    // placeholder 提示思考中
    expect(wrapper.find('textarea').attributes('placeholder')).toContain('AI 思考中')
    wrapper.unmount()
  })

  it('非图片大文件（>1MB）拦截并 toast', async () => {
    const wrapper = mountInput()
    const big = new File([new Uint8Array(1024 * 1024 + 1)], 'big.txt', { type: 'text/plain' })
    const fileInput = wrapper.find('input[type="file"]')
    Object.defineProperty(fileInput.element, 'files', { value: [big], configurable: true })
    await fileInput.trigger('change')
    const toast = wrapper.emitted('toast')
    expect(toast).toBeTruthy()
    expect(toast[0][0].message).toContain('文件过大')
    expect(wrapper.vm.attachments).toHaveLength(0)
    wrapper.unmount()
  })

  it('removeAttachment 移除指定附件', async () => {
    const wrapper = mountInput()
    await addImage(wrapper, 'a.png', 'image/png')
    await addImage(wrapper, 'b.jpg', 'image/jpeg')
    expect(wrapper.vm.attachments).toHaveLength(2)
    wrapper.vm.removeAttachment(0)
    expect(wrapper.vm.attachments).toHaveLength(1)
    expect(wrapper.vm.attachments[0].name).toBe('b.jpg')
    wrapper.unmount()
  })

  it('粘贴图片触发附件添加', async () => {
    const wrapper = mountInput()
    vi.stubGlobal('FileReader', FakeFileReader)
    const file = makeFile('paste.png', 'image/png')
    const clipboardData = { items: [{ type: 'image/png', getAsFile: () => file }] }
    const ta = wrapper.find('textarea')
    await ta.trigger('paste', { clipboardData })
    expect(wrapper.vm.attachments).toHaveLength(1)
    expect(wrapper.vm.attachments[0].type).toBe('image/png')
    wrapper.unmount()
  })

  it('粘贴非图片内容不处理', async () => {
    const wrapper = mountInput()
    const clipboardData = { items: [{ type: 'text/plain', getAsFile: () => null }] }
    await wrapper.find('textarea').trigger('paste', { clipboardData })
    expect(wrapper.vm.attachments).toHaveLength(0)
    wrapper.unmount()
  })

  it('拖放文件添加附件；拖入时显示 drag-over 样式', async () => {
    const wrapper = mountInput()
    vi.stubGlobal('FileReader', FakeFileReader)
    const area = wrapper.find('.chat-input-area')
    await area.trigger('dragover', { dataTransfer: { types: ['Files'] } })
    expect(wrapper.vm.dragging).toBe(true)
    const file = makeFile('drop.png', 'image/png')
    await area.trigger('drop', { dataTransfer: { files: [file] } })
    expect(wrapper.vm.dragging).toBe(false)
    expect(wrapper.vm.attachments).toHaveLength(1)
    wrapper.unmount()
  })

  it('拖入后 dragleave 清除 drag-over 样式', async () => {
    const wrapper = mountInput()
    const area = wrapper.find('.chat-input-area')
    await area.trigger('dragover', { dataTransfer: { types: ['Files'] } })
    expect(wrapper.vm.dragging).toBe(true)
    await area.trigger('dragleave')
    expect(wrapper.vm.dragging).toBe(false)
    wrapper.unmount()
  })

  it('cortex-focus-input 事件聚焦输入框', async () => {
    const wrapper = mountInput()
    const ta = wrapper.find('textarea')
    const focusSpy = vi.spyOn(ta.element, 'focus')
    window.dispatchEvent(new CustomEvent('cortex-focus-input'))
    expect(focusSpy).toHaveBeenCalled()
    // 卸载后不再监听
    wrapper.unmount()
    focusSpy.mockClear()
    window.dispatchEvent(new CustomEvent('cortex-focus-input'))
    expect(focusSpy).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('发送后清空输入与附件', async () => {
    const wrapper = mountInput()
    await addImage(wrapper)
    await sendClick(wrapper, '发送后清空')
    expect(wrapper.vm.input).toBe('')
    expect(wrapper.vm.attachments).toHaveLength(0)
    wrapper.unmount()
  })
})
