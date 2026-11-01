import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from './chat.js'
import { useSessionStore } from './session.js'
import { useWsStore } from '@/ws/store.js'
import { wsClient } from '@/ws/client.js'
import { endpoints } from '@/api.js'

// 让 send 重试循环不真实等待（_sendWithRetry 最多 8×1s）
function stubConnected(value) {
  vi.spyOn(wsClient, 'isConnected').mockReturnValue(value)
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
    // 事件日志默认空：切换会话的测试不应真实发请求
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: { events: [], last_seq: 0 } })
    vi.spyOn(endpoints, 'sessionProjections').mockResolvedValue({ data: { projections: [] } })
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
    const session = useSessionStore()
    session.sessionId = 's1'
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addApproval({ data: { payload: { request_id: 'r1' }, stage_event: {} } })
    chat.approve('r1', true)
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'security_response', request_id: 'r1', approved: true }))
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
    const session = useSessionStore()
    session.sessionId = 's1'
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addIntent({ data: { payload: { request_id: 'i1', question: 'q' } } })
    chat.answerIntent('i1', '答案B')
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'interactive_response', request_id: 'i1', answer: '答案B' }))
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

  it('addThinkingStep 累积思考并 consume（无标签）', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: '步骤一', data: { identity_name: '总指挥' } })
    chat.addThinkingStep({ content: '步骤二' })
    // 大模型连续思考 → 过程流（不再是带【总指挥】标签的思考框）
    expect(chat.processFlow.some(l => l === '步骤一')).toBe(true)
    expect(chat.processFlow.some(l => l === '步骤二')).toBe(true)
    expect(chat.processFlow.some(l => l.includes('总指挥'))).toBe(false)
    // DeepSeek reason 才进 consumeThinking
    const t = chat.consumeThinking()
    expect(t).toBe('')
  })

  it('addThinkingStep: DeepSeek reason 保留去标签进回复思考区', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: '【思考】这是reason推理', data: { identity_name: '总指挥', source: 'reasoning' } })
    const t = chat.consumeThinking()
    expect(t).toContain('这是reason推理')
    expect(t).not.toContain('【思考】')
    expect(t).not.toContain('总指挥')
  })

  it('stop 置 stopped 并 finalize', () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.markProcessing('s1')
    chat.stop()
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'stop' }))
    expect(chat.processing).toBe(false)
    vi.restoreAllMocks()
  })

  it('init 清空全部状态', () => {
    const chat = useChatStore()
    const session = useSessionStore()
    chat.addMessage({ role: 'user', content: 'x' })
    session.sessionId = 'old'
    chat.markProcessing('old')
    chat.init()
    expect(chat.messages).toHaveLength(0)
    expect(chat.processing).toBe(false)
    expect(session.sessionId).toBeNull()
  })

  it('approve 拒绝时发送 approved:false', async () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    const sendSpy = vi.spyOn(wsClient, 'send').mockReturnValue(true)
    chat.addApproval({ data: { payload: { request_id: 'r2' }, stage_event: {} } })
    chat.approve('r2', false)
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'security_response', request_id: 'r2', approved: false }))
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
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'input', content: '你好' }))
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
    chat.markProcessing('s1')
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
    expect(sendSpy).toHaveBeenCalledWith('s1', expect.objectContaining({ type: 'input', content: '重试' }))
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
    const session = useSessionStore()
    session.sessionId = 's1'
    chat.addMessage({ role: 'user', content: 'x' })
    chat.markProcessing('s1')
    chat.hint = '思考中...'
    chat.clearMessages()
    expect(chat.messages).toHaveLength(0)
    expect(chat.processing).toBe(false)
    expect(chat.hint).toBe('')
    expect(chat.runners).toHaveLength(0)
  })

  it('finalizeStream 收尾：更新流式消息、清状态与残留思考', () => {
    const chat = useChatStore()
    const session = useSessionStore()
    session.sessionId = 's1'
    chat.addMessage({ role: 'assistant', content: '初稿' })
    chat.streamingIdx = 0
    chat.markProcessing('s1')
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

  it('deleteMessageAt 删 AI 回复联动移除上方过程流面板；删 user 不动', async () => {
    const chat = useChatStore()
    chat.addMessage({ role: 'user', content: '问' })
    chat.addMessage({ kind: 'process', role: 'system', content: '过程流', runners: null })
    chat.addMessage({ role: 'assistant', content: '答' })
    // 删除 assistant → 上方紧邻 process 面板一并移除
    expect(await chat.deleteMessageAt(2)).toBe(true)
    expect(chat.messages.map(m => m.kind || m.role)).toEqual(['user'])
    // 再建一轮：删除 user 消息不联动
    chat.addMessage({ kind: 'process', role: 'system', content: 'p2' })
    chat.addMessage({ role: 'user', content: '问2' })
    expect(await chat.deleteMessageAt(2)).toBe(true)
    expect(chat.messages).toHaveLength(2)
    // 剩余：user + 过程流面板（删 user 消息不联动）
    expect(chat.messages[0].kind).toBeUndefined()
    expect(chat.messages[1].kind).toBe('process')
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
    expect(chat.messages).toHaveLength(2)
    expect(chat.messages[0].role).toBe('user')
    expect(chat.messages[1].identity_name).toBe('总指挥')
    // 无 process 聚合时 supervisor 思考是过程内容（折叠进回复思考区，不独立成气泡）
    expect(chat.messages[1].kind).not.toBe('expert')
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
    // 大模型思考去重后进入过程流（无标签）
    expect(chat.processFlow.filter(l => l === '步骤X')).toHaveLength(1)
    expect(chat.processFlow.some(l => l.includes('large'))).toBe(false)
    expect(chat.consumeThinking()).toBe('')
  })

  it('supervisor/expert 推理进过程流（不挂气泡，不混入总思考区）', () => {
    const chat = useChatStore()
    chat.addThinkingStep({ content: '正在分析需求', data: { tier: 'supervisor', identity_name: '代码主管' } })
    expect(chat.processFlow.some(l => l === '正在分析需求')).toBe(true)
    expect(chat.processFlow.some(l => l.includes('代码主管'))).toBe(false)
    expect(chat.consumeThinking()).toBe('')
    // 最终输出气泡不含过程推理（主管只发最终回复才显示气泡）
    chat.addExpertMessage({ content: '我负责整体方案', data: { tier: 'supervisor', identity_name: '代码主管' } })
    const b = chat.messages.find(m => m.kind === 'expert')
    expect(b._thinking).toBe('')
    // 旧工具文本摘要 → 过程流（结构化轨迹只由 tool 事件提供）
    chat.addThinkingStep({ content: 'todo: done (50 chars)', data: { tier: 'supervisor', identity_name: '代码主管' } })
    expect(chat.processFlow.some(l => l.includes('todo: done'))).toBe(true)
    expect(chat.traces).toHaveLength(0)
  })

  it('switchToSession 清空主管/专家气泡关联（无残留引用/缓冲）', async () => {
    const chat = useChatStore()
    const sess = useSessionStore()
    // 无输出事件时的缓冲（异常中断场景）：推理进过程流
    chat.addThinkingStep({ content: '未落位的推理', data: { tier: 'expert', identity_name: '实现专家' } })
    chat.addThinkingStep({ content: 'read_file: done (120 chars)', data: { tier: 'expert' } })
    // 已建气泡
    chat.addExpertMessage({ content: '输出1', data: { tier: 'expert', identity_name: '实现专家' } })
    const b1 = chat.messages.find(m => m.kind === 'expert')
    expect(b1._thinking).toBe('')
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

  it('同 tier 不同身份各自独立气泡，互不覆盖（回归：后出现者替换前者）', () => {
    const chat = useChatStore()
    // 两个不同身份的 expert 依次输出
    chat.addExpertMessage({ content: '前端方案', data: { tier: 'expert', identity_name: '前端专家' } })
    chat.addExpertMessage({ content: '后端方案', data: { tier: 'expert', identity_name: '后端专家' } })
    const experts = chat.messages.filter(m => m.kind === 'expert' && m.role === 'expert')
    expect(experts).toHaveLength(2)
    expect(experts.map(m => m.name).sort()).toEqual(['前端专家', '后端专家'])
    expect(experts.map(m => m.content)).toContain('前端方案')
    expect(experts.map(m => m.content)).toContain('后端方案')
    // 同身份再次输出 → 每次新建气泡（不合并、不覆盖他人）
    chat.addExpertMessage({ content: '前端方案v2', data: { tier: 'expert', identity_name: '前端专家' } })
    const experts2 = chat.messages.filter(m => m.kind === 'expert' && m.role === 'expert')
    expect(experts2).toHaveLength(3)
    expect(experts2[2].content).toBe('前端方案v2')
    expect(experts2[2].name).toBe('前端专家')
    // 思考/工具缓冲也按身份隔离
    chat.addExpertThinking({ content: '前端推理', data: { tier: 'expert', identity_name: '前端专家' } })
    expect(experts2.find(m => m.name === '后端专家')._thinking).toBe('')
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

  it('思考区剔除"思考结束"段（_stripReplyText 生效，进过程流）', () => {
    const chat = useChatStore()
    // 后端"思考结束：{summary}"的 summary 就是最终回复，不应混入思考区
    chat.addThinkingStep({ content: '推理过程。\n思考结束：最终答案在这里' })
    expect(chat.processFlow.some(l => l === '推理过程。')).toBe(true)
    expect(chat.processFlow.some(l => l.includes('思考结束'))).toBe(false)
  })
})

describe('运行轨迹（工具调用从对话流分离）', () => {
it('结构化工具事件进入 traces，旧文本摘要只进过程流', () => {
    const chat = useChatStore()
    chat.clearMessages()
    chat.addThinkingStep({ content: 'todo: done (391 chars)' })
    chat.addThinkingStep({ content: 'directory_tree: done (1156 chars)' })
    chat.addThinkingStep({ content: '【委托】委托给 code_supervisor：评估项目' })
    // 文本摘要（旧协议）不再冒充结构化轨迹
    expect(chat.traces).toHaveLength(0)
    expect(chat.processFlow.some(l => l.includes('todo: done'))).toBe(true)
    // 结构化事件 → 一条运行中的轨迹，result 原地补齐
    chat.addToolCall({ data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' }, status: 'running' } })
    expect(chat.traces).toHaveLength(1)
    expect(chat.traces[0]).toMatchObject({ kind: 'tool', name: 'read_file', status: 'running' })
    chat.addToolResult({ data: { call_id: 'c1', name: 'read_file', status: 'ok', duration_ms: 12.5, result_preview: 'x = 1', result_chars: 5 } })
    expect(chat.traces).toHaveLength(1)
    expect(chat.traces[0]).toMatchObject({ status: 'ok', durationMs: 12.5, preview: 'x = 1' })
    // 纯推理进过程流（无【总指挥】标签）
    chat.addThinkingStep({ content: '【总指挥】正在分析需求', data: { identity_name: '总指挥' } })
    expect(chat.traces).toHaveLength(1)
    expect(chat.processFlow.some(l => l === '正在分析需求')).toBe(true)
    expect(chat.processFlow.some(l => l.includes('总指挥'))).toBe(false)
  })

  it('工具结果先于调用到达时补一条已落定轨迹', () => {
    const chat = useChatStore()
    chat.clearMessages()
    chat.addToolResult({ data: { call_id: 'c9', name: 'web_search', status: 'timeout', duration_ms: 30000, error: '执行超时' } })
    expect(chat.traces).toHaveLength(1)
    expect(chat.traces[0]).toMatchObject({ kind: 'tool', status: 'timeout', error: '执行超时' })
  })

  it('工具失败状态保留错误信息与参数（不再依赖正则）', () => {
    const chat = useChatStore()
    chat.clearMessages()
    chat.addToolCall({ data: { call_id: 'c2', name: 'exec_command', args: { command: 'pytest' } } })
    chat.addToolResult({ data: { call_id: 'c2', name: 'exec_command', status: 'error', duration_ms: 800, error: 'exit 1' } })
    const tr = chat.traces[0]
    expect(tr.status).toBe('error')
    expect(tr.error).toBe('exit 1')
    expect(tr.args.command).toBe('pytest')
  })

  it('工具卡片 view 随调用建立、随结果覆盖（Phase 2）', () => {
    const chat = useChatStore()
    chat.clearMessages()
    chat.addToolCall({ data: {
      call_id: 'c1', name: 'exec_command', args: { command: 'pytest -q' },
      view: { card: 'terminal', title: 'pytest -q', command: 'pytest -q', exit_code: null, output: '' },
    } })
    expect(chat.traces[0].view.card).toBe('terminal')
    chat.addToolResult({ data: {
      call_id: 'c1', name: 'exec_command', status: 'ok', duration_ms: 20,
      view: { card: 'terminal', title: 'pytest -q', command: 'pytest -q', exit_code: 0, output: '3 passed' },
    } })
    expect(chat.traces[0].view.exit_code).toBe(0)
    expect(chat.traces[0].view.output).toBe('3 passed')
  })

  it('结果无 view 时清空调用骨架 → 回退 generic 卡片', () => {
    const chat = useChatStore()
    chat.clearMessages()
    chat.addToolCall({ data: { call_id: 'c1', name: 'write_file', view: { card: 'diff', title: 'x.py', diffs: [] } } })
    chat.addToolResult({ data: { call_id: 'c1', name: 'write_file', status: 'ok', result_preview: 'written' } })
    expect(chat.traces[0].view).toBe(null)
    expect(chat.traces[0].preview).toBe('written')
  })

  it('同一 call_id 的重复事件只建一条轨迹（回放/实时重叠）', () => {
    const chat = useChatStore()
    chat.clearMessages()
    chat.addToolCall({ data: { call_id: 'c1', name: 'read_file', args: {}, status: 'running' } })
    chat.addToolCall({ data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' }, status: 'running' } })
    expect(chat.traces).toHaveLength(1)
    // 后到的参数补上（回放可能比实时帧信息更全）
    expect(chat.traces[0].args.path).toBe('a.py')
  })

  it('切换会话时按事件日志回放工具轨迹（刷新后与运行中一致）', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [{ role: 'user', content: '你好' }] })
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: { last_seq: 3, events: [
      { type: 'tool', event: 'tool_call', _seq: 1, data: { call_id: 'c1', name: 'read_file', args: { path: 'a.py' }, status: 'running' } },
      { type: 'thinking', event: 'model_comm', _seq: 2, content: '推理' },
      { type: 'tool', event: 'tool_result', _seq: 3, data: { call_id: 'c1', name: 'read_file', status: 'ok', duration_ms: 12, result_preview: 'x = 1' } },
    ] } })
    await chat.switchToSession('s1')
    expect(chat.traces).toHaveLength(1)
    expect(chat.traces[0]).toMatchObject({ callId: 'c1', name: 'read_file', status: 'ok', durationMs: 12, preview: 'x = 1' })
    expect(chat.traces[0].args.path).toBe('a.py')
  })

  it('事件回放按 seq 分页，不遗漏后续工具调用', async () => {
    const chat = useChatStore()
    chat.clearMessages()
    const page1 = { data: { events: [
      { type: 'tool', event: 'tool_call', _seq: 1, data: { call_id: 'a', name: 'read_file' } },
      { type: 'tool', event: 'tool_result', _seq: 2, data: { call_id: 'a', status: 'ok' } },
    ] } }
    const page2 = { data: { events: [
      { type: 'tool', event: 'tool_call', _seq: 3, data: { call_id: 'b', name: 'web_search' } },
    ] } }
    const spy = vi.spyOn(endpoints, 'sessionEvents')
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2)
    await chat.replayToolEvents('s1', 2)
    expect(spy).toHaveBeenCalledTimes(2)
    expect(spy.mock.calls[1][1]).toBe(2)
    expect(chat.traces.map(t => t.name)).toEqual(['read_file', 'web_search'])
  })

  it('事件接口不可用时静默降级（不影响会话加载）', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [{ role: 'user', content: '你好' }] })
    vi.spyOn(endpoints, 'sessionEvents').mockRejectedValue(new Error('boom'))
    await chat.switchToSession('s1')
    expect(chat.traces).toHaveLength(0)
    expect(chat.messages).toHaveLength(1)
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

  it('历史加载 supervisor/expert 思考为过程内容（无 process 聚合不独立成专家气泡）', async () => {
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
    // 无 process 聚合时 expert 思考是过程内容，不独立成专家气泡
    const expert = chat.messages.find(m => m.kind === 'expert')
    expect(expert).toBeUndefined()
    // 工具调用不混入消息流
    expect(chat.messages.some(m => (m.content || '').includes('read_file: done'))).toBe(false)
  })

  it('历史加载 security tier 的 thought 跳过（审批/提问瞬态事件不混入思考区）', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    const sess = useSessionStore()
    sess.switchSession('s1')
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({
      data: [
        { role: 'thought', content: '【安全审查】request_mode_change 等待用户审批', tier: 'security', id: 's1' },
        { role: 'thought', content: '【安全审查】exec_command 等待用户审批', tier: 'security', id: 's2' },
        { role: 'assistant', content: '最终回复', tier: 'large', id: 'r1' },
        { role: 'user', content: '你好' },
      ],
    })
    await chat.switchToSession('s1')
    const reply = chat.messages.find(m => m.role === 'assistant')
    // security 事件不折叠进回复思考区
    expect(reply.thinking || '').not.toContain('安全审查')
    // 也不作为独立消息/思考气泡
    const kinds = chat.messages.map(m => m.kind || m.role)
    expect(kinds.filter(k => k === 'thinking')).toHaveLength(0)
  })



describe('专家气泡复用与统一分类（架构一致性）', () => {
  beforeEach(() => { setActivePinia(createPinia()) })
// ── 运行时 expert/supervisor 气泡复用（与恢复聚合一致，§71 同类） ───────────

it('运行时同 tier 多次输出各自独立气泡（不合并）', () => {
  const chat = useChatStore()
  chat.addExpertMessage({ content: '第一次输出', data: { tier: 'supervisor', identity_name: '代码主管' } })
  chat.addExpertMessage({ content: '第二次输出', data: { tier: 'supervisor', identity_name: '代码主管' } })
  const bubbles = chat.messages.filter(m => m.kind === 'expert' && m.role === 'supervisor')
  // 每次输出独立气泡（§85 修复：同身份不合并）
  expect(bubbles).toHaveLength(2)
  expect(bubbles[0].content).toBe('第一次输出')
  expect(bubbles[1].content).toBe('第二次输出')
})

it('运行时不同 tier 各自独立气泡', () => {
  const chat = useChatStore()
  chat.addExpertMessage({ content: '主管', data: { tier: 'supervisor' } })
  chat.addExpertMessage({ content: '专家', data: { tier: 'expert' } })
  const kinds = chat.messages.filter(m => m.kind === 'expert').map(m => m.role)
  expect(kinds).toEqual(expect.arrayContaining(['supervisor', 'expert']))
})


// ── 架构一致性：运行时 dispatchThinking 与恢复 classifyThinking 用同一分类规则 ──

it('classifyThinking 分类规则（运行时/恢复共用）', () => {
  const chat = useChatStore()
  // security → skip
  expect(chat.classifyThinking({ content: 'x', data: { tier: 'security' } }).kind).toBe('skip')
  // 工具 trace → tool_trace
  expect(chat.classifyThinking({ content: 'read_file: done (120 chars)', data: { tier: 'thinking' } }).kind).toBe('tool_trace')
  // supervisor 推理（无 entry_type / thought）→ thinking（过程流），不再视为最终气泡
  expect(chat.classifyThinking({ content: '思考', data: { tier: 'supervisor' } }).kind).toBe('thinking')
  // supervisor 最终回复（entry_type='response'）→ expert（气泡）
  expect(chat.classifyThinking({ content: '回复', data: { tier: 'supervisor', entry_type: 'response' } }).kind).toBe('expert')
  expect(chat.classifyThinking({ content: '回复', data: { tier: 'expert', entry_type: 'response' } }).kind).toBe('expert')
  // 大模型思考 → thinking
  expect(chat.classifyThinking({ content: '推理', data: { tier: 'thinking' } }).kind).toBe('thinking')
  expect(chat.classifyThinking({ content: '推理', data: { tier: '' } }).kind).toBe('thinking')
})

it('dispatchThinking: expert 输出（response）创建气泡，推理进过程流', () => {
  const chat = useChatStore()
  // 推理事件（role=thinking + tier=supervisor）→ 过程流（不再挂气泡思考区）
  chat.dispatchThinking({ content: '先分析需求', role: 'thinking', data: { tier: 'supervisor', identity_name: '代码主管' } })
  expect(chat.processFlow.some(l => l.includes('先分析需求'))).toBe(true)
  // 最终回复事件（role=supervisor + entry_type=response）→ 创建气泡
  chat.dispatchThinking({ content: '我负责整体方案', role: 'supervisor', data: { tier: 'supervisor', identity_name: '代码主管', entry_type: 'response' } })
  const bubble = chat.messages.find(m => m.kind === 'expert')
  expect(bubble).toBeDefined()
  expect(bubble.content).toBe('我负责整体方案')
  // 再次输出 → 新建气泡（§85 修复：同身份不合并）
  chat.dispatchThinking({ content: '补充方案', role: 'supervisor', data: { tier: 'supervisor', identity_name: '代码主管', entry_type: 'response' } })
  const bubbles = chat.messages.filter(m => m.kind === 'expert')
  expect(bubbles).toHaveLength(2)
  expect(bubbles[1].content).toBe('补充方案')
})

it('dispatchThinking: tool_trace 只进过程流（结构化轨迹由 tool 事件负责）', () => {
  const chat = useChatStore()
  chat.dispatchThinking({ content: 'calc: done (3 chars)', role: 'thinking', data: { tier: 'thinking' } })
  expect(chat.traces).toHaveLength(0)
  expect(chat.processFlow.some(l => l.includes('calc: done'))).toBe(true)
  // 专家工具 trace → 也进过程流
  chat.dispatchThinking({ content: 'read_file: done (120 chars)', role: 'thinking', data: { tier: 'expert' } })
  expect(chat.processFlow.some(l => l.includes('read_file: done'))).toBe(true)
})

})

// ── 流式正文（Phase 3）：chunk 帧 → 尾巴气泡 → 最终 message 替换 ──

describe('流式正文尾巴', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: { events: [], last_seq: 0 } })
    vi.spyOn(endpoints, 'sessionProjections').mockResolvedValue({ data: { projections: [] } })
  })

  it('同一轮累加到同一个尾巴，换轮重置，clearStream 清空', () => {
    const chat = useChatStore()
    chat.appendStreamChunk({ content: '你', data: { tier: 'large', turn: 0 } })
    chat.appendStreamChunk({ content: '好', data: { tier: 'large', turn: 0 } })
    expect(chat.streamTail.content).toBe('你好')
    expect(chat.streamTail.tier).toBe('large')
    // 换轮（工具循环下一轮）→ 重新开始，避免拼接两轮内容
    chat.appendStreamChunk({ content: '换一轮', data: { tier: 'large', turn: 1 } })
    expect(chat.streamTail.content).toBe('换一轮')
    chat.clearStream()
    expect(chat.streamTail).toBe(null)
  })

  it('空增量忽略；身份名随首个非空帧补上', () => {
    const chat = useChatStore()
    chat.appendStreamChunk({ content: '' })
    expect(chat.streamTail).toBe(null)
    chat.appendStreamChunk({ content: '正文', data: { tier: 'large', identity_name: '总指挥' } })
    expect(chat.streamTail.identityName).toBe('总指挥')
  })

  it('finalizeStream / clearMessages 都会丢弃未落定的尾巴', () => {
    const chat = useChatStore()
    chat.appendStreamChunk({ content: '半句', data: { tier: 'large' } })
    chat.finalizeStream('')
    expect(chat.streamTail).toBe(null)
    chat.appendStreamChunk({ content: '半句', data: { tier: 'large' } })
    chat.clearMessages()
    expect(chat.streamTail).toBe(null)
  })

  it('classifyThinking：大模型 streaming_delta 跳过（已由 chunk 帧渲染）', () => {
    const chat = useChatStore()
    expect(chat.classifyThinking({ content: '正在输入', data: { tier: 'large', entry_type: 'streaming_delta' } }).kind).toBe('skip')
    // 主管/专家的流式增量仍进过程流（他们没有流式气泡）
    expect(chat.classifyThinking({ content: '专家在写', data: { tier: 'expert', entry_type: 'streaming_delta' } }).kind).toBe('thinking')
  })
})

