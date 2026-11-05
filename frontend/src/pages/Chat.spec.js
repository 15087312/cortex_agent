import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createTestPinia, stubFetch } from '@/test/helpers.js'
import { useChatStore } from '@/stores/chat.js'
import { useSessionStore } from '@/stores/session.js'
import { wsClient } from '@/ws/client.js'
import { endpoints } from '@/api.js'
import Chat from './Chat.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }, { path: '/chat', component: { template: '<div />' } }],
  })
}

describe('Chat 页面', () => {
  let router
  let wrapper = null

  beforeEach(() => {
    stubFetch({ data: { todos: [] } })
    router = makeRouter()
  })

  afterEach(() => {
    vi.restoreAllMocks()
    try { wsClient.disconnect() } catch {}
    // 统一卸载 wrapper → 触发 onUnmounted → 清理 watchTimer/todoTimer，避免跨测试定时器泄漏
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })

  async function mountChat() {
    const w = mount(Chat, { global: { plugins: [createTestPinia(), router] } })
    await router.isReady()
    await flushPromises()
    // 设置当前会话（事件分发按会话过滤）
    const session = useSessionStore()
    session.sessionId = 's1'
    wrapper = w
    return w
  }

  it('渲染页面结构', async () => {
    const w = await mountChat()
    expect(w.find('.chat-layout').exists()).toBe(true)
  })

  it('WS thinking 安全审批事件 → chat.addApproval', async () => {
    await mountChat()
    const chat = useChatStore()
    const sev = { event_type: 'security', action: '等待用户审批', target: 'exec_command' }
    wsClient._emit('thinking', {
      session_id: 's1',
      data: { payload: { request_id: 'r1', detail: '执行危险命令' }, stage_event: sev },
    })
    const approvals = chat.messages.filter(m => m.kind === 'approval')
    expect(approvals).toHaveLength(1)
    expect(approvals[0].requestId).toBe('r1')
    expect(approvals[0].target).toBe('exec_command')
  })

  it('WS tool 事件 → 结构化轨迹（名称/状态/耗时/结果预览）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_call', session_id: 's1',
      data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' }, status: 'running' },
    })
    expect(chat.traces).toHaveLength(1)
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_result', session_id: 's1',
      data: { call_id: 'c1', status: 'ok', duration_ms: 42, result_preview: 'print(1)', result_chars: 8 },
    })
    expect(chat.traces[0]).toMatchObject({ status: 'ok', durationMs: 42, preview: 'print(1)' })
    await w.vm.$nextTick()
    await w.find('.chat-traces-header').trigger('click')
    expect(w.text()).toContain('read_file')
    expect(w.text()).toContain('42ms')
    expect(w.text()).toContain('print(1)')
  })

  it('tool 事件带 view → 轨迹区渲染对应工具卡片（Phase 2）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_call', session_id: 's1',
      data: {
        call_id: 'c3', name: 'exec_command', args: { command: 'pytest -q' },
        view: { card: 'terminal', title: 'pytest -q', command: 'pytest -q', cwd: '/repo', exit_code: null, output: '' },
      },
    })
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_result', session_id: 's1',
      data: {
        call_id: 'c3', status: 'ok', duration_ms: 30,
        view: { card: 'terminal', title: 'pytest -q', command: 'pytest -q', cwd: '/repo', exit_code: 0, output: '3 passed' },
      },
    })
    expect(chat.traces[0].view.card).toBe('terminal')
    await w.vm.$nextTick()
    await w.find('.chat-traces-header').trigger('click')
    expect(w.find('.tool-card-terminal').exists()).toBe(true)
    expect(w.text()).toContain('exit 0')
    expect(w.find('.tool-card-output').text()).toContain('3 passed')
  })

  it('WS chunk 事件 → 流式尾巴气泡，最终 message 到达后替换（Phase 3）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    wsClient._emit('chunk', {
      type: 'chunk', event: 'chunk_delta', session_id: 's1', content: '你好',
      data: { tier: 'large', identity_name: '总指挥' },
    })
    await w.vm.$nextTick()
    expect(chat.streamTail.content).toBe('你好')
    expect(w.text()).toContain('你好')
    // 尾巴气泡不带操作按钮（未落定不可复制/删除）
    expect(w.findAll('.message-actions')).toHaveLength(0)

    wsClient._emit('chunk', {
      type: 'chunk', event: 'chunk_delta', session_id: 's1', content: '，世界',
      data: { tier: 'large', identity_name: '总指挥' },
    })
    await w.vm.$nextTick()
    expect(chat.streamTail.content).toBe('你好，世界')

    // 其它会话的 chunk 不混入当前流
    wsClient._emit('chunk', { type: 'chunk', session_id: 'other', content: '噪声', data: { tier: 'large' } })
    expect(chat.streamTail.content).toBe('你好，世界')

    // 最终 message 落定 → 尾巴清空，改为正式气泡
    wsClient._emit('message', {
      type: 'message', event: 'assistant_message', session_id: 's1',
      content: '你好，世界', data: { message_id: 'm1' },
    })
    await w.vm.$nextTick()
    expect(chat.streamTail).toBe(null)
    expect(chat.messages.at(-1)).toMatchObject({ role: 'assistant', content: '你好，世界' })
  })

  it('工具调用开始 → 流式尾巴收起（不冒充最终回答）', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('chunk', {
      type: 'chunk', session_id: 's1', content: '先看看文件', data: { tier: 'large', turn: 0 },
    })
    expect(chat.streamTail).not.toBe(null)
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_call', session_id: 's1',
      data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' } },
    })
    expect(chat.streamTail).toBe(null)
    expect(chat.traces).toHaveLength(1)
  })

  it('工具卡片插进对话流：卡片在用户气泡下方、回复在卡片之后（Phase 3 交错）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '查一下目录' })
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_call', session_id: 's1',
      data: {
        call_id: 'c1', name: 'exec_command', args: { command: 'ls' },
        view: { card: 'terminal', title: 'ls', command: 'ls', cwd: '', exit_code: null, output: '' },
      },
    })
    await w.vm.$nextTick()
    // 记下调用时的消息→卡片可定位到那一条
    expect(chat.traces[0].afterKey).toBeTruthy()
    const inline = w.find('.chat-traces-inline')
    expect(inline.exists()).toBe(true)
    expect(inline.find('.tool-card-terminal').exists()).toBe(true)
    // 已定位的不再重复进折叠面板
    expect(w.find('.chat-traces').exists()).toBe(false)

    wsClient._emit('message', {
      type: 'message', session_id: 's1', content: '目录已列出', data: { message_id: 'm1' },
    })
    await w.vm.$nextTick()
    expect(chat.messages.at(-1).content).toBe('目录已列出')
    // DOM 顺序：用户气泡 → 工具卡片 → 回复气泡
    const order = Array.from(w.element.querySelectorAll('.message, .chat-traces-inline'))
      .map(el => (el.classList.contains('chat-traces-inline') ? 'traces' : 'msg'))
    expect(order).toEqual(['msg', 'traces', 'msg'])
  })

  it('历史回放的工具事件不插进对话流（无可靠位置 → 折叠面板）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '旧问题' })
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({
      data: {
        events: [{
          _seq: 1, type: 'tool', event: 'tool_call', session_id: 's1',
          data: { call_id: 'h1', name: 'read_file', args: { path: 'a.py' } },
        }],
        last_seq: 1,
      },
    })
    await chat.replayToolEvents('s1')
    expect(chat.traces[0].afterKey).toBe('')
    await w.vm.$nextTick()
    expect(w.find('.chat-traces-inline').exists()).toBe(false)
    expect(w.find('.chat-traces').exists()).toBe(true)
  })

  it('刷新后工具卡片回到原位（user_message 事件提供锚点）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    // 模拟 switchToSession 从 DB 加载的历史消息（带服务端 id）
    chat.messages = [
      { _id: 'm1', id: 'u1', role: 'user', content: '旧问题' },
      { _id: 'm2', id: 'a1', role: 'assistant', content: '旧回答' },
    ]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, type: 'user_message', event: 'user_message', data: { message_id: 'u1' } },
        { _seq: 2, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' } } },
      ],
      last_seq: 2,
    } })
    await chat.replayToolEvents('s1')
    await w.vm.$nextTick()
    expect(chat.traces[0].afterKey).toBe('u1')
    // 回到对话流里（用户消息之后、回复之前），不再进折叠面板
    expect(w.find('.chat-traces').exists()).toBe(false)
    const order = Array.from(w.element.querySelectorAll('.message, .chat-traces-inline'))
      .map(el => (el.classList.contains('chat-traces-inline') ? 'traces' : 'msg'))
    expect(order).toEqual(['msg', 'traces', 'msg'])
  })

  it('刷新后老会话（无 user_message 事件）的工具卡片也回到原位', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.messages = [
      { _id: 'm1', id: 'u1', role: 'user', content: '旧问题' },
      { _id: 'm2', id: 'a1', role: 'assistant', content: '旧回答' },
    ]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, timestamp: 1100, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' } } },
      ],
      last_seq: 1,
    } })
    // 无 user_message 事件 → 由消息时间回退定位
    await chat.replayToolEvents('s1', 500, [{ key: 'u1', ts: 1000 }])
    await w.vm.$nextTick()
    expect(chat.traces[0].afterKey).toBe('u1')
    expect(w.find('.chat-traces').exists()).toBe(false)
    const order = Array.from(w.element.querySelectorAll('.message, .chat-traces-inline'))
      .map(el => (el.classList.contains('chat-traces-inline') ? 'traces' : 'msg'))
    expect(order).toEqual(['msg', 'traces', 'msg'])
  })

  it('非当前会话的 tool 事件不混入轨迹', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('tool', {
      type: 'tool', event: 'tool_call', session_id: 'other',
      data: { call_id: 'c2', name: 'exec_command', args: {} },
    })
    expect(chat.traces).toHaveLength(0)
  })

  it('WS thinking user_intent_request 事件 → chat.addIntent', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('thinking', {
      session_id: 's1',
      data: {
        payload: { request_id: 'i1', question: '用哪个框架？', options: ['Vue', 'React'] },
        stage_event: { event_type: 'security', action: 'user_intent_request' },
      },
    })
    const intents = chat.messages.filter(m => m.kind === 'intent')
    expect(intents).toHaveLength(1)
    expect(intents[0].options).toEqual(['Vue', 'React'])
  })

  it('非当前会话的 thinking 事件不混入', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('thinking', {
      session_id: 'other_session',
      data: { payload: { request_id: 'r9' }, stage_event: { event_type: 'security', action: '等待用户审批' } },
    })
    expect(chat.messages.filter(m => m.kind === 'approval')).toHaveLength(0)
  })

  it('WS message 事件渲染 assistant 消息', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('message', { session_id: 's1', event: 'assistant_message', content: '你好' })
    expect(chat.messages.some(m => m.role === 'assistant' && m.content === '你好')).toBe(true)
  })

  it('WS done 事件结束处理状态', async () => {
    await mountChat()
    const chat = useChatStore()
    chat.markProcessing('s1')
    chat.addMessage({ role: 'user', content: 'x' })
    wsClient._emit('done', { session_id: 's1' })
    expect(chat.processing).toBe(false)
  })

  it('用户主动 stop 后忽略补发消息', async () => {
    await mountChat()
    const chat = useChatStore()
    chat.stopped = true // Pinia 自动解包，直接赋值
    wsClient._emit('message', { session_id: 's1', content: '被忽略' })
    expect(chat.messages.some(m => m.content === '被忽略')).toBe(false)
  })

  it('send 按钮发送消息', async () => {
    const w = await mountChat()
    const connectSpy = vi.spyOn(wsClient, 'connect').mockResolvedValue()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    const input = w.find('textarea')
    await input.setValue('测试消息')
    const sendBtn = w.findAll('button').find(b => b.classes().includes('chat-send-btn'))
    await sendBtn.trigger('click')
    expect(sendSpy).toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  // ── 滚动交互：底部悬浮按钮 + 上滑自动加载更早消息 ──
  function _mockDims(w, { scrollTop, scrollHeight, clientHeight }) {
    const el = w.find('.chat-messages').element
    Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true })
    Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
    Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
    return el
  }

  it('滚动离开底部 → 显示回到底部按钮；回到底部 → 隐藏', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '问' })
    chat.addMessage({ role: 'assistant', content: '答' })
    await flushPromises()
    // 初始在底部附近（dist=0）→ 无按钮
    _mockDims(w, { scrollTop: 0, scrollHeight: 300, clientHeight: 300 })
    await w.find('.chat-messages').trigger('scroll')
    expect(w.find('.chat-scroll-bottom').exists()).toBe(false)
    // 离开底部（dist=700 > 120）→ 按钮出现
    _mockDims(w, { scrollTop: 0, scrollHeight: 1000, clientHeight: 300 })
    await w.find('.chat-messages').trigger('scroll')
    expect(w.find('.chat-scroll-bottom').exists()).toBe(true)
    // 回到底部 → 按钮消失
    _mockDims(w, { scrollTop: 700, scrollHeight: 1000, clientHeight: 300 })
    await w.find('.chat-messages').trigger('scroll')
    expect(w.find('.chat-scroll-bottom').exists()).toBe(false)
  })

  it('点击底部按钮强制滚到最下', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '问' })
    chat.addMessage({ role: 'assistant', content: '答' })
    const el = _mockDims(w, { scrollTop: 0, scrollHeight: 1000, clientHeight: 300 })
    await w.find('.chat-messages').trigger('scroll')
    expect(w.find('.chat-scroll-bottom').exists()).toBe(true)
    await w.find('.chat-scroll-bottom').trigger('click')
    // force 模式无视守卫：scrollTop 被赋值为 scrollHeight
    await flushPromises()
    expect(el.scrollTop).toBe(1000)
  })

  it('上滑到顶部附近自动加载更早消息（分批渲染）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    for (let i = 0; i < 120; i++) chat.addMessage({ role: 'user', content: 'm' + i })
    await flushPromises()
    // 默认只渲染最后 RENDER_LIMIT(50) 条
    const renderedBefore = w.findAll('.chat-messages > *').length
    // 模拟滚到顶部附近：scrollTop < 80 且还有未渲染消息 → 自动加载一批
    _mockDims(w, { scrollTop: 10, scrollHeight: 5000, clientHeight: 600 })
    await w.find('.chat-messages').trigger('scroll')
    await new Promise((r) => setTimeout(r, 20))
    const renderedAfter = w.findAll('.chat-messages > *').length
    expect(chat.messages.length).toBe(120)
    expect(renderedAfter).toBeGreaterThan(renderedBefore - 1 + 49) // 至少 +50 条进入渲染
  })

  // ── 渲染测试：删除联动 / 分批渲染可见性 / 加载入口 ──

  it('渲染：删除 AI 回复后过程流面板从 DOM 消失', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '问' })
    chat.addMessage({ kind: 'process', role: 'system', content: '思考步骤A\n调度语言B', runners: null })
    chat.addMessage({ role: 'assistant', content: '最终回答X' })
    await flushPromises()
    // 删除前：时间线面板与回复都在渲染输出中（面板默认折叠 → 展开后显示步骤）
    expect(w.find('.trajectory').exists()).toBe(true)
    await w.find('.trajectory-head').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('思考步骤A')
    expect(w.text()).toContain('最终回答X')
    // 删除 assistant → 确认框通过 → 联动移除上方 process 时间线，渲染同步更新
    const p = w.vm.handleDeleteMessage(2)
    await new Promise((r) => setTimeout(r, 10))
    const { resolveDialog } = await import('@/composables/useDialog.js')
    resolveDialog(true)
    await p
    await flushPromises()
    expect(w.find('.trajectory').exists()).toBe(false)
    expect(w.text()).not.toContain('思考步骤A')
    expect(w.text()).toContain('问')
  })

  it('渲染：超过渲染上限时最新一批可见、更早的不可见，加载入口显示剩余条数', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    for (let i = 0; i < 120; i++) chat.addMessage({ role: 'user', content: 'm' + i })
    await flushPromises()
    const text = () => w.find('.chat-messages').text()
    // 最新一条在渲染中；最早一批（前 70 条）不在 DOM
    expect(text()).toContain('m119')
    expect(text()).not.toContain('m0')
    expect(text()).not.toContain('m69')
    // 加载入口显示剩余数量
    const entry = w.find('.chat-load-more')
    expect(entry.exists()).toBe(true)
    expect(entry.text()).toContain('还有 70 条')
    // 上滑自动加载一批后：m20 进入渲染、m19 仍隐藏，剩余数减少
    _mockDims(w, { scrollTop: 10, scrollHeight: 5000, clientHeight: 600 })
    await w.find('.chat-messages').trigger('scroll')
    await new Promise((r) => setTimeout(r, 30))
    expect(text()).toContain('m20')
    expect(text()).not.toContain('m19')
    expect(w.find('.chat-load-more').text()).toContain('还有 20 条')
  })

  it('渲染：空会话无回到底部按钮，有消息且离底时才出现', async () => {
    const w = await mountChat()
    expect(useChatStore().messages.length).toBe(0)
    expect(w.find('.chat-scroll-bottom').exists()).toBe(false)
    const chat = useChatStore()
    chat.addMessage({ role: 'assistant', content: '答' })
    await flushPromises()
    // 在底部附近（jsdom 默认尺寸 0）→ 不出现
    expect(w.find('.chat-scroll-bottom').exists()).toBe(false)
    // 模拟离底 → 出现
    _mockDims(w, { scrollTop: 0, scrollHeight: 900, clientHeight: 300 })
    await w.find('.chat-messages').trigger('scroll')
    expect(w.find('.chat-scroll-bottom').exists()).toBe(true)
  })

  it('审批/提问通过页面处理函数回传 WS', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addApproval({ data: { payload: { request_id: 'ra', detail: 'x' }, stage_event: { target: 't' } } })
    chat.addIntent({ data: { payload: { request_id: 'ia', question: 'q', options: ['A'] } } })
    await flushPromises()
    w.vm.handleApprove('ra', true)
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'security_response', request_id: 'ra', approved: true }))
    w.vm.handleAnswerIntent('ia', 'A')
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'interactive_response', request_id: 'ia', answer: 'A' }))
    vi.restoreAllMocks()
  })

  it('WS mental 心理活动事件渲染 system 消息', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('mental', { session_id: 's1', msg_type: 'mental', content: '内心独白...' })
    expect(chat.messages.some(m => m.kind === 'mental' && m.content === '内心独白...')).toBe(true)
  })

  it('WS message 错误前缀渲染为错误横幅', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('message', { session_id: 's1', content: '[安全拦截] 权限不足' })
    const m = chat.messages.find(x => x.content === '[安全拦截] 权限不足')
    expect(m.error).toBe(true)
  })

  it('WS message 空内容触发 finalizeStream', async () => {
    await mountChat()
    const chat = useChatStore()
    chat.markProcessing('s1')
    wsClient._emit('message', { session_id: 's1', content: '' })
    expect(chat.processing).toBe(false)
  })

  it('WS message 无 conversation_history 时回退本地组装会话记忆（含 map 回调）', async () => {
    await mountChat()
    const chat = useChatStore()
    chat.messages = [
      { role: 'user', content: '第一个问题' },
      { role: 'assistant', content: '第一个回答' },
    ]
    wsClient._emit('message', { session_id: 's1', content: '新回复' })
    const m = chat.messages.find((x) => x.content === '新回复')
    expect(m.meta.sessionMemory).toContain('[user]: 第一个问题')
    expect(m.meta.sessionMemory).toContain('[assistant]: 第一个回答')
  })

  it('WS message 优先使用后端 conversation_history 原文', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('message', {
      session_id: 's1',
      content: '带历史的回复',
      data: {
        meta: { conversation_history: '后端注入原文', inner_monologue: '内心独白', event_memory: '事件记忆' },
      },
    })
    const m = chat.messages.find((x) => x.content === '带历史的回复')
    expect(m.meta.sessionMemory).toBe('后端注入原文')
    expect(m.meta.innerMonologue).toBe('内心独白')
    expect(m.meta.eventMemory).toBe('事件记忆')
  })

  it('WS status 解析在线模型 runners 与上下文 token', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('status', {
      session_id: 's1',
      data: {
        elapsed_s: 3.5,
        context_tokens: 1200,
        context_window_size: 8000,
        large_model: { model_id: 'm1', status: 'thinking' },
        active_supervisors: [{ model_id: 'm2', status: 'completed' }],
        active_experts: [{ model_id: 'm3', status: 'thinking', supervisor: 'm2' }],
      },
    })
    expect(chat.elapsed).toBe(3.5)
    expect(chat.runners.map(r => r.tier)).toEqual(['large', 'supervisor', 'expert'])
    expect(chat.runners[2].supervisor).toBe('m2')
  })

  it('WS status 上下文占用保持最近一次非零值（心跳带 0 不清空）', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.markProcessing('s1', true)
    // 真实 thinking_progress 始终携带 runner 结构；测试聚焦上下文字段本身
    const runner = { large_model: { model_id: 'm1', status: 'thinking' } }
    wsClient._emit('status', { session_id: 's1', data: { context_tokens: 5000, context_window_size: 10000, ...runner } })
    wsClient._emit('status', { session_id: 's1', data: { context_tokens: 0, context_window_size: 0, ...runner } })
    await flushPromises()
    const pct = w.find('.context-usage-pct')
    expect(pct.exists()).toBe(true)
    expect(pct.text()).toBe('50%')
  })

  it('处理中显示实时思考轨迹时间线，结束后固化为过程消息', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    // 无处理状态时即使有轨迹也不渲染（v-if 依赖 processing）
    chat.addThinkingStep({ content: '第一步思考' })
    await flushPromises()
    expect(w.find('.trajectory').exists()).toBe(false)
    // 标记处理中 → 实时时间线出现，内容为当前累积轨迹
    chat.markProcessing('s1', true)
    chat.addThinkingStep({ content: '第二步思考' })
    await flushPromises()
    expect(w.find('.trajectory').exists()).toBe(true)
    expect(w.text()).toContain('第一步思考')
    expect(w.text()).toContain('第二步思考')
    // 结束处理 → 实时面板消失，轨迹固化为会话消息（持久化，刷新后可恢复）
    chat.finalizeStream('')
    await flushPromises()
    expect(chat.processing).toBe(false)
    expect(chat.trajectory.length).toBe(0)
    expect(chat.messages.some(m => m.kind === 'process' && m.trajectory && m.trajectory.length)).toBe(true)
  })

  it('WS ack busy 设置提示并稍后重发', async () => {
    vi.useFakeTimers()
    await mountChat()
    const chat = useChatStore()
    const retrySpy = vi.spyOn(chat, 'retryLastInput').mockImplementation(() => {})
    wsClient._emit('ack', { session_id: 's1', event: 'busy', data: {} })
    expect(chat.hint).toBe('会话正在处理中，请稍候…')
    vi.advanceTimersByTime(2600)
    expect(retrySpy).toHaveBeenCalled()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('WS ack 带 message_id 回填最后一条 user 消息', async () => {
    await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: 'x' })
    wsClient._emit('ack', { session_id: 's1', event: 'received', data: { message_id: 'msg-42' } })
    const last = chat.messages[chat.messages.length - 1]
    expect(last.id).toBe('msg-42')
  })

  it('WS proactive 当前会话追加主动消息', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('proactive', { session_id: 's1', content: '关心你一下', data: { message_id: 'p1' } })
    expect(chat.messages.some(m => m.proactive && m.content === '关心你一下')).toBe(true)
  })

  it('WS proactive 其他会话只刷新列表不混入', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const loadSpy = vi.spyOn(useSessionStore(), 'loadSessions')
    wsClient._emit('proactive', { session_id: 'other', content: '别的会话' })
    expect(chat.messages.some(m => m.content === '别的会话')).toBe(false)
    expect(loadSpy).toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('WS error 显示错误 toast', async () => {
    await mountChat()
    const toast = (await import('@/stores/toast.js')).useToastStore()
    wsClient._emit('error', { session_id: 's1', content: '模型超时' })
    expect(toast.toasts.some(t => t.msg.includes('模型超时'))).toBe(true)
  })

  it('WS done 其他会话完成时仅清除其处理状态', async () => {
    await mountChat()
    const chat = useChatStore()
    chat.markProcessing('other_sid')
    wsClient._emit('done', { session_id: 'other_sid' })
    expect(chat.processing).toBe(false)
  })

  it('handleSend 附带图片附件', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    vi.spyOn(wsClient, 'connect').mockResolvedValue()
    vi.spyOn(wsClient, 'send').mockReturnValue(true)
    const sendSpy = vi.spyOn(chat, 'sendMessage').mockResolvedValue()
    const atts = [{ type: 'image/png', data: 'data:image/png;base64,xxx', name: 'a.png' }, { type: 'text/plain', data: 'hello', name: 'b.txt' }]
    w.vm.handleSend({ text: '看图', attachments: atts })
    expect(sendSpy).toHaveBeenCalledWith('看图', atts, undefined)
    const userMsg = chat.messages.find(m => m.role === 'user' && m.content === '看图')
    expect(userMsg.images).toEqual(['data:image/png;base64,xxx'])
    expect(chat.hint).toBe('思考中...')
    vi.restoreAllMocks()
  })

  it('handleSend 处理中时忽略重复发送', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const sendSpy = vi.spyOn(chat, 'sendMessage')
    chat.markProcessing('s1')
    w.vm.handleSend({ text: '再发一次' })
    expect(sendSpy).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('commitTitle 保存标题', async () => {
    const w = await mountChat()
    const session = useSessionStore()
    session.sessionId = 's1'
    session.currentTitle = '旧标题'
    const updSpy = vi.spyOn(endpoints, 'updateSessionTitle').mockResolvedValue({})
    w.vm.editingTitle = true
    w.vm.titleDraft = '新标题'
    await w.vm.commitTitle()
    expect(updSpy).toHaveBeenCalledWith('s1', '新标题')
    expect(session.currentTitle).toBe('新标题')
    vi.restoreAllMocks()
  })

  it('handleSessionRename 重命名会话', async () => {
    const w = await mountChat()
    const session = useSessionStore()
    session.sessionId = 's1'
    session.sessions = [{ session_id: 's1', title: '原名' }]
    const updSpy = vi.spyOn(endpoints, 'updateSessionTitle').mockResolvedValue({})
    // usePrompt 走 dialogState._resolve——先发起再 resolve
    const p = w.vm.handleSessionRename('s1')
    await new Promise(r => setTimeout(r, 10))
    const { dialogState, resolveDialog } = await import('@/composables/useDialog.js')
    expect(dialogState().type).toBe('prompt')
    resolveDialog('新名字')
    await p
    expect(updSpy).toHaveBeenCalledWith('s1', '新名字')
    vi.restoreAllMocks()
  })

  it('handleDeleteMessage 确认后删除', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '删我' })
    const delSpy = vi.spyOn(chat, 'deleteMessageAt').mockResolvedValue(true)
    w.vm.handleDeleteMessage(0)
    await new Promise(r => setTimeout(r, 10))
    // 打开确认框 → 通过
    const { dialogState } = await import('@/composables/useDialog.js')
    dialogState().visible = true
    const { resolveDialog } = await import('@/composables/useDialog.js')
    resolveDialog(true)
    await flushPromises()
    expect(delSpy).toHaveBeenCalledWith(0)
    vi.restoreAllMocks()
  })

  it('handleEditMessage 无 id 提示不可编辑', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: 'x' })
    const editSpy = vi.spyOn(chat, 'editMessageAt')
    await w.vm.handleEditMessage(0)
    expect(editSpy).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('handleClearChat 确认后清空并删除后端消息', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    chat.addMessage({ role: 'user', content: 'x' })
    const clearSpy = vi.spyOn(chat, 'clearMessages')
    const { dialogState, resolveDialog } = await import('@/composables/useDialog.js')
    w.vm.handleClearChat()
    await new Promise(r => setTimeout(r, 10))
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await flushPromises()
    expect(clearSpy).toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('handleCopyMessage 复制到剪贴板', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'assistant', content: '复制我' })
    const writeSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    w.vm.handleCopyMessage(0)
    await flushPromises()
    expect(writeSpy).toHaveBeenCalledWith('复制我')
    vi.restoreAllMocks()
  })

  it('handleCopyMessage clipboard 失败回退 copyTextFallback', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'assistant', content: '复制我' })
    const { useToastStore } = await import('@/stores/toast.js')
    const toast = useToastStore()
    // clipboard 拒绝 → 走 .catch → copyTextFallback → execCommand 成功 → toast 已复制
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    Object.defineProperty(document, 'execCommand', { value: () => true, configurable: true, writable: true })
    try {
      w.vm.handleCopyMessage(0)
      await flushPromises()
      expect(toast.toasts.some((t) => t.msg.includes('已复制'))).toBe(true)
    } finally {
      delete document.execCommand
    }
    // 无内容直接返回不报错
    w.vm.handleCopyMessage(99)
  })

  it('handleNewSession 初始化新会话', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const initSpy = vi.spyOn(chat, 'init').mockResolvedValue()
    await w.vm.handleNewSession()
    expect(initSpy).toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('loadMoreMessages 分批加载更多', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    for (let i = 0; i < 60; i++) chat.addMessage({ role: 'user', content: 'm' + i })
    await flushPromises()
    expect(w.vm.visibleMessages.length).toBe(50)
    expect(w.vm.hasMoreMessages).toBe(true)
    w.vm.loadMoreMessages()
    await flushPromises()
    expect(w.vm.visibleMessages.length).toBe(60)
    expect(w.vm.hasMoreMessages).toBe(false)
  })

  it('toggleTodo 切换任务完成状态', async () => {
    const w = await mountChat()
    globalThis.fetch = vi.fn(async (url, init) => ({
      ok: true, status: 200,
      json: async () => ({ success: true, data: { todos: [] } }),
      text: async () => '',
    }))
    const t = { id: 't1', status: 'pending' }
    await w.vm.toggleTodo(t)
    expect(t.status).toBe('completed')
    vi.restoreAllMocks()
  })

  it('startEditTitle 无会话时不进入编辑', () => {
    const w = mount(Chat, { global: { plugins: [createTestPinia(), router] } })
    const session = useSessionStore()
    session.sessionId = null
    w.vm.startEditTitle()
    expect(w.vm.editingTitle).toBe(false)
  })

  it('handleSessionSelect 切换会话', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const switchSpy = vi.spyOn(chat, 'switchToSession').mockResolvedValue()
    await w.vm.handleSessionSelect('s2')
    expect(switchSpy).toHaveBeenCalledWith('s2')
    vi.restoreAllMocks()
  })

  it('handleSessionDelete 删除当前会话后切换到最近会话', async () => {
    const w = await mountChat()
    const session = useSessionStore()
    const chat = useChatStore()
    session.sessionId = 's1'
    session.sessions = [{ session_id: 's1', title: 'A' }, { session_id: 's2', title: 'B' }]
    // 真实 deleteSession 会从列表移除——mock 时保持该行为
    const delSpy = vi.spyOn(session, 'deleteSession').mockImplementation(async () => {
      session.sessions = session.sessions.filter(s => s.session_id !== 's1')
    })
    const switchSpy = vi.spyOn(chat, 'switchToSession').mockResolvedValue()
    // 打开确认框并放行
    const p = w.vm.handleSessionDelete('s1')
    await new Promise((r) => setTimeout(r, 10))
    const { dialogState, resolveDialog } = await import('@/composables/useDialog.js')
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await p
    await flushPromises()
    expect(delSpy).toHaveBeenCalledWith('s1')
    expect(switchSpy).toHaveBeenCalledWith('s2')
    vi.restoreAllMocks()
  })

  it('handleSessionDelete 无 sid 时仅刷新列表', async () => {
    const w = await mountChat()
    const session = useSessionStore()
    const loadSpy = vi.spyOn(session, 'loadSessions').mockResolvedValue()
    await w.vm.handleSessionDelete(null)
    expect(loadSpy).toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('copyTextFallback 用 textarea 兜底复制', async () => {
    const w = await mountChat()
    const { useToastStore } = await import('@/stores/toast.js')
    const toast = useToastStore()
    // jsdom 无 execCommand：execCommand 调用抛 TypeError → 被 catch → 显示复制失败
    w.vm.copyTextFallback('兜底复制内容')
    await new Promise((r) => setTimeout(r, 10))
    expect(toast.toasts.some((t) => t.msg.includes('复制失败'))).toBe(true)
  })

  it('copyTextFallback 成功路径（execCommand 可用）', async () => {
    const w = await mountChat()
    const { useToastStore } = await import('@/stores/toast.js')
    const toast = useToastStore()
    // 注入 execCommand 成功实现，try/finally 保证即使断言失败也清理干净
    Object.defineProperty(document, 'execCommand', { value: () => true, configurable: true, writable: true })
    try {
      w.vm.copyTextFallback('兜底复制内容')
      await new Promise((r) => setTimeout(r, 10))
      expect(toast.toasts.some((t) => t.msg.includes('已复制'))).toBe(true)
    } finally {
      delete document.execCommand
    }
  })

  it('loadTodos 加载任务列表（含失败不崩溃）', async () => {
    const w = await mountChat()
    // 用 stubGlobal 而非直接赋值：afterEach 的 unstubAllGlobals 会兜底清理
    vi.stubGlobal('fetch', vi.fn(async (url) => ({
      ok: true, status: 200,
      json: async () => ({ data: { todos: [{ id: 't1', status: 'completed', text: '已完成' }] } }),
      text: async () => '',
    })))
    await w.vm.loadTodos()
    expect(w.vm.todos).toHaveLength(1)
    expect(w.vm.todoDone).toBe(1)
    // 失败不崩溃
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('net') }))
    await w.vm.loadTodos()
    expect(w.vm.todos).toHaveLength(1) // 保持旧值
    vi.restoreAllMocks()
  })

  it('activeSkill 从 large runner 提取', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.runners = [{ tier: 'large', active_skill: 'code_review' }, { tier: 'expert', active_skill: '' }]
    await w.vm.$nextTick()
    expect(w.vm.activeSkill).toBe('code_review')
    chat.runners = []
    await w.vm.$nextTick()
    expect(w.vm.activeSkill).toBe('')
  })

  it('showThinkingWaiting 仅在 processing 且末条为 user 时显示', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.markProcessing('s1', false)
    expect(w.vm.showThinkingWaiting).toBe(false)
    chat.markProcessing('s1')
    expect(w.vm.showThinkingWaiting).toBe(true) // 空消息
    chat.addMessage({ role: 'assistant', content: 'a' })
    expect(w.vm.showThinkingWaiting).toBe(false)
    chat.addMessage({ role: 'user', content: 'u' })
    expect(w.vm.showThinkingWaiting).toBe(true)
  })

  it('连接看门狗：处理中断开显示错误并 finalize', async () => {
    vi.useFakeTimers()
    await mountChat()
    const chat = useChatStore()
    chat.markProcessing('s1')
    // 模拟 WS 未连接
    vi.spyOn(wsClient, 'isConnected').mockReturnValue(false)
    vi.advanceTimersByTime(2200)
    expect(chat.processing).toBe(false)
    const { useToastStore } = await import('@/stores/toast.js')
    expect(useToastStore().toasts.some((t) => t.msg.includes('连接已断开'))).toBe(true)
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('WS mental 空内容不添加', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('mental', { session_id: 's1', msg_type: 'mental', content: '' })
    expect(chat.messages.some((m) => m.kind === 'mental')).toBe(false)
  })

  it('WS thinking 未知 action 的 security 事件不拦截（走思考区）', async () => {
    await mountChat()
    const chat = useChatStore()
    wsClient._emit('thinking', {
      session_id: 's1',
      role: 'thinking',
      content: '推理步骤',
      data: { payload: { request_id: 'rX' }, stage_event: { event_type: 'security', action: '其他动作' } },
    })
    // 非审批/提问 action → 走思考区累积
    expect(chat.messages.filter((m) => m.kind === 'approval')).toHaveLength(0)
    expect(chat.messages.filter((m) => m.kind === 'intent')).toHaveLength(0)
  })

  it('commitTitle 保存失败提示', async () => {
    const w = await mountChat()
    const session = useSessionStore()
    session.sessionId = 's1'
    session.currentTitle = '旧'
    w.vm.editingTitle = true
    w.vm.titleDraft = '新标题'
    vi.spyOn(endpoints, 'updateSessionTitle').mockRejectedValue(new Error('x'))
    await w.vm.commitTitle()
    const { useToastStore } = await import('@/stores/toast.js')
    expect(useToastStore().toasts.some((t) => t.msg.includes('标题保存失败'))).toBe(true)
    vi.restoreAllMocks()
  })

  it('handleSessionDelete 删除非当前会话不切换', async () => {
    const w = await mountChat()
    const session = useSessionStore()
    const chat = useChatStore()
    session.sessionId = 's1'
    session.sessions = [{ session_id: 's2', title: 'B' }]
    const switchSpy = vi.spyOn(chat, 'switchToSession').mockResolvedValue()
    const p = w.vm.handleSessionDelete('s2')
    await new Promise((r) => setTimeout(r, 10))
    const { resolveDialog } = await import('@/composables/useDialog.js')
    resolveDialog(true)
    await p
    expect(switchSpy).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('handleDeleteMessage 无消息直接返回', async () => {
    const w = await mountChat()
    await w.vm.handleDeleteMessage(99)
    // msg 不存在 → 直接 return，不弹确认框
    const { dialogState } = await import('@/composables/useDialog.js')
    expect(dialogState().visible).toBe(false)
  })

  it('handleEditMessage 确认后编辑成功', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '旧', id: 'm9' })
    const editSpy = vi.spyOn(chat, 'editMessageAt').mockResolvedValue(true)
    const p = w.vm.handleEditMessage(0)
    await new Promise((r) => setTimeout(r, 10))
    const { dialogState, resolveDialog } = await import('@/composables/useDialog.js')
    expect(dialogState().type).toBe('prompt')
    resolveDialog('新内容')
    await p
    expect(editSpy).toHaveBeenCalledWith(0, '新内容')
    vi.restoreAllMocks()
  })
