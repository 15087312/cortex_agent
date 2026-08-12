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
    chat.processing = true
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

  it('审批/提问通过页面处理函数回传 WS', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addApproval({ data: { payload: { request_id: 'ra', detail: 'x' }, stage_event: { target: 't' } } })
    chat.addIntent({ data: { payload: { request_id: 'ia', question: 'q', options: ['A'] } } })
    await flushPromises()
    w.vm.handleApprove('ra', true)
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'security_response', request_id: 'ra', approved: true }))
    w.vm.handleAnswerIntent('ia', 'A')
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'interactive_response', request_id: 'ia', answer: 'A' }))
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
    chat.processing = true
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
    chat.processing = true
    chat.processingSid = 'other_sid'
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
    expect(sendSpy).toHaveBeenCalledWith('看图', atts)
    const userMsg = chat.messages.find(m => m.role === 'user' && m.content === '看图')
    expect(userMsg.images).toEqual(['data:image/png;base64,xxx'])
    expect(chat.hint).toBe('思考中...')
    vi.restoreAllMocks()
  })

  it('handleSend 处理中时忽略重复发送', async () => {
    const w = await mountChat()
    const chat = useChatStore()
    const sendSpy = vi.spyOn(chat, 'sendMessage')
    chat.processing = true
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
    chat.processing = false
    expect(w.vm.showThinkingWaiting).toBe(false)
    chat.processing = true
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
    chat.processing = true
    // 模拟 WS 未连接
    vi.spyOn(wsClient, 'connected', 'get').mockReturnValue(false)
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
    // 不弹确认框、不崩溃
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
})