// ── 轨迹入流（Phase 3）：工具卡片锚定到“调用发生时最后一条消息” ──

describe('轨迹入流锚点', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: { events: [], last_seq: 0 } })
  })

  it('锚定到最后一条消息；无消息时留空（走折叠面板）', () => {
    const chat = useChatStore()
    chat.addToolCall({ data: { call_id: 'c0', name: 'read_file' } })
    expect(chat.traces[0].afterKey).toBe('')
    chat.addMessage({ role: 'user', content: '问' })
    const key = chat.messages[0]._id
    chat.addToolCall({ data: { call_id: 'c1', name: 'read_file' } })
    expect(chat.traces[1].afterKey).toBe(key)
    // ack 回填服务端 id 后，新调用用 id 锚定（渲染时 id / _id 都能命中）
    chat.messages[0].id = 'srv1'
    chat.addToolCall({ data: { call_id: 'c2', name: 'read_file' } })
    expect(chat.traces[2].afterKey).toBe('srv1')
  })

  it('回放：同一轮内按 user_message / message 事件交错定位（说一句 → 调工具 → 接着说）', async () => {
    const chat = useChatStore()
    // 历史消息从 DB 加载（带服务端 id）
    chat.messages = [
      { _id: 'm1', id: 'u1', role: 'user', content: '问' },
      { _id: 'm2', id: 'a1', role: 'assistant', content: '答' },
    ]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, type: 'user_message', event: 'user_message', data: { message_id: 'u1' } },
        { _seq: 2, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file' } },
        { _seq: 3, type: 'message', event: 'assistant_message', data: { message_id: 'a1' } },
        { _seq: 4, type: 'tool', event: 'tool_call', data: { call_id: 'c2', name: 'exec_command' } },
      ],
      last_seq: 4,
    } })
    await chat.replayToolEvents('s1')
    // 助手终稿之后的工具挂到那条回复下，而不是一律挂在用户消息下
    expect(chat.traces.map(t => t.afterKey)).toEqual(['u1', 'a1'])
  })

  it('回放：终稿没落库（id 不在消息里）时保留上一锚点', async () => {
    const chat = useChatStore()
    chat.messages = [{ _id: 'm1', id: 'u1', role: 'user', content: '问' }]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, type: 'user_message', event: 'user_message', data: { message_id: 'u1' } },
        { _seq: 2, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file' } },
        // 中断残稿 / 终稿未落库：事件里没有 message_id，或 id 对不上任何消息
        { _seq: 3, type: 'message', event: 'assistant_message', data: {} },
        { _seq: 4, type: 'message', event: 'assistant_message', data: { message_id: 'ghost' } },
        { _seq: 5, type: 'tool', event: 'tool_call', data: { call_id: 'c2', name: 'exec_command' } },
      ],
      last_seq: 5,
    } })
    await chat.replayToolEvents('s1')
    expect(chat.traces.map(t => t.afterKey)).toEqual(['u1', 'u1'])
  })

  it('回放：老会话（无 user_message 事件）按时间回退到此前最近一条用户消息', async () => {
    const chat = useChatStore()
    chat.messages = [
      { _id: 'm1', id: 'u1', role: 'user', content: '问一' },
      { _id: 'm2', id: 'a1', role: 'assistant', content: '答一' },
      { _id: 'm3', id: 'u2', role: 'user', content: '问二' },
    ]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, timestamp: 1100, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file' } },
        // 用户消息之前就没有可锚定的消息 → 留空，仍进折叠面板
        { _seq: 2, timestamp: 900, type: 'tool', event: 'tool_call', data: { call_id: 'c0', name: 'read_file' } },
      ],
      last_seq: 2,
    } })
    await chat.replayToolEvents('s1', 500, [
      { key: 'u1', ts: 1000 },
      { key: 'u2', ts: 2000 },
    ])
    expect(chat.traces.map(t => t.afterKey)).toEqual(['u1', ''])
  })

  it('回放：user_message 事件优先于时间回退', async () => {
    const chat = useChatStore()
    chat.messages = [{ _id: 'm1', id: 'u1', role: 'user', content: '问' }]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, type: 'user_message', event: 'user_message', data: { message_id: 'u2' } },
        { _seq: 2, timestamp: 1100, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file' } },
      ],
      last_seq: 2,
    } })
    await chat.replayToolEvents('s1', 500, [{ key: 'u1', ts: 1000 }])
    expect(chat.traces[0].afterKey).toBe('u2')
  })

  it('切换会话：老会话按消息时间给工具定位（switchToSession 链路）', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [
      { id: 'u1', role: 'user', content: '问', created_at: '2026-10-30T01:00:00.123456' },
      { id: 'a1', role: 'assistant', content: '答', created_at: '2026-10-30T01:00:20' },
    ] })
    // chat_messages.created_at 与 session_events.timestamp 同为 UTC（naive）
    const ts = Date.parse('2026-10-30T01:00:03Z') / 1000
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, timestamp: ts, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file' } },
      ],
      last_seq: 1,
    } })
    await chat.switchToSession('s1')
    expect(chat.traces[0].afterKey).toBe('u1')
  })

  it('回放：老会话没有 user_message 事件时保持无锚点（不猜位置）', async () => {
    const chat = useChatStore()
    chat.messages = [{ _id: 'm1', id: 'a1', role: 'assistant', content: '答' }]
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: {
      events: [
        { _seq: 1, type: 'message', event: 'assistant_message', data: { message_id: 'a1' } },
        { _seq: 2, type: 'tool', event: 'tool_call', data: { call_id: 'c1', name: 'read_file' } },
      ],
      last_seq: 2,
    } })
    await chat.replayToolEvents('s1')
    expect(chat.traces[0].afterKey).toBe('')
  })

  it('结果先到（补建轨迹）时沿用事件带的锚点', () => {
    const chat = useChatStore()
    chat.addToolResult({ data: { call_id: 'c9', name: 'read_file', status: 'timeout', afterKey: '' } })
    expect(chat.traces[0].afterKey).toBe('')
  })
})