// ── todo 推送（替代轮询）：WS 事件触发按需拉取 ────────────────────────────

it('收到 todo 推送事件 → 按需重新拉取 todo 列表', async () => {
  const w = await mountChat()
  const sessions = useSessionStore()
  sessions.sessionId = 's1'
  vi.stubGlobal('fetch', vi.fn(async (url) => {
    if (String(url).includes('/api/management/todos')) {
      return { ok: true, status: 200, json: async () => ({ data: { todos: [{ id: 't1', status: 'pending', text: '新任务' }] } }), text: async () => '' }
    }
    return { ok: true, status: 200, json: async () => ({}), text: async () => '' }
  }))
  w.vm.todos = []
  await w.vm._onTodo({ session_id: 's1', content: 'todo 已变更' })
  await flushPromises()
  expect(w.vm.todos).toHaveLength(1)
  expect(w.vm.todos[0].text).toBe('新任务')
})

it('todo 推送事件属于其他会话 → 不刷新当前会话 todo', async () => {
  const w = await mountChat()
  const sessions = useSessionStore()
  sessions.sessionId = 's1'
  w.vm.todos = [{ id: 'keep', status: 'pending', text: '保留' }]
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ data: { todos: [] } }), text: async () => '' })))
  await w.vm._onTodo({ session_id: 'other_session', content: 'x' })
  await flushPromises()
  expect(w.vm.todos).toHaveLength(1)
  expect(w.vm.todos[0].text).toBe('保留')
})

