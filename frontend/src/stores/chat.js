import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session.js'
import { useWsStore } from '@/ws/store.js'
import { endpoints } from '@/api.js'

let _uid = 0
function uid() { return 'm' + (++_uid) }

// ── 身份系统（对齐 js/pages/chat.js） ──
const _nameForRole = { user: '我', supervisor: '主管', expert: '专家' }

// 工具调用记录识别（借鉴 dsh 轨迹：工具/委托等中间步骤从对话流分离到轨迹面板）
function _isToolTrace(text) {
  return /^[a-z_]+: (done|ok|error|→|exit=)/.test(text) ||
    /: done \(\d+ chars\)/.test(text) ||
    /^【(委托|创建主管|主管异常)/.test(text)
}

function _cleanThinking(text) {
  return String(text || '')
    .replace(/^[\s\u{2600}-\u{27BF}\u{1F000}-\u{1FAFF}\u{2B00}-\u{2BFF}]+/u, '')
    .replace(/^(\[[^\]]*\]\s*)+/, '')
    .replace(/[ \t\n\r]+$/, '')
}

function _avatarCls(role) {
  const r = String(role || '').toLowerCase()
  if (r === 'supervisor') return 'avatar-supervisor'
  if (r === 'expert') return 'avatar-expert'
  if (r === 'user') return 'avatar-user'
  return 'avatar-large'
}

function _nameFor(role) {
  return _nameForRole[String(role || '').toLowerCase()] || '总指挥'
}