// ── 会话投影（Phase 4）：整值快照 + higher-seq-wins ──

describe('会话投影', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: { events: [], last_seq: 0 } })
    vi.spyOn(endpoints, 'sessionProjections').mockResolvedValue({ data: { projections: [] } })
  })

  it('todos 快照直接覆盖列表（不再需要额外请求）', () => {
    const chat = useChatStore()
    expect(chat.applyProjection({
      key: 'todos', seq: 3,
      value: { todos: [{ id: 't1', content: '写测试', status: 'pending' }], done: 0, total: 1 },
    })).toBe(true)
    expect(chat.todos).toHaveLength(1)
    expect(chat.todos[0].content).toBe('写测试')
    expect(chat.projections.todos.total).toBe(1)
  })

  it('contextPressure 应用 tokens/window（0 也要应用：投影是权威快照）', () => {
    const chat = useChatStore()
    chat.applyProjection({ key: 'contextPressure', seq: 5, value: { tokens: 3200, window: 128000, ratio: 0.025 } })
    expect(chat.contextTokens).toBe(3200)
    expect(chat.contextWindowSize).toBe(128000)
    chat.applyProjection({ key: 'contextPressure', seq: 6, value: { tokens: 0, window: 128000, ratio: 0 } })
    expect(chat.contextTokens).toBe(0)
  })

  it('runners 快照展开成扁平列表（与 thinking_progress 同一套形状）', () => {
    const chat = useChatStore()
    chat.applyProjection({
      key: 'runners', seq: 8,
      value: {
        large_model: { model_id: 'large_1', name: '总指挥', status: 'running', context_tokens: 100, context_window_size: 1000 },
        active_supervisors: [{ model_id: 'sup_1', name: '主管', supervisor: '' }],
        active_experts: [{ model_id: 'exp_1', name: '专家', supervisor: 'sup_1' }],
        context_tokens: 100, context_window_size: 1000,
      },
    })
    expect(chat.runners.map(r => r.tier)).toEqual(['large', 'supervisor', 'expert'])
    expect(chat.runners[2].supervisor).toBe('sup_1')
    expect(chat.contextTokens).toBe(100)
  })

  it('旧 seq 丢弃、新 seq 覆盖、同 seq 也丢弃', () => {
    const chat = useChatStore()
    chat.applyProjection({ key: 'todos', seq: 10, value: { todos: [{ id: 'new' }], total: 1 } })
    expect(chat.applyProjection({ key: 'todos', seq: 9, value: { todos: [{ id: 'old' }], total: 1 } })).toBe(false)
    expect(chat.todos[0].id).toBe('new')
    expect(chat.applyProjection({ key: 'todos', seq: 10, value: { todos: [{ id: 'same' }], total: 1 } })).toBe(false)
    expect(chat.todos[0].id).toBe('new')
    expect(chat.applyProjection({ key: 'todos', seq: 11, value: { todos: [{ id: 'newer' }], total: 1 } })).toBe(true)
    expect(chat.todos[0].id).toBe('newer')
  })

  it('seq 按 key 独立去重（不同 key 互不影响）', () => {
    const chat = useChatStore()
    chat.applyProjection({ key: 'todos', seq: 20, value: { todos: [], total: 0 } })
    expect(chat.applyProjection({ key: 'runners', seq: 5, value: { large_model: null } })).toBe(true)
  })

  it('缺 key / 缺 seq 的帧：无 key 忽略，无 seq 照常应用', () => {
    const chat = useChatStore()
    expect(chat.applyProjection({ value: { todos: [] } })).toBe(false)
    expect(chat.applyProjection({ key: 'todos', value: { todos: [{ id: 'x' }], total: 1 } })).toBe(true)
    expect(chat.todos[0].id).toBe('x')
  })

  it('tokenUsage 快照保存到 store', () => {
    const chat = useChatStore()
    chat.applyProjection({ key: 'tokenUsage', seq: 4, value: { prompt: 10, completion: 5, total: 15, by_tier: {} } })
    expect(chat.tokenUsage.total).toBe(15)
  })

  it('resetProjections 清空状态与 seq 水位（新会话不会继承旧快照）', () => {
    const chat = useChatStore()
    chat.applyProjection({ key: 'todos', seq: 30, value: { todos: [{ id: 'a' }], total: 1 } })
    chat.applyProjection({ key: 'contextPressure', seq: 31, value: { tokens: 9, window: 99, ratio: 0.09 } })
    chat.resetProjections()
    expect(chat.todos).toEqual([])
    expect(chat.contextTokens).toBe(0)
    expect(chat.projections).toEqual({})
    // 水位已清 → 小 seq 也能应用
    expect(chat.applyProjection({ key: 'todos', seq: 2, value: { todos: [], total: 0 } })).toBe(true)
  })

  it('switchToSession 读回该会话的投影快照', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [{ role: 'user', content: '你好' }] })
    vi.spyOn(endpoints, 'sessionProjections').mockResolvedValue({ data: { projections: [
      { key: 'todos', seq: 7, value: { todos: [{ id: 't1', status: 'pending' }], done: 0, total: 1 } },
      { key: 'contextPressure', seq: 8, value: { tokens: 640, window: 6400, ratio: 0.1 } },
      { key: 'tokenUsage', seq: 9, value: { prompt: 1, completion: 2, total: 3, by_tier: {} } },
    ] } })
    await chat.switchToSession('s1')
    expect(chat.todos).toHaveLength(1)
    expect(chat.contextTokens).toBe(640)
    expect(chat.contextWindowSize).toBe(6400)
    expect(chat.tokenUsage.total).toBe(3)
  })

  it('switchToSession 前清空旧会话投影（不显示别人的 todo）', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    chat.applyProjection({ key: 'todos', seq: 50, value: { todos: [{ id: 'old' }], total: 1 } })
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [{ role: 'user', content: '你好' }] })
    await chat.switchToSession('s1')
    expect(chat.todos).toEqual([])
  })

  it('投影接口失败不影响切换会话', async () => {
    vi.spyOn(wsClient, 'connect').mockResolvedValue(true)
    const chat = useChatStore()
    vi.spyOn(endpoints, 'sessionMessages').mockResolvedValue({ data: [{ role: 'user', content: '你好' }] })
    vi.spyOn(endpoints, 'sessionProjections').mockRejectedValue(new Error('boom'))
    await chat.switchToSession('s1')
    expect(chat.messages).toHaveLength(1)
    expect(chat.todos).toEqual([])
  })
})