// ── 会话投影（Phase 4）：整值快照直接更新 UI，不再额外请求 ──

it('收到 session_projection 帧 → todo 列表与上下文占用按快照更新（不额外拉取）', async () => {
  const w = await mountChat()
  const fetchSpy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ data: { todos: [] } }), text: async () => '' }))
  vi.stubGlobal('fetch', fetchSpy)
  w.vm._onProjection({
    type: 'session_projection', session_id: 's1', _seq: 12,
    data: { key: 'todos', seq: 12, value: { todos: [{ id: 't1', content: '投影任务', status: 'pending' }], done: 0, total: 1 } },
  })
  w.vm._onProjection({
    type: 'session_projection', session_id: 's1', _seq: 13,
    data: { key: 'contextPressure', seq: 13, value: { tokens: 640, window: 6400, ratio: 0.1 } },
  })
  await flushPromises()
  expect(w.vm.todos).toHaveLength(1)
  expect(w.vm.todos[0].content).toBe('投影任务')
  expect(w.vm.todoDone).toBe(0)
  expect(w.vm.contextTokens).toBe(640)
  expect(w.vm.contextWindowSize).toBe(6400)
  // 快照自带列表 → 不需要再请求 /management/todos
  expect(fetchSpy.mock.calls.some(([u]) => String(u).includes('/api/management/todos'))).toBe(false)
})

