import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from './chat.js'
import { useSessionStore } from './session.js'
import { useWsStore } from '@/ws/store.js'
import { wsClient } from '@/ws/client.js'
import { endpoints } from '@/api.js'

// 让 send 重试循环不真实等待（_sendWithRetry 最多 8×1s）
function stubConnected(value) {
  vi.spyOn(wsClient, 'connected', 'get').mockReturnValue(value)
}
function stubSend(value) {
  return vi.spyOn(wsClient, 'send').mockReturnValue(value)
}
// 让 _sendWithRetry 的 1s sleep 立即返回（不真实等待 8s）
function fastSleep() {
  vi.spyOn(globalThis, 'setTimeout').mockImplementation((fn) => { fn(); return 0 })
}

describe('useChatStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('addApproval 追加审批消息并去重', () => {
    const chat = useChatStore()
    const d = { data: { payload: { request_id: 'r1', detail: '执行 rm' }, stage_event: { target: 'exec_command' } } }
    chat.addApproval(d)
    expect(chat.messages).toHaveLength(1)
    expect(chat.messages[0].kind).toBe('approval')
    expect(chat.messages[0].requestId).toBe('r1')
    expect(chat.messages[0].target).toBe('exec_command')
    // 同 requestId 去重
    chat.addApproval(d)
    expect(chat.messages).toHaveLength(1)
  })

  it('approve 发送 security_response 并更新状态', async () => {
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addApproval({ data: { payload: { request_id: 'r1' }, stage_event: {} } })
    chat.approve('r1', true)
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'security_response', request_id: 'r1', approved: true }))
    const m = chat.messages.find(x => x.kind === 'approval' && x.requestId === 'r1')
    expect(m.resolved).toBe(true)
    expect(m.approved).toBe(true)
    vi.restoreAllMocks()
  })

  it('addIntent 追加提问消息（options + 去重）', () => {
    const chat = useChatStore()
    const d = { data: { payload: { request_id: 'i1', question: '选哪个？', options: ['A', 'B'] } } }
    chat.addIntent(d)
    expect(chat.messages[0].kind).toBe('intent')
    expect(chat.messages[0].question).toBe('选哪个？')
    expect(chat.messages[0].options).toEqual(['A', 'B'])
    chat.addIntent(d)
    expect(chat.messages).toHaveLength(1)
  })

  it('answerIntent 发送 interactive_response 并标记已回答', async () => {
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addIntent({ data: { payload: { request_id: 'i1', question: 'q' } } })
    chat.answerIntent('i1', '答案B')
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'interactive_response', request_id: 'i1', answer: '答案B' }))
    const m = chat.messages.find(x => x.kind === 'intent' && x.requestId === 'i1')
    expect(m.answered).toBe(true)
    expect(m.answer).toBe('答案B')
    vi.restoreAllMocks()
  })

  it('answerIntent 空答案不发送', async () => {
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.answerIntent('i1', '')
    chat.answerIntent('i1', null)
    expect(sendSpy).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('addMessage 追加消息', () => {
    const chat = useChatStore()
    chat.addMessage({ role: 'assistant', content: 'hi' })
    expect(chat.messages[0].role).toBe('assistant')
    expect(chat.messages[0].content).toBe('hi')
  })

  it('addThinkingStep 累积思考并 consume', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: '步骤一', data: { identity_name: '总指挥' } })
    chat.addThinkingStep({ content: '步骤二' })
    const t = chat.consumeThinking()
    expect(t).toContain('【总指挥】步骤一')
    expect(t).toContain('步骤二')
    expect(chat.consumeThinking()).toBe('')
  })

  it('stop 置 stopped 并 finalize', () => {
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.processing = true
    chat.stop()
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'stop' }))
    expect(chat.processing).toBe(false)
    vi.restoreAllMocks()
  })

  it('init 清空全部状态', () => {
    const chat = useChatStore()
    const session = useSessionStore()
    chat.addMessage({ role: 'user', content: 'x' })
    chat.processing = true
    session.sessionId = 'old'
    chat.init()
    expect(chat.messages).toHaveLength(0)
    expect(chat.processing).toBe(false)
    expect(session.sessionId).toBeNull()
  })

  it('approve 拒绝时发送 approved:false', async () => {
    const chat = useChatStore()
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addApproval({ data: { payload: { request_id: 'r2' }, stage_event: {} } })
    chat.approve('r2', false)
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'security_response', request_id: 'r2', approved: false }))
    const m = chat.messages.find(x => x.kind === 'approval' && x.requestId === 'r2')
    expect(m.resolved).toBe(true)
    expect(m.approved).toBe(false)
    vi.restoreAllMocks()
  })

  it('addApproval 缺 request_id 不添加', () => {
    const chat = useChatStore()
    chat.addApproval({ data: { payload: {}, stage_event: {} } })
    chat.addApproval({ data: {} })
    expect(chat.messages).toHaveLength(0)
  })

  it('addIntent 缺 request_id 不添加；options 非数组时为空', () => {
    const chat = useChatStore()
    chat.addIntent({ data: {} })
    chat.addIntent({ data: { payload: { request_id: 'i2', options: 'not-array' } } })
    expect(chat.messages).toHaveLength(1)
    expect(chat.messages[0].options).toEqual([])
  })

  it('sendMessage 创建会话并发送 input', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = null
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    stubConnected(true)
    const sendSpy = stubSend(true)
    // createSession 真实会设置 sessionId；mock 时同步更新
    const createSpy = vi.spyOn(session, 'createSession').mockImplementation(async () => { session.sessionId = 's1'; return 's1' })
    await chat.sendMessage('你好', [])
    expect(createSpy).toHaveBeenCalled()
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'input', content: '你好' }))
    expect(chat.processingSid).toBe('s1')
    vi.restoreAllMocks()
  })

  it('sendMessage 发送失败时复位处理状态', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    stubConnected(true)
    fastSleep()
    // 一直发送失败（_sendWithRetry 8 次重试内都失败）
    const sendSpy = stubSend(false)
    chat.processing = true
    await chat.sendMessage('x', [])
    expect(sendSpy).toHaveBeenCalled()
    expect(chat.processing).toBe(false)
    expect(chat.hint).toBe('')
    vi.restoreAllMocks()
  })

  it('retryLastInput 仅处理中且已连接时重发', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    stubConnected(true)
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const sendSpy = stubSend(true)
    // 未处理中 → 不发
    chat.retryLastInput()
    expect(sendSpy).not.toHaveBeenCalled()
    // 处理中 → 重发 input
    await chat.sendMessage('重试', [])
    chat.retryLastInput()
    expect(sendSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'input', content: '重试' }))
    vi.restoreAllMocks()
  })

  it('retryLastInput 已停止时不重发', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    stubConnected(true)
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const sendSpy = stubSend(true)
    await chat.sendMessage('x', [])
    chat.stop()
    sendSpy.mockClear()
    chat.retryLastInput()
    expect(sendSpy).not.toHaveBeenCalled()
    vi.restoreAllMocks()
  })

  it('clearMessages 清空消息与处理状态', () => {
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: 'x' })
    chat.processing = true
    chat.hint = '思考中...'
    chat.clearMessages()
    expect(chat.messages).toHaveLength(0)
    expect(chat.processing).toBe(false)
    expect(chat.hint).toBe('')
    expect(chat.runners).toHaveLength(0)
  })

  it('finalizeStream 收尾：更新流式消息、清状态与残留思考', () => {
    const chat = useChatStore()
    chat.addMessage({ role: 'assistant', content: '初稿' })
    chat.streamingIdx = 0
    chat.processing = true
    chat.addThinkingStep({ content: '残留思考' })
    chat.finalizeStream('最终回复')
    expect(chat.messages[0].content).toBe('最终回复')
    expect(chat.streamingIdx).toBe(-1)
    expect(chat.processing).toBe(false)
    expect(chat.consumeThinking()).toBe('')
  })

  it('deleteMessageAt 删除本地消息（无 id 直接删）', async () => {
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: 'a' })
    chat.addMessage({ role: 'user', content: 'b' })
    const ok = await chat.deleteMessageAt(0)
    expect(ok).toBe(true)
    expect(chat.messages).toHaveLength(1)
    expect(chat.messages[0].content).toBe('b')
  })

  it('deleteMessageAt 有 id 时同步后端；后端失败不删除', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    chat.addMessage({ role: 'user', content: 'a', id: 'msg1' })
    const delSpy = vi.spyOn(endpoints, 'deleteMessage').mockRejectedValue(new Error('x'))
    const ok = await chat.deleteMessageAt(0)
    expect(ok).toBe(false)
    expect(chat.messages).toHaveLength(1)
    expect(delSpy).toHaveBeenCalledWith('s1', 'msg1')
    vi.restoreAllMocks()
  })

  it('deleteMessageAt 越界返回 false', async () => {
    const chat = useChatStore()
    const ok = await chat.deleteMessageAt(5)
    expect(ok).toBe(false)
  })

  it('editMessageAt 本地更新内容并同步后端', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    chat.addMessage({ role: 'user', content: '旧内容', id: 'msg9' })
    const updSpy = vi.spyOn(endpoints, 'updateMessage').mockResolvedValue({})
    const ok = await chat.editMessageAt(0, '新内容')
    expect(ok).toBe(true)
    expect(chat.messages[0].content).toBe('新内容')
    expect(updSpy).toHaveBeenCalledWith('s1', 'msg9', '新内容')
    vi.restoreAllMocks()
  })

  it('editMessageAt 后端失败返回 false', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    chat.addMessage({ role: 'user', content: 'a', id: 'm1' })
    vi.spyOn(endpoints, 'updateMessage').mockRejectedValue(new Error('x'))
    const ok = await chat.editMessageAt(0, 'b')
    expect(ok).toBe(false)
    expect(chat.messages[0].content).toBe('a')
    vi.restoreAllMocks()
  })

  it('switchToSession 加载消息并连接 WS', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    session.sessions = [{ session_id: 's2', title: '会话二' }]
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [
      { role: 'user', content: '问', id: 'u1' },
      { role: 'thought', content: '【思考】思考过程', tier: 'supervisor', id: 't1' },
      { role: 'assistant', content: '答', id: 'a1', tier: 'large', metadata: { identity_name: '总指挥' } },
    ] })
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    await chat.switchToSession('s2')
    expect(session.sessionId).toBe('s2')
    expect(chat.messages).toHaveLength(3)
    expect(chat.messages[0].role).toBe('user')
    expect(chat.messages[1].identity_name).toBe('总指挥')
    // supervisor 思考聚合为独立专家气泡（与实时 addExpertMessage 一致）
    expect(chat.messages[2].kind).toBe('expert')
    expect(chat.messages[2].role).toBe('supervisor')
    expect(chat.messages[2]._thinking).toContain('思考过程')
    expect(session.currentTitle).toBe('会话二')
    vi.restoreAllMocks()
  })

  it('switchToSession 消息为空时回退 management dialog', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    session.sessions = []
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [] })
    vi.spyOn(endpoints, 'sessionDialog').mockResolvedValue({ data: { dialog: [{ role: 'user', content: '旧问' }, { role: 'assistant', text: '旧答' }] } })
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    await chat.switchToSession('s3')
    expect(chat.messages).toHaveLength(2)
    expect(chat.messages[0].content).toBe('旧问')
    expect(chat.messages[1].content).toBe('旧答')
    vi.restoreAllMocks()
  })

  it('addThinkingStep 去重重复片段与空内容', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: '' })
    chat.addThinkingStep({ content: '  ' })
    chat.addThinkingStep({ content: '步骤X', data: { tier: 'large' } })
    chat.addThinkingStep({ content: '步骤X', data: { tier: 'large' } })
    const t = chat.consumeThinking()
    expect(t).toBe('【large】步骤X')
  })

  it('supervisor/expert 推理挂到其气泡（不混入总思考区）', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: '正在分析需求', data: { tier: 'supervisor', identity_name: '代码主管' } })
    expect(chat.consumeThinking()).toBe('')
    // 输出气泡创建后挂载缓冲的推理
    chat.addExpertMessage({ content: '我负责整体方案', data: { tier: 'supervisor', identity_name: '代码主管' } })
    const b = chat.messages.find(m => m.kind === 'expert')
    expect(b._thinking).toContain('【代码主管】正在分析需求')
    // 工具调用挂气泡工具列表 + 轨迹
    chat.addThinkingStep({ content: 'todo: done (50 chars)', data: { tier: 'supervisor' } })
    expect(b._tools).toContain('todo: done (50 chars)')
    expect(chat.traces.some(t => t.text.includes('todo: done'))).toBe(true)
    // 后续推理追加到已建气泡
    chat.addThinkingStep({ content: '继续细化方案', data: { tier: 'supervisor', identity_name: '代码主管' } })
    expect(b._thinking).toContain('继续细化方案')
  })

  it('switchToSession 清空主管/专家气泡关联（无残留引用/缓冲）', async () => {
    const chat = useChatStore()
    const sess = useSessionStore()
    // 无输出事件时的缓冲（异常中断场景）
    chat.addThinkingStep({ content: '未落位的推理', data: { tier: 'expert', identity_name: '实现专家' } })
    chat.addThinkingStep({ content: 'read_file: done (120 chars)', data: { tier: 'expert' } })
    // 已建气泡
    chat.addExpertMessage({ content: '输出1', data: { tier: 'expert', identity_name: '实现专家' } })
    const b1 = chat.messages.find(m => m.kind === 'expert')
    expect(b1._thinking).toContain('未落位的推理')
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [] })
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    sess.switchSession('s1')
    await chat.switchToSession('s1')
    // 切换后：消息重建、气泡关联清空
    expect(chat.messages).toHaveLength(0)
    // 新会话再次出现 expert 输出 → 全新气泡，不带旧会话缓冲/引用
    chat.addExpertMessage({ content: '新会话输出', data: { tier: 'expert', identity_name: '实现专家' } })
    const b2 = chat.messages.find(m => m.kind === 'expert')
    expect(b2._thinking).toBe('')
    expect(b2.content).toBe('新会话输出')
    expect(b2).not.toBe(b1)
  })

  it('气泡关联清空后旧引用不泄漏到新会话', () => {
    const chat = useChatStore()
    chat.addExpertMessage({ content: '主管输出', data: { tier: 'supervisor', identity_name: '代码主管' } })
    const oldBubble = chat.messages.find(m => m.kind === 'expert')
    // init（新建会话）清空
    chat.init()
    expect(chat.messages).toHaveLength(0)
    chat.addExpertMessage({ content: '新输出', data: { tier: 'supervisor', identity_name: '代码主管' } })
    const newBubble = chat.messages.find(m => m.kind === 'expert')
    expect(newBubble).not.toBe(oldBubble)
    expect(newBubble._thinking).toBe('')
  })

  it('思考区剔除"思考结束"段（_stripReplyText 经 addThinkingStep 生效）', () => {
    const chat = useChatStore()
    // 后端"思考结束：{summary}"的 summary 就是最终回复，不应混入思考区
    chat.addThinkingStep({ content: '推理过程。\n思考结束：最终答案在这里' })
    const t = chat.consumeThinking()
    expect(t).toBe('推理过程。')
    expect(t).not.toContain('思考结束')
  })
})