// ── 可应答请求（Phase 4.b）：整快照 → 横幅恢复 / 失效标记 / 输入区接管 ──

describe('可应答请求', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(endpoints, 'sessionEvents').mockResolvedValue({ data: { events: [], last_seq: 0 } })
    vi.spyOn(endpoints, 'sessionProjections').mockResolvedValue({ data: { projections: [] } })
  })

  it('审批快照 → 建审批横幅并置 hasPendingRequest', () => {
    const chat = useChatStore()
    chat.applyPendingRequests([
      { request_id: 'r1', kind: 'approval', target: 'exec_command', detail: '执行 rm -rf' },
    ])
    expect(chat.hasPendingRequest).toBe(true)
    const banner = chat.messages.find(m => m.kind === 'approval')
    expect(banner.requestId).toBe('r1')
    expect(banner.target).toBe('exec_command')
    expect(banner.resolved).toBe(false)
  })

  it('提问快照 → 建提问横幅（带选项）', () => {
    const chat = useChatStore()
    chat.applyPendingRequests([
      { request_id: 'q1', kind: 'question', question: '选哪个？', options: ['A', 'B'] },
    ])
    const banner = chat.messages.find(m => m.kind === 'intent')
    expect(banner.question).toBe('选哪个？')
    expect(banner.options).toEqual(['A', 'B'])
  })

  it('重复快照不重复建横幅（request_id 去重）', () => {
    const chat = useChatStore()
    const req = { request_id: 'r1', kind: 'approval', target: 'exec_command' }
    chat.applyPendingRequests([req])
    chat.applyPendingRequests([req])
    expect(chat.messages.filter(m => m.kind === 'approval')).toHaveLength(1)
  })

  it('请求从快照里消失 → 横幅标记失效（不再留下点不动的按钮）', () => {
    const chat = useChatStore()
    chat.applyPendingRequests([{ request_id: 'r2', kind: 'approval', target: 'exec_command' }])
    chat.applyPendingRequests([])
    const banner = chat.messages.find(m => m.kind === 'approval')
    expect(banner.resolved).toBe(true)
    expect(banner.stale).toBe(true)
    expect(chat.hasPendingRequest).toBe(false)
  })

  it('本地已作答的横幅不被快照覆盖成失效', () => {
    const chat = useChatStore()
    chat.applyPendingRequests([{ request_id: 'r3', kind: 'approval', target: 'exec_command' }])
    const banner = chat.messages.find(m => m.kind === 'approval')
    banner.resolved = true
    banner.approved = true            // 用户点了批准
    chat.applyPendingRequests([])
    expect(banner.approved).toBe(true)
    expect(banner.stale).toBeUndefined()
  })

  it('resetPending 清空列表（切会话/清空对话）', () => {
    const chat = useChatStore()
    chat.applyPendingRequests([{ request_id: 'r1', kind: 'approval', target: 'x' }])
    chat.resetPending()
    expect(chat.hasPendingRequest).toBe(false)
    expect(chat.pendingRequests).toEqual([])
  })

  it('非法快照（非数组/缺 request_id）被忽略', () => {
    const chat = useChatStore()
    chat.applyPendingRequests(null)
    chat.applyPendingRequests([{ kind: 'approval' }, null])
    expect(chat.pendingRequests).toEqual([])
    expect(chat.messages).toHaveLength(0)
  })
})