// ── 可应答请求（Phase 4.b）：快照重建横幅 + 输入区接管 ──

it('收到 session_request 帧 → 重建审批横幅并接管输入区', async () => {
  const w = await mountChat()
  const chat = useChatStore()
  expect(chat.hasPendingRequest).toBe(false)
  w.vm._onRequest({
    type: 'session_request', session_id: 's1',
    data: { requests: [{ request_id: 'r1', kind: 'approval', target: 'exec_command', detail: '执行 rm' }] },
  })
  await flushPromises()
  expect(chat.hasPendingRequest).toBe(true)
  expect(w.findAll('.approval-banner')).toHaveLength(1)
  // 输入区被接管：占位符提示先处理横幅，发送按钮禁用
  expect(w.find('.chat-input-field').attributes('placeholder')).toContain('请先在上方处理')
  expect(w.find('.chat-send-btn').attributes('disabled')).toBeDefined()
})

it('session_request：其他会话忽略；空快照→横幅失效且输入区恢复', async () => {
  const w = await mountChat()
  const chat = useChatStore()
  w.vm._onRequest({ session_id: 'other', data: { requests: [{ request_id: 'x', kind: 'approval' }] } })
  expect(chat.hasPendingRequest).toBe(false)
  w.vm._onRequest({
    session_id: 's1',
    data: { requests: [{ request_id: 'r1', kind: 'approval', target: 'exec_command' }] },
  })
  await flushPromises()
  expect(chat.hasPendingRequest).toBe(true)
  w.vm._onRequest({ session_id: 's1', data: { requests: [] } })
  await flushPromises()
  expect(chat.hasPendingRequest).toBe(false)
  // 横幅保留（不凭空消失）但显示失效文案，按钮不可点
  expect(w.findAll('.approval-banner')).toHaveLength(1)
  expect(w.text()).toContain('该请求已失效')
  expect(w.find('.chat-send-btn').attributes('disabled')).toBeUndefined()
})

