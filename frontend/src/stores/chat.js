import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
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
  const currentModel = ref('large')
  const streamingIdx = ref(-1)
  // 状态提示（对齐 js chatHint）：'' | '思考中...' | '会话正在处理中，请稍候…'
  const hint = ref('')
  const elapsed = ref(0)
  // 用户主动停止后，忽略后端后续补发的 message（避免"按了停止又冒出回复"）
  const _stopped = ref(false)
  const _lastInput = ref(null)
  // 正在处理的会话 id 集合（多会话并行：每个处理中的会话独立连接、独立状态）
  const _processingSids = ref(new Set())
  // 当前 WS 连接的会话 id 集合（多会话并行连接）
  const _connectedSids = ref(new Set())
  // 当前会话是否处理中（由集合派生，切换会话互不干扰）
  const processing = computed(() => _processingSids.value.has(session.sessionId))
  // 当前会话的处理中 id（null 表示当前会话空闲）
  const processingSid = computed(() => processing.value ? session.sessionId : null)
  // 思考循环在线模型状态（由 thinking_progress 解析）：[{model_id, tier, name, status, status_detail, round, max_turns, supervisor, last_thought}]
  const runners = ref([])
  // 过程流：本轮连续思考 + 调度语言（无身份标签），持久化到 DB，会话恢复后重建
  const processFlow = ref([])
  // 过程流面板的模型状态快照（显示"当时"情况，非实时）
  const processSnapshot = ref(null)
  // 运行轨迹：工具调用/委托等中间步骤（借鉴 dsh，从对话流分离）
  const traces = ref([])
  // 主管/专家气泡：按 "tier:identity" 关联其思考过程（_thinking）与工具调用（_tools）
  // 仅按 tier 会导致同 tier 不同身份（identity_name）互相覆盖——后出现者替换前者
  const _expertBubbles = new Map()
  const _pendingByTier = {}
  const _pendingTools = {}
  const _thinkingSeen = new Set()

  function _tierOf(d) {
    return String(d.data?.tier || d.data?.dialog_tier || d.tier || '').toLowerCase()
  }

  // 气泡/缓冲的复合 key：同 tier 内不同身份互不干扰
  function _bubbleKey(tier, ident) {
    return ident ? `${tier}:${ident}` : tier
  }

  function _clearExpertState() {
    _expertBubbles.clear()
    _thinkingSeen.clear()
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
    const key = _bubbleKey(tier, ident)
    const line = (ident ? `【${ident}】` : '') + text
    const bubble = _expertBubbles.get(key)
    if (bubble) {
      const cur = bubble._thinking || ''
      if (!cur.includes(line)) bubble._thinking = cur ? cur + '\n\n' + line : line
    } else {
      const cur = _pendingByTier[key] || ''
      if (!cur.includes(line)) _pendingByTier[key] = cur ? cur + '\n\n' + line : line
    }
  }

  // 主管/专家的工具调用 → 轨迹面板 + 该身份气泡工具列表
  function addExpertTool(d, traceText) {
    const tier = _tierOf(d)
    if (tier !== 'supervisor' && tier !== 'expert') return
    const text = (traceText || _stripReplyText(_cleanThinking(d.content))).slice(0, 200)
    if (text) traces.value.push({ text, time: Date.now() })
    const ident = d.data?.identity_name || ''
    const key = _bubbleKey(tier, ident)
    const bubble = _expertBubbles.get(key)
    if (bubble) {
      const cur = bubble._tools || []
      if (!cur.includes(text)) bubble._tools = [...cur, text]
    } else {
      const cur = _pendingTools[key] || []
      if (!cur.includes(text)) _pendingTools[key] = [...cur, text]
    }
  }

  // 主管/专家的实际输出 → 每次创建新气泡（同身份多次发言不合并）
  // 思考/工具缓冲按身份累积，在每次输出时消费（增量：仅包含自上次输出以来的新内容）
  function addExpertMessage(d) {
    const tier = _tierOf(d)
    if (tier !== 'supervisor' && tier !== 'expert') return
    const ident = d.data?.identity_name || ''
    const key = _bubbleKey(tier, ident)
    const name = ident || (tier === 'supervisor' ? '主管' : '专家')
    const content = d.content || ''
    // 每次输出创建新气泡，思考/工具从缓冲消费（不复用已有气泡）
    const msg = {
      role: tier,
      content,
      kind: 'expert',
      name,
      avatarCls: tier === 'supervisor' ? 'avatar-supervisor' : 'avatar-expert',
      id: '',
      _thinking: _pendingByTier[key] || '',
      _tools: _pendingTools[key] || [],
      _expanded: false,
    }
    _pendingByTier[key] = ''
    _pendingTools[key] = []
    messages.value.push({ _id: uid(), ...msg })
    _expertBubbles.set(key, messages.value[messages.value.length - 1])
  }

  async function init() {
    // 新建会话：断开旧 WS、清空会话 id 与消息（懒创建，首条消息发送时才建新会话）
    try { ws.wsClient.disconnectAll() } catch {}
    session.sessionId = null
    session.currentTitle = '新会话'
    messages.value = []
    hint.value = ''
    elapsed.value = 0
    streamingIdx.value = -1
    _stopped.value = false
    _lastInput.value = null
    _processingSids.value = new Set()
    _connectedSids.value = new Set()
    runners.value = []
    traces.value = []
    processFlow.value = []
    processSnapshot.value = null
    _clearExpertState()
    ws.reset()
  }

  async function switchToSession(sid) {
    session.switchSession(sid)
    messages.value = []
    _clearExpertState()
    // 清空模型状态：避免显示旧会话的思考循环（新会话的 thinking_progress 会重新填充）
    runners.value = []
    // 处理状态按会话隔离（_processingSids 集合），切换会话不重置其他会话的处理状态
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
    // ── 统一重放：持久化消息用同一 classifyThinking 分类（保证与运行时规则唯一） ──
    messages.value = []
    traces.value = []
    processFlow.value = []
    processSnapshot.value = null
    // 若会话中已有 role=process 聚合记录（新格式），则跳过逐条 thought 折叠（避免重复）
    const hasProcessAgg = msgs.some(m => m.role === 'process')
    // 恢复专用的 expert 聚合：每次输出独立气泡，思考增量计算（非累积）
    const expertAgg = {}  // "tier:identity" -> { lastThinking: '', tools: [], contents: [], name: '' }
    let pendingLargeThinking = ''
    for (const d of msgs) {
      const tier = d.tier || ''
      // 心理活动 → 独立心理活动框（与运行时 mental 事件一致）
      if (d.role === 'mental') {
        messages.value.push({
          _id: uid(),
          kind: 'mental',
          role: 'system',
          content: d.content || '',
          id: d.id || '',
        })
        continue
      }
      // 过程流快照（role=process，后端持久化的连续思考+调度+模型状态） → 渲染为过程面板消息
      if (d.role === 'process') {
        messages.value.push({
          _id: uid(),
          kind: 'process',
          role: 'system',
          content: d.content || '',
          runners: d.metadata?.runners || null,
          _processOpen: false,
          id: d.id || '',
        })
        continue
      }
      // 思考/对话步骤 → 用统一 classifyThinking 分类，按类别累积
      if (d.role === 'thought') {
        const evt = { content: d.content || '', data: { tier, dialog_tier: tier } }
        const c = classifyThinking(evt)
        if (c.kind === 'skip' || c.kind === 'approval' || c.kind === 'intent') continue
        const ident = d.identity_name || d.metadata?.identity_name || ''
        const key = ident ? `${c.tier}:${ident}` : c.tier
        if (c.kind === 'tool_trace') {
          if (hasProcessAgg) {
            // 过程流聚合已包含工具/委托步骤 → 不再重复进轨迹或气泡
            continue
          }
          if (c.tier === 'supervisor' || c.tier === 'expert') {
            const agg = (expertAgg[key] = expertAgg[key] || { lastThinking: '', tools: [], contents: [], name: ident })
            if (!agg.tools.includes(c.text)) agg.tools.push(c.text)
          } else {
            traces.value.push({ text: c.text, time: Date.now() })
          }
          continue
        }
        if (c.kind === 'thinking' && hasProcessAgg) {
          continue
        }
        if (c.kind === 'expert') {
          const agg = (expertAgg[key] = expertAgg[key] || { lastThinking: '', tools: [], contents: [], name: ident })
          // 增量思考：当前累积减去上次已显示的 = 本次新增
          const fullThinking = c.text || ''
          const prevLen = agg.lastThinking.length
          const incrThinking = fullThinking.slice(prevLen).trim()
          agg.contents.push(incrThinking || fullThinking)
          agg.lastThinking = fullThinking
          continue
        }
        if (c.kind === 'thinking') {
          if (c.text) pendingLargeThinking += (pendingLargeThinking ? '\n\n' : '') + c.text
          continue
        }
        continue
      }
      // 普通消息（user / assistant 终稿）
      let role = (d.role === 'user') ? 'user' : (d.role || 'large')
      if (!d.role) {
        if (tier === 'supervisor') role = 'supervisor'
        else if (tier === 'expert') role = 'expert'
        else if (tier === 'large') role = 'large'
      }
      const msg = {
        _id: uid(),
        role,
        content: d.content || d.text || '',
        id: d.id || '',
        identity_name: d.metadata?.identity_name || '',
      }
      // 大模型回复：把累积的思考挂到该回复的思考区（与运行时 addMessage thinking 一致）
      if ((role === 'large' || role === 'assistant') && pendingLargeThinking) {
        msg.thinking = pendingLargeThinking
        pendingLargeThinking = ''
      }
      messages.value.push(msg)
    }
    // 追加聚合的 supervisor/expert 专家气泡（仅真正有最终输出内容时；纯思考/工具进过程流）
    for (const [key, agg] of Object.entries(expertAgg)) {
      if (!agg.contents.length) continue
      const tier = key.includes(':') ? key.split(':')[0] : key
      messages.value.push({
        _id: uid(),
        role: tier,
        kind: 'expert',
        name: agg.name || _nameFor(tier),
        avatarCls: _avatarCls(tier),
        content: agg.contents[agg.contents.length - 1] || '',
        _thinking: agg.lastThinking,
        _tools: agg.tools,
        _expanded: false,
      })
    }
    // 末尾仍未挂出的思考 → 丢弃（与运行时 consumeThinking 未消费一致）
    const found = session.sessions.find(x => x.session_id === sid)
    session.currentTitle = found?.title || found?.name || (sid.slice(0, 12) + '...')
    ws.reset()
    streamingIdx.value = -1
    // 多会话并行：始终连接目标会话（处理中的其他会话连接保留，互不干扰）
    _connectedSids.value.add(sid)
    try {
      await Promise.race([ws.wsClient.connect(sid), new Promise(r => setTimeout(r, 8000))])
    } catch {}
  }

  // 测试/内部辅助：标记指定会话为处理中（并行会话集合操作）
  function markProcessing(sid, on = true) {
    if (!sid) return
    if (on) _processingSids.value.add(sid)
    else _processingSids.value.delete(sid)
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

  function _cleanThinkingText(d) {
    // 去掉身份标签（【总指挥】【主管】【专家】等）与 "思考："/"【思考】" 前缀，还原纯思考内容。
    // 保留结构性前缀（【委托】【创建主管】等）以继续匹配工具/委托分类。
    return _stripReplyText(_cleanThinking(String(d?.content || '')))
      .replace(/^【思考】\s*/, '')
      .replace(/^【[^】]*(总指挥|主管|专家|orchestrator|supervisor|expert)[^】]*】\s*/i, '')
      .replace(/^思考[：:]\s*/, '')
      .trim()
  }

  function addThinkingStep(d) {
    const treeRaw = String(d?.content || '')
    const isTrace = _isToolTrace(treeRaw)
    // 工具/委托等中间步骤 → 轨迹 + 过程流
    if (isTrace) {
      const text = treeRaw.slice(0, 200)
      traces.value.push({ text, time: Date.now() })
      addProcessStep(text)
      return
    }
    const text = _cleanThinkingText(d)
    if (!text) return
    // DeepSeek 自带的 reason 字段（data.source==='reasoning'）→ 折叠进最终回复的思考区（保留，仅去标签）
    if (String(d?.data?.source || '').toLowerCase() === 'reasoning') {
      if (_thinkingSeen.has(text)) return
      _thinkingSeen.add(text)
      pendingThinking.value += (pendingThinking.value ? '\n\n' : '') + text
      return
    }
    // 主管/专家的推理 → 过程流（不混入总思考区）
    const tier = _tierOf(d)
    if (tier === 'supervisor' || tier === 'expert') {
      addProcessStep(text)
      return
    }
    // 大模型连续思考/调度语言 → 过程流
    addProcessStep(text)
  }

  // 去重：流式增量可能重复推送相同片段（用 Set 精确匹配，不用 includes 子串）
  function addProcessStep(text) {
    const line = _cleanThinkingText({ content: text })
    if (!line) return
    if (_thinkingSeen.has(line)) return
    _thinkingSeen.add(line)
    processFlow.value.push(line)
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
    // 本轮结束 → 将过程流固化为一条会话消息（持久化，刷新后仍恢复）
    _persistLiveProcess()
    // 仅清理当前会话的处理状态（其他会话的处理状态保留，互不干扰）
    _processingSids.value.delete(session.sessionId)
    hint.value = ''
    if (!processing.value) runners.value = []
    pendingThinking.value = ''  // 清理残留思考（未合并到回复时丢弃）
  }

  // 按会话完成清理（后端 done 事件携带 session_id，切走后仍可精确清理对应会话）
  function finalizeSession(sid) {
    if (!sid) return
    _processingSids.value.delete(sid)
    if (sid === session.sessionId) {
      _persistLiveProcess()
      streamingIdx.value = -1
      hint.value = ''
      runners.value = []
      pendingThinking.value = ''
    }
  }

  // 将本轮累积的过程流固化为消息（有内容或快照才固化）；固化后清空待发缓冲
  function _persistLiveProcess() {
    const flow = processFlow.value || []
    const snap = processSnapshot.value
    if (!flow.length && !snap) return
    messages.value.push({
      _id: uid(),
      kind: 'process',
      role: 'system',
      content: flow.join('\n\n'),
      runners: snap,
      _processOpen: false,
    })
    processFlow.value = []
    processSnapshot.value = null
    _thinkingSeen.clear()
  }

  async function _ensureConnected() {
    if (ws.wsClient.isConnected(session.sessionId)) return true
    // 多会话并行：确保当前会话有独立连接（其他会话连接保留）
    _connectedSids.value.add(session.sessionId)
    try {
      await Promise.race([ws.wsClient.connect(session.sessionId), new Promise(r => setTimeout(r, 8000))])
    } catch {}
    return ws.wsClient.isConnected(session.sessionId)
  }

  // 发送前确保连接就绪；失败则最多等待 ~8s 重试（对齐 js/pages/chat.js sendMessage）
  async function _sendWithRetry(payload) {
    for (let w = 0; w < 8; w++) {
      if (ws.wsClient.send(session.sessionId, payload)) return true
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
    _processingSids.value.add(session.sessionId)
    await _ensureConnected()
    const ok = await _sendWithRetry({ type: 'input', content, model: currentModel.value, attachments })
    if (!ok) {
      _processingSids.value.delete(session.sessionId)
      hint.value = ''
    }
  }

  // 后端 busy 丢弃 input 后重发（会话处理完成后会自动恢复）
  function retryLastInput() {
    if (_stopped.value || !_lastInput.value || !processing.value) return
    if (!ws.wsClient.isConnected(session.sessionId)) return
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
    _processingSids.value.delete(session.sessionId)
    finalizeStream('')
  }

  function clearMessages() {
    messages.value = []
    hint.value = ''
    elapsed.value = 0
    streamingIdx.value = -1
    _stopped.value = false
    _processingSids.value.delete(session.sessionId)
    runners.value = []
    processFlow.value = []
    processSnapshot.value = null
  }

  function setProcessSnapshot(snap) {
    processSnapshot.value = snap || null
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
    const wasAiFinal = ['assistant', 'large', 'supervisor', 'expert'].includes(String(m.role || '').toLowerCase())
      || (m.kind === 'process')
    messages.value.splice(idx, 1)
    // 删除 AI 最终回复时，联动移除其上方紧邻的过程流面板（与后端同轮思考清理一致）
    if (wasAiFinal) {
      const prev = messages.value[idx - 1]
      if (prev && prev.kind === 'process') messages.value.splice(idx - 1, 1)
    }
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

  // ── 统一思考事件分类（运行时 WS 与 loadHistory 恢复共用，保证展示规则唯一） ──
  // 返回: { kind: 'approval'|'intent'|'tool_trace'|'thinking'|'expert'|'skip', tier, text }
  function classifyThinking(d) {
    const tier = _tierOf(d)
    // 广播条目类型（thought/response/thinking）：response=最终回复气泡；其余=过程流
    const entryType = String(d.data?.entry_type || '').toLowerCase()
    // 安全审查/审批/提问 → 跳过（瞬态交互，不重建横幅、不折叠进思考区）
    if (tier === 'security') return { kind: 'skip', tier }
    // 安全审批 / 模型提问（实时才有 request_id；恢复时后端标 tier='security' 已在上方跳过）
    const sev = d.data?.stage_event
    if (sev?.event_type === 'security' && d.data?.payload?.request_id) {
      const action = String(sev.action || '')
      if (action.indexOf('等待用户审批') >= 0) return { kind: 'approval', tier, d }
      if (action.indexOf('user_intent_request') >= 0) return { kind: 'intent', tier, d }
    }
    const text = _stripReplyText(_cleanThinking(d.content || ''))
    // 工具/委托等中间步骤 → 运行轨迹
    if (_isToolTrace(text)) return { kind: 'tool_trace', tier, text: text.slice(0, 200) }
    // supervisor/expert：entry_type='response' → 最终回复气泡；其余 → 过程流
    if (tier === 'supervisor' || tier === 'expert') {
      if (entryType === 'response') return { kind: 'expert', tier, text, entryType: 'response' }
      return { kind: 'thinking', tier, text, entryType: entryType || 'process' }
    }
    // 大模型思考 → 折叠到回复
    if (tier === 'thinking' || tier === 'large' || tier === '') return { kind: 'thinking', tier, text }
    return { kind: 'skip', tier }
  }

  // ── 统一思考事件分派（运行时 WS 事件用） ──
  // 返回事件类型：'approval' | 'intent' | 'thinking' | 'expert' | 'tool_trace' | ''（未处理/跳过）
  function dispatchThinking(d) {
    const c = classifyThinking(d)
    switch (c.kind) {
      case 'approval': addApproval(d); return 'approval'
      case 'intent': addIntent(d); return 'intent'
      case 'tool_trace':
        addProcessStep(c.text)
        traces.value.push({ text: c.text, time: Date.now() })
        return 'tool_trace'
      case 'expert': {
        // 仅 entry_type='response' 的最终回复创建气泡；过程推理走过程流
        if (c.entryType === 'response') addExpertMessage(d)
        return 'expert'
      }
      case 'thinking':
        addThinkingStep(d)
        return 'thinking'
      default: return ''
    }
  }

  return {
    messages, processing, currentModel, streamingIdx, hint, elapsed,
    stopped: _stopped, processingSid, runners, traces, processFlow, processSnapshot,
    init, switchToSession, addMessage, addThinkingStep, addProcessStep, addExpertThinking, addExpertTool,
    addExpertMessage, consumeThinking, dispatchThinking, classifyThinking,
    finalizeStream, finalizeSession, sendMessage, retryLastInput, stop, clearMessages,
    setProcessSnapshot,
    deleteMessageAt, editMessageAt,
    addApproval, approve, addIntent, answerIntent,
    markProcessing,
  }
})