describe('运行轨迹（工具调用从对话流分离）', () => {
  it('工具调用记录进入 traces 而非思考区', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: 'todo: done (391 chars)' })
    chat.addThinkingStep({ content: 'directory_tree: done (1156 chars)' })
    chat.addThinkingStep({ content: '【委托】委托给 code_supervisor：评估项目' })
    // 工具/委托 → traces
    expect(chat.traces.length).toBe(3)
    // 纯推理仍进思考区
    chat.addThinkingStep({ content: '【总指挥】正在分析需求', data: { identity_name: '总指挥' } })
    expect(chat.traces.length).toBe(3)
  })

  it('历史加载 thought 工具调用不混入消息流', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    const sess = useSessionStore()
    sess.switchSession('s1')
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({
      data: [
        { role: 'thought', content: 'todo: done (391 chars)', tier: '' },
        { role: 'thought', content: '我想到应该先分析需求', tier: 'large' },
        { role: 'user', content: '你好' },
      ],
    })
    await chat.switchToSession('s1')
    // 工具调用进 traces（不混入消息流）
    expect(chat.traces.length).toBeGreaterThanOrEqual(1)
    expect(chat.traces.some(t => t.text.includes('todo: done'))).toBe(true)
    // 消息流只有用户消息，不含工具调用；大模型 thought 聚合到回复思考区（无后续回复则丢弃，不独立成"思考"气泡）
    const kinds = chat.messages.map(m => m.kind || m.role)
    expect(kinds.filter(k => k === 'thinking')).toHaveLength(0)
    expect(chat.messages.some(m => (m.content || '').includes('todo: done'))).toBe(false)
  })

  it('历史加载 大模型 thought 聚合到回复思考区而非独立气泡', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    const sess = useSessionStore()
    sess.switchSession('s1')
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({
      data: [
        { role: 'thought', content: '第一轮思考', tier: 'large', id: 's1' },
        { role: 'thought', content: '第二轮思考', tier: 'large', id: 's2' },
        { role: 'assistant', content: '最终回答', tier: 'large', id: 'r1' },
        { role: 'user', content: '你好' },
      ],
    })
    await chat.switchToSession('s1')
    const reply = chat.messages.find(m => m.role === 'assistant' && (m.content || '').includes('最终回答'))
    expect(reply).toBeDefined()
    // 两轮思考聚合到该回复的思考区（运行时折叠，不散成 2 个独立"思考"气泡）
    expect(reply.thinking).toContain('第一轮思考')
    expect(reply.thinking).toContain('第二轮思考')
    const kinds = chat.messages.map(m => m.kind || m.role)
    expect(kinds.filter(k => k === 'thinking')).toHaveLength(0)
  })
})

  it('历史加载 supervisor/expert 思考聚合为独立专家气泡', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    const sess = useSessionStore()
    sess.switchSession('s1')
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({
      data: [
        { role: 'thought', content: '【专家】先分析需求', tier: 'expert', id: 'e1' },
        { role: 'thought', content: 'read_file: done (120 chars)', tier: 'expert', id: 'e2' },
        { role: 'thought', content: '【专家】输出结果', tier: 'expert', id: 'e3' },
        { role: 'user', content: '你好' },
      ],
    })
    await chat.switchToSession('s1')
    const expert = chat.messages.find(m => m.kind === 'expert')
    expect(expert).toBeDefined()
    expect(expert.role).toBe('expert')
    expect(expert._thinking).toContain('先分析需求')
    expect(expert._thinking).toContain('输出结果')
    expect(expert._tools.some(t => t.includes('read_file: done'))).toBe(true)
    // 工具调用不混入消息流
    expect(chat.messages.some(m => (m.content || '').includes('read_file: done'))).toBe(false)
  })


// ── 运行时 expert/supervisor 气泡复用（与恢复聚合一致，§71 同类） ───────────

it('运行时同 tier 多次输出复用同一气泡（不散成多条）', () => {
  const chat = useChatStore()
  chat.addExpertMessage({ content: '第一次输出', data: { tier: 'supervisor', identity_name: '代码主管' } })
  chat.addExpertMessage({ content: '第二次输出', data: { tier: 'supervisor', identity_name: '代码主管' } })
  const bubbles = chat.messages.filter(m => m.kind === 'expert' && m.role === 'supervisor')
  // 同 tier 复用：只有 1 条气泡，内容更新为最新
  expect(bubbles).toHaveLength(1)
  expect(bubbles[0].content).toBe('第二次输出')
})

it('运行时不同 tier 各自独立气泡', () => {
  const chat = useChatStore()
  chat.addExpertMessage({ content: '主管', data: { tier: 'supervisor' } })
  chat.addExpertMessage({ content: '专家', data: { tier: 'expert' } })
  const kinds = chat.messages.filter(m => m.kind === 'expert').map(m => m.role)
  expect(kinds).toEqual(expect.arrayContaining(['supervisor', 'expert']))
})
