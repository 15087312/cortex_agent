import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createTestPinia, stubFetch } from '@/test/helpers.js'
import ChatMessage from './ChatMessage.vue'

function mountMsg(message, opts = {}) {
  return mount(ChatMessage, {
    props: { message, index: opts.index ?? 0, isStreaming: opts.isStreaming ?? false },
    global: { plugins: [createTestPinia()] },
  })
}

describe('ChatMessage', () => {
  beforeEach(() => {
    // pet actions fetch
    stubFetch({ data: { actions: [] } })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('审批横幅 (approval)', () => {
    it('未解决时显示批准/拒绝按钮，点击 emit approve', async () => {
      const wrapper = mountMsg({ kind: 'approval', requestId: 'r1', target: 'exec_command', detail: 'rm -rf', resolved: false })
      expect(wrapper.text()).toContain('等待审批')
      expect(wrapper.text()).toContain('exec_command')
      await wrapper.find('.btn-danger').trigger('click')
      expect(wrapper.emitted('approve')[0]).toEqual(['r1', false])
      await wrapper.find('.btn-primary').trigger('click')
      expect(wrapper.emitted('approve')[1]).toEqual(['r1', true])
    })

    it('已解决时显示已批准/已拒绝', () => {
      const wrapper = mountMsg({ kind: 'approval', requestId: 'r1', resolved: true, approved: true })
      expect(wrapper.text()).toContain('已批准')
      const w2 = mountMsg({ kind: 'approval', requestId: 'r2', resolved: true, approved: false })
      expect(w2.text()).toContain('已拒绝')
    })
  })

  describe('提问面板 (intent)', () => {
    it('带选项时渲染选项按钮，点击 emit answer-intent', async () => {
      const wrapper = mountMsg({ kind: 'intent', requestId: 'i1', question: '选哪个？', options: ['A', 'B'], answered: false })
      expect(wrapper.text()).toContain('模型需要你确认')
      expect(wrapper.text()).toContain('选哪个？')
      const btns = wrapper.findAll('.intent-box .btn')
      expect(btns).toHaveLength(2)
      await btns[1].trigger('click')
      expect(wrapper.emitted('answer-intent')[0]).toEqual(['i1', 'B'])
    })

    it('无选项时显示输入框，提交后 emit', async () => {
      const wrapper = mountMsg({ kind: 'intent', requestId: 'i1', question: '输入答案', options: [], answered: false })
      const input = wrapper.find('input')
      await input.setValue('自定义答案')
      await wrapper.find('.intent-box .btn-primary').trigger('click')
      expect(wrapper.emitted('answer-intent')[0]).toEqual(['i1', '自定义答案'])
    })

    it('空输入不提交', async () => {
      const wrapper = mountMsg({ kind: 'intent', requestId: 'i1', question: 'q', options: [], answered: false })
      await wrapper.find('.intent-box .btn-primary').trigger('click')
      expect(wrapper.emitted('answer-intent')).toBeUndefined()
    })

    it('已回答时显示回答内容', () => {
      const wrapper = mountMsg({ kind: 'intent', requestId: 'i1', question: 'q', options: [], answered: true, answer: '已选B' })
      expect(wrapper.text()).toContain('已回答：已选B')
    })
  })

  describe('用户消息', () => {
    it('渲染用户名与文本', async () => {
      const wrapper = mountMsg({ role: 'user', content: '你好\n世界' })
      await flushPromises()
      expect(wrapper.text()).toContain('我')
      expect(wrapper.find('.message-bubble').html()).toContain('你好<br>世界')
    })

    it('带图片时渲染附件缩略图', () => {
      const wrapper = mountMsg({ role: 'user', content: '', images: ['data:image/png;base64,x'] })
      expect(wrapper.findAll('.user-attachment-img')).toHaveLength(1)
    })

    it('带 id 时显示编辑按钮，点击 emit edit', async () => {
      const wrapper = mountMsg({ role: 'user', content: 'x', id: 'm1' })
      await wrapper.findAll('.msg-action')[1].trigger('click')
      expect(wrapper.emitted('edit')[0]).toEqual([0])
    })

    it('无 id 时显示保存中', () => {
      const wrapper = mountMsg({ role: 'user', content: 'x' })
      expect(wrapper.text()).toContain('保存中…')
    })

    it('复制/删除按钮 emit copy/delete（无 id 时仅两个操作按钮）', async () => {
      const wrapper = mountMsg({ role: 'user', content: 'x' })
      const actions = wrapper.findAll('.msg-action')
      expect(actions).toHaveLength(2) // copy + delete（edit 需要 id）
      await actions[0].trigger('click')
      expect(wrapper.emitted('copy')[0]).toEqual([0])
      await actions[1].trigger('click')
      expect(wrapper.emitted('delete')[0]).toEqual([0])
    })
  })

  describe('AI 消息', () => {
    it('渲染 markdown 分段与身份名', async () => {
      const wrapper = mountMsg({ role: 'assistant', content: '# 标题\n\n代码块\n```js\nconst a = 1\n```', identity_name: '总指挥' })
      await flushPromises()
      expect(wrapper.text()).toContain('总指挥')
      expect(wrapper.find('.message-bubble').html()).toContain('<h1>标题</h1>')
    })

    it('思考折叠区可展开', async () => {
      const wrapper = mountMsg({ role: 'assistant', content: '答', thinking: '思考过程' })
      await flushPromises()
      expect(wrapper.find('.think-collapse').exists()).toBe(true)
      await wrapper.find('.think-collapse').trigger('click')
      expect(wrapper.find('.think-collapse-text').text()).toContain('思考过程')
    })

    it('错误消息带 error 样式', () => {
      const wrapper = mountMsg({ role: 'assistant', content: '[安全拦截] x', error: true })
      expect(wrapper.find('.bubble-error').exists()).toBe(true)
    })

    it('meta 思考详情展开', async () => {
      const wrapper = mountMsg({ role: 'assistant', content: '答', meta: { innerMonologue: '内心', eventMemory: '事件', sessionMemory: '会话' } })
      await flushPromises()
      await wrapper.find('.meta-collapse-btn').trigger('click')
      expect(wrapper.text()).toContain('内心独白')
      expect(wrapper.text()).toContain('事件记忆')
      expect(wrapper.text()).toContain('会话记忆')
    })
  })

  describe('thinking/mental 消息', () => {
    it('thinking 短内容直接显示，长内容可展开', async () => {
      const wrapper = mountMsg({ kind: 'thinking', role: 'thinking', content: '思考' })
      expect(wrapper.text()).toContain('思考')
      expect(wrapper.text()).toContain('思考')
      const w2 = mountMsg({ kind: 'thinking', role: 'thinking', content: 'x'.repeat(200) })
      expect(w2.text()).toContain('展开 ▼')
      await w2.find('.thinking-box').trigger('click')
      expect(w2.text()).toContain('收起 ▲')
    })

    it('mental 消息渲染心理活动', () => {
      const wrapper = mountMsg({ kind: 'mental', role: 'system', content: '心理活动内容' })
      expect(wrapper.text()).toContain('心理活动')
      expect(wrapper.text()).toContain('心理活动内容')
    })
  })

  describe('打字机效果', () => {
    it('typing 消息逐字显示 cursor，完成后消失', async () => {
      vi.useFakeTimers()
      const wrapper = mountMsg({ role: 'assistant', content: 'hello world', typing: true })
      await flushPromises()
      // 只推进一步（18ms）：仍在打字中 → 显示 cursor
      vi.advanceTimersByTime(18)
      await flushPromises()
      expect(wrapper.find('.streaming-cursor').exists()).toBe(true)
      // 推进足够长：打字完成 → cursor 消失
      vi.advanceTimersByTime(500)
      await flushPromises()
      expect(wrapper.find('.streaming-cursor').exists()).toBe(false)
      wrapper.unmount()
      vi.useRealTimers()
    })
  })
})

describe('主管/专家输出气泡', () => {
  it('kind=expert 显示身份名与内容', () => {
    const wrapper = mountMsg({ kind: 'expert', name: '代码主管', content: '正在拆分任务' })
    expect(wrapper.text()).toContain('代码主管')
    expect(wrapper.text()).toContain('正在拆分任务')
  })

  it('expert 气泡显示思考过程折叠与工具调用列表', () => {
    const wrapper = mountMsg({
      kind: 'expert', name: '实现专家', content: '已完成实现',
      _thinking: '【实现专家】分析依赖\n【实现专家】选择方案',
      _tools: ['todo: done (50 chars)', 'read_file: done (312 chars)'],
      _expanded: false,
    })
    expect(wrapper.text()).toContain('实现专家')
    // 思考过程默认折叠展示（shortOf 截断）
    expect(wrapper.text()).toContain('【实现专家】')
    // 工具调用列表
    expect(wrapper.text()).toContain('todo: done (50 chars)')
    expect(wrapper.text()).toContain('read_file: done (312 chars)')
  })

  it('expert 气泡 Markdown 内容渲染代码块', () => {
    const wrapper = mountMsg({ kind: 'expert', name: '实现专家', content: '```js\nconst a = 1\n```' })
    expect(wrapper.find('.code-block').exists()).toBe(true)
    expect(wrapper.text()).toContain('const a = 1')
  })
})