it('session_projection：其他会话 / 旧 seq 的帧被忽略', async () => {
  const w = await mountChat()
  w.vm._onProjection({ session_id: 'other', data: { key: 'todos', seq: 5, value: { todos: [{ id: 'x' }], total: 1 } } })
  expect(w.vm.todos).toHaveLength(0)
  w.vm._onProjection({ session_id: 's1', data: { key: 'todos', seq: 9, value: { todos: [{ id: 'a' }], total: 1 } } })
  w.vm._onProjection({ session_id: 's1', data: { key: 'todos', seq: 8, value: { todos: [{ id: 'b' }], total: 1 } } })
  expect(w.vm.todos[0].id).toBe('a')
})


  it('A 会话处理中切到 B，A 的回复不混入 B', async () => {
    await mountChat()
    const chat = useChatStore()
    const session = useSessionStore()
    // A 会话处理中
    chat.markProcessing('s1')
    // 切到 B
    session.sessionId = 's2'
    // A 的回复到达
    wsClient._emit('message', { session_id: 's1', event: 'assistant_message', content: 'A 的回复' })
    expect(chat.messages.some(m => m.content === 'A 的回复')).toBe(false)
  })

  it('A 会话处理中切到 B，A 的 done 到达时 B 无处理 → processing 归零', async () => {
    await mountChat()
    const chat = useChatStore()
    const session = useSessionStore()
    chat.markProcessing('s1')
    session.sessionId = 's2'
    wsClient._emit('done', { session_id: 's1' })
    expect(chat.processing).toBe(false)
  })

  it('A 会话处理中切到 B 且 B 也在处理，A 的 done 不清除 B 的处理状态', async () => {
    await mountChat()
    const chat = useChatStore()
    const session = useSessionStore()
    chat.markProcessing('s1')
    session.sessionId = 's2'
    // B 也开始处理
    chat.markProcessing('s2')
    wsClient._emit('done', { session_id: 's1' })
    expect(chat.processing).toBe(true)
  })

  it('真实流程：A 处理中 → switchToSession(B) → A 的回复/思考/done 均不混入 B', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const session = useSessionStore()
    // A 处理中
    session.sessionId = 's1'
    session.sessions = [{ session_id: 's1', title: 'A' }, { session_id: 's2', title: 'B' }]
    chat.markProcessing('s1')
    // 真实切到 B
    vi.spyOn(wsClient, 'connect').mockResolvedValue()
    vi.spyOn(wsClient, 'isConnected').mockReturnValue(true)
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [] })
    vi.spyOn(endpoints, 'sessionDialog').mockResolvedValue({ data: { dialog: [] } })
    await chat.switchToSession('s2')
    expect(session.sessionId).toBe('s2')
    // A 的各类事件到达
    wsClient._emit('thinking', { session_id: 's1', msg_type: 'thinking', event: 'thinking', content: 'A 在思考', role: 'thinking' })
    wsClient._emit('message', { session_id: 's1', event: 'assistant_message', content: 'A 的回复' })
    wsClient._emit('status', { session_id: 's1', event: 'status', data: {} })
    wsClient._emit('done', { session_id: 's1' })
    expect(chat.messages.some(m => m.content === 'A 的回复')).toBe(false)
  })
})