export const useChatStore = defineStore('chat', () => {
  const session = useSessionStore()
  const ws = useWsStore()

  const messages = ref([])
  const processing = ref(false)
  const currentModel = ref('large')
  const streamingIdx = ref(-1)
  // 状态提示（对齐 js chatHint）：'' | '思考中...' | '会话正在处理中，请稍候…'
  const hint = ref('')
  const elapsed = ref(0)
  // 用户主动停止后，忽略后端后续补发的 message（避免"按了停止又冒出回复"）
  const _stopped = ref(false)
  const _lastInput = ref(null)
  // 正在处理的会话 id（跨会话保留处理状态：切走后仍显示思考横幅、可停止）
  const _processingSid = ref(null)
  // 当前 WS 连接的会话 id
  const _connectedSid = ref(null)
  // 思考循环在线模型状态（由 thinking_progress 解析）：[{model_id, tier, name, status, status_detail, round, max_turns, supervisor, last_thought}]
  const runners = ref([])
  // 运行轨迹：工具调用/委托等中间步骤（借鉴 dsh，从对话流分离）
  const traces = ref([])
  // 主管/专家气泡：按 tier 关联其思考过程（_thinking）与工具调用（_tools）
  const _expertBubbles = new Map()
  const _pendingByTier = {}
  const _pendingTools = {}

  function _tierOf(d) {
    return String(d.data?.tier || d.data?.dialog_tier || d.tier || '').toLowerCase()
  }

  function _clearExpertState() {
    _expertBubbles.clear()
    for (const k of Object.keys(_pendingByTier)) _pendingByTier[k] = ''
    for (const k of Object.keys(_pendingTools)) _pendingTools[k] = []
  }

  // 主管/专家的推理 → 挂到该身份气泡（未创建前缓冲）
  function addExpertThinking(d) {
    const tier = _tierOf(d)
    if (tier !== 'supervisor' && tier !== 'expert') return
    const text = _stripReplyText(_cleanThinking(d.content))
    if (!text) return
    const ident = d.data?.identity_name || ''
    const line = (ident ? `【${ident}】` : '') + text
    const bubble = _expertBubbles.get(tier)
    if (bubble) {
      const cur = bubble._thinking || ''
      if (!cur.includes(line)) bubble._thinking = cur ? cur + '\n\n' + line : line
    } else {
      const cur = _pendingByTier[tier] || ''
      if (!cur.includes(line)) _pendingByTier[tier] = cur ? cur + '\n\n' + line : line
    }
  }

  // 主管/专家的工具调用 → 轨迹面板 + 该身份气泡工具列表
  function addExpertTool(d, traceText) {
    const tier = _tierOf(d)
    if (tier !== 'supervisor' && tier !== 'expert') return
    const text = (traceText || _stripReplyText(_cleanThinking(d.content))).slice(0, 200)
    if (text) traces.value.push({ text, time: Date.now() })
    const bubble = _expertBubbles.get(tier)
    if (bubble) {
      const cur = bubble._tools || []
      if (!cur.includes(text)) bubble._tools = [...cur, text]
    } else {
      const cur = _pendingTools[tier] || []
      if (!cur.includes(text)) _pendingTools[tier] = [...cur, text]
    }
  }

  // 主管/专家的实际输出 → 创建独立气泡（携带该身份的思考过程与工具调用）
  function addExpertMessage(d) {
    const tier = _tierOf(d)
    if (tier !== 'supervisor' && tier !== 'expert') return
    const ident = d.data?.identity_name || ''
    const name = ident || (tier === 'supervisor' ? '主管' : '专家')
    const msg = {
      role: tier,
      content: d.content || '',
      kind: 'expert',
      name,
      avatarCls: tier === 'supervisor' ? 'avatar-supervisor' : 'avatar-expert',
      id: '',
      _thinking: _pendingByTier[tier] || '',
      _tools: _pendingTools[tier] || [],
      _expanded: false,
    }
    _pendingByTier[tier] = ''
    _pendingTools[tier] = []
    messages.value.push({ _id: uid(), ...msg })
    _expertBubbles.set(tier, messages.value[messages.value.length - 1])
  }

  async function init() {
    // 新建会话：断开旧 WS、清空会话 id 与消息（懒创建，首条消息发送时才建新会话）
    try { ws.wsClient.disconnect() } catch {}
    session.sessionId = null
    session.currentTitle = '新会话'
    messages.value = []
    processing.value = false
    hint.value = ''
    elapsed.value = 0
    streamingIdx.value = -1
    _stopped.value = false
    _lastInput.value = null
    _processingSid.value = null
    _connectedSid.value = null
    runners.value = []
    traces.value = []
    _clearExpertState()
    ws.reset()
  }

  async function switchToSession(sid) {
    const oldSid = session.sessionId
    session.switchSession(sid)
    messages.value = []
    _clearExpertState()
    // 清空模型状态：避免显示旧会话的思考循环（新会话的 thinking_progress 会重新填充）
    runners.value = []
    // 保留处理状态：若另有会话仍在处理中，不重置 processing/hint（横幅+停止按钮继续生效）
    _stopped.value = false
    // 双源加载：优先 /stream 消息，空则回退 /management dialog
    let msgs = []
    try {
      const r = await endpoints.sessionMessages(sid, 100)
      msgs = (r.data && Array.isArray(r.data)) ? r.data : (r.data?.messages || [])
    } catch {}
    if (msgs.length === 0) {
      try {
        const r2 = await endpoints.sessionDialog(sid, 100)
        msgs = (r2.data?.dialog || []).map(d => ({
          role: d.role || d.sender || 'assistant',
          content: d.content || d.text || '',
          created_at: d.timestamp || d.time,
        }))
      } catch {}
    }
    // 预扫描：聚合 supervisor/expert 的思考与工具调用为独立专家气泡（与实时 addExpertMessage 一致）
    // 后端把每个 stage 存为 role='thought'+tier；恢复时同 tier 聚合，不散成多条 thinking 气泡
    const expertAgg = {}  // tier -> { thinking: [], tools: [], contents: [] }
    for (const d of msgs) {
      const tier = d.tier || ''
      if ((d.role === 'thought' || (d.role === 'assistant' && tier)) && (tier === 'supervisor' || tier === 'expert')) {
        const text = _stripReplyText(_cleanThinking(d.content || ''))
        const agg = (expertAgg[tier] = expertAgg[tier] || { thinking: [], tools: [], contents: [] })
        if (_isToolTrace(text)) {
          if (!agg.tools.includes(text.slice(0, 200))) agg.tools.push(text.slice(0, 200))
        } else if (text) {
          agg.thinking.push(text)
          agg.contents.push(text)
        }
      }
    }
    // 待挂到大模型回复的思考累积（恢复时把同一轮大模型 thought 聚合到回复框，不散成独立"思考"气泡）
    let pendingLargeThinking = ''
    messages.value = msgs.map(d => {
      const et = d.type || ''
      const tier = d.tier || ''
      // 持久化的心理活动（mental）→ 渲染为心理活动框（与运行时 mental 事件一致）
      if (d.role === 'mental') {
        return {
          _id: uid(),
          kind: 'mental',
          role: 'system',
          content: d.content || '',
          id: d.id || '',
        }
      }
      // 持久化的思考/对话步骤 → 与运行时 addThinkingStep 一致：
      // 工具调用进运行轨迹；supervisor/expert 聚合为独立气泡（上方已处理）；
      // 大模型思考聚合到紧随其后的 assistant 回复的思考区（运行时折叠，不独立成消息）
      if (d.role === 'thought') {
        const thoughtText = _stripReplyText(_cleanThinking(d.content || ''))
        if (_isToolTrace(thoughtText)) {
          traces.value.push({ text: thoughtText.slice(0, 200), time: Date.now() })
          return null
        }
        if (tier === 'supervisor' || tier === 'expert') return null
        if (thoughtText) {
          pendingLargeThinking += (pendingLargeThinking ? '\n\n' : '') + thoughtText
        }
        return null
      }
      let role = (d.role === 'user' || et === 'user_input') ? 'user' : (d.role || 'large')
      if (!d.role) {
        if (tier === 'supervisor') role = 'supervisor'
        else if (tier === 'expert') role = 'expert'
        else if (et === 'thought' || et === 'response') role = 'large'
      }
      const msg = {
        _id: uid(),
        role,
        content: d.content || d.text || '',
        id: d.id || '',
        identity_name: d.metadata?.identity_name || '',
      }
      // 大模型回复：把累积的思考挂到该回复的思考区（折叠显示）
      // 后端 assistant 消息 role='assistant'，无 role 时回退 'large'，两者都算大模型回复
      if ((role === 'large' || role === 'assistant') && pendingLargeThinking) {
        msg._thinking = pendingLargeThinking
        pendingLargeThinking = ''
      }
      return msg
    }).filter(Boolean)
    // 末尾仍有未挂出的大模型思考（无后续回复）→ 丢弃（与运行时 consumeThinking 未消费一致）
    // 追加聚合的 supervisor/expert 专家气泡（内容 = 最后一条输出，思考/工具聚合）
    for (const [tier, agg] of Object.entries(expertAgg)) {
      if (!agg.thinking.length && !agg.tools.length) continue
      messages.value.push({
        _id: uid(),
        role: tier,
        kind: 'expert',
        name: _nameFor(tier),
        avatarCls: _avatarCls(tier),
        content: agg.contents.length ? agg.contents[agg.contents.length - 1] : '',
        _thinking: agg.thinking.join('\n\n'),
        _tools: agg.tools,
        _expanded: false,
        id: '',
      })
    }
    const found = session.sessions.find(x => x.session_id === sid)
    session.currentTitle = found?.title || found?.name || (sid.slice(0, 12) + '...')
    ws.reset()
    streamingIdx.value = -1
    // WS 策略：处理中的会话保持连接（继续监听其 thinking/done，避免切换后收不到完成事件）；
    // 无处理中会话或目标就是处理中会话 → 连接目标会话
    if (_processingSid.value && _processingSid.value !== sid && _processingSid.value === oldSid) {
      // 保持现有连接
    } else {
      _connectedSid.value = sid
      try {
        await Promise.race([ws.wsClient.connect(sid), new Promise(r => setTimeout(r, 8000))])
      } catch {}
    }
  }

  function addMessage(msg) {
    messages.value.push({ _id: uid(), ...msg })
  }

  // ── 思考步骤：累积到当前轮回复的思考区（折叠在回复框内，不独立成消息） ──
  const pendingThinking = ref('')
  function _stripReplyText(raw) {
    // 后端"思考控制/思考结束：{result_summary}"段的 summary 就是最终回复本身，
    // 那不是思考过程——从思考区剔除，避免与正式回复重复
    return String(raw)
      .replace(/思考结束[：:，,]?\s*[\s\S]*$/i, '')
      .replace(/【思考控制】[；;，,\s]*$/i, '')
      .trim()
  }
  function addThinkingStep(d) {
    const text = _stripReplyText(_cleanThinking(d.content))
    if (!text) return
    // 工具调用/委托等中间步骤 → 运行轨迹（不混入对话思考区）
    if (_isToolTrace(text)) {
      traces.value.push({ text: text.slice(0, 200), time: Date.now() })
      // 主管/专家的工具调用 → 额外挂到该身份气泡
      const tier = _tierOf(d)
      if (tier === 'supervisor' || tier === 'expert') addExpertTool(d, text)
      return
    }
    // 主管/专家的推理 → 挂到该身份气泡（不混入总思考区）
    const tier = _tierOf(d)
    if (tier === 'supervisor' || tier === 'expert') {
      addExpertThinking(d)
      return
    }
    // 带身份标注（deepseek 推理来自哪个模型）
    const ident = d.data?.identity_name || d.data?.tier || ''
    const line = (ident ? `【${ident}】` : '') + text
    // 去重：流式增量可能重复推送相同片段
    if (pendingThinking.value.includes(line)) return
    pendingThinking.value += (pendingThinking.value ? '\n\n' : '') + line
  }
  function consumeThinking() {
    const t = pendingThinking.value
    pendingThinking.value = ''
    return t
  }

  function finalizeStream(content) {
    const idx = streamingIdx.value
    if (idx >= 0 && messages.value[idx]) {
      const final = content || messages.value[idx].content
      messages.value[idx] = { ...messages.value[idx], content: final }
    }
    streamingIdx.value = -1
    processing.value = false
    hint.value = ''
    _processingSid.value = null
    runners.value = []
    pendingThinking.value = ''  // 清理残留思考（未合并到回复时丢弃）
  }

  async function _ensureConnected() {
    if (ws.wsClient.connected && _connectedSid.value === session.sessionId) return true
    // WS 连接的不是当前会话（切走后原会话连接残留）→ 重连当前会话。
    // 注意：sendMessage 刚设置 _processingSid = 当前会话，不能清空；
    // 只有旧会话（非当前）的处理中状态才需要在此清除（当前会话发消息意味着旧会话处理已结束）。
    const processingThis = _processingSid.value === session.sessionId
    _connectedSid.value = session.sessionId
    if (!processingThis) _processingSid.value = null
    try {
      await Promise.race([ws.wsClient.connect(session.sessionId), new Promise(r => setTimeout(r, 8000))])
    } catch {}
    return ws.wsClient.connected
  }

  // 发送前确保连接就绪；失败则最多等待 ~8s 重试（对齐 js/pages/chat.js sendMessage）
  async function _sendWithRetry(payload) {
    for (let w = 0; w < 8; w++) {
      if (ws.wsClient.send(payload)) return true
      await new Promise(r => setTimeout(r, 1000))
    }
    return false
  }

  async function sendMessage(content, attachments) {
    if (!session.sessionId) {
      await session.createSession()
    }
    _stopped.value = false
    _lastInput.value = { content, attachments }
    _processingSid.value = session.sessionId
    await _ensureConnected()
    const ok = await _sendWithRetry({ type: 'input', content, model: currentModel.value, attachments })
    if (!ok) {
      processing.value = false
      hint.value = ''
    }
  }

  // 后端 busy 丢弃 input 后重发（会话处理完成后会自动恢复）
  function retryLastInput() {
    if (_stopped.value || !_lastInput.value || !processing.value) return
    if (!ws.wsClient.connected) return
    _sendWithRetry({
      type: 'input',
      content: _lastInput.value.content,
      model: currentModel.value,
      attachments: _lastInput.value.attachments,
    })
  }

  function stop() {
    _stopped.value = true
    _sendWithRetry({ type: 'stop' })
    finalizeStream('')
  }

  function clearMessages() {
    messages.value = []
    processing.value = false
    hint.value = ''
    elapsed.value = 0
    streamingIdx.value = -1
    _stopped.value = false
    _processingSid.value = null
    runners.value = []
  }

  // ── 消息操作（后端同步） ──
  async function deleteMessageAt(idx) {
    const m = messages.value[idx]
    if (!m) return false
    if (m.id) {
      try {
        await endpoints.deleteMessage(session.sessionId, m.id)
      } catch {
        return false
      }
    }
    messages.value.splice(idx, 1)
    return true
  }

  async function editMessageAt(idx, content) {
    const m = messages.value[idx]
    if (!m) return false
    try {
      if (m.id) {
        await endpoints.updateMessage(session.sessionId, m.id, content)
      }
      messages.value[idx] = { ...m, content }
      return true
    } catch {
      return false
    }
  }

  // ── 安全审批（专家要执行有风险命令时） ──
  function addApproval(d) {
    const payload = d.data?.payload || {}
    const sev = d.data?.stage_event || {}
    const requestId = payload.request_id
    if (!requestId) return
    if (messages.value.some(m => m.kind === 'approval' && m.requestId === requestId)) return
    messages.value.push({
      _id: uid(),
      kind: 'approval',
      role: 'system',
      requestId,
      target: sev.target || '',
      detail: payload.detail || '',
      resolved: false,
      approved: null,
    })
  }

  function approve(requestId, approved) {
    _sendWithRetry({ type: 'security_response', request_id: requestId, approved, reason: approved ? '用户批准' : '用户拒绝' })
    const m = messages.value.find(x => x.kind === 'approval' && x.requestId === requestId)
    if (m) { m.resolved = true; m.approved = approved }
  }

  // ── 模型提问（ask_user_intent：需要用户选择/输入） ──
  function addIntent(d) {
    const payload = d.data?.payload || {}
    const requestId = payload.request_id
    if (!requestId) return
    if (messages.value.some(m => m.kind === 'intent' && m.requestId === requestId)) return
    messages.value.push({
      _id: uid(),
      kind: 'intent',
      role: 'system',
      requestId,
      question: payload.question || '',
      options: Array.isArray(payload.options) ? payload.options : [],
      answered: false,
      answer: '',
    })
  }

  function answerIntent(requestId, answer) {
    if (answer === undefined || answer === null || String(answer).trim() === '') return
    _sendWithRetry({ type: 'interactive_response', request_id: requestId, answer: String(answer) })
    const m = messages.value.find(x => x.kind === 'intent' && x.requestId === requestId)
    if (m) { m.answered = true; m.answer = String(answer) }
  }

  return {
    messages, processing, currentModel, streamingIdx, hint, elapsed,
    stopped: _stopped, processingSid: _processingSid, runners, traces,
    init, switchToSession, addMessage, addThinkingStep, addExpertThinking, addExpertTool,
    addExpertMessage, consumeThinking,
    finalizeStream, sendMessage, retryLastInput, stop, clearMessages,
    deleteMessageAt, editMessageAt,
    addApproval, approve, addIntent, answerIntent,
  }
})
