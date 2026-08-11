import { mount } from '@vue/test-utils'
import { describe, it, expect, afterEach } from 'vitest'
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
