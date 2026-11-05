import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useSessionStore } from './session.js'
import { useWsStore } from '@/ws/store.js'
import { endpoints } from '@/api.js'
import { useI18n } from '@/i18n/init.js'

let _uid = 0
function uid() { return 'm' + (++_uid) }
let _traceSeq = 0
let _streamSeq = 0

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

// 事件时间（秒）统一解析：数字直接可用；字符串兼容 ISO / 本地时间（NAIVE UTC → 补 Z）。
// `chat_messages.created_at` 与 `session_events.created_at` 同存 naive UTC，可直接比较。
function _tsSec(v) {
  if (typeof v === 'number' && isFinite(v)) return v
  const s = String(v || '').trim()
  if (!s) return 0
  // JS 只认 3 位小数秒：毫秒之后的部分截断
  const norm = s.replace(/(\.\d{3})\d+/, '$1')
  const iso = /([zZ]|[+-]\d{2}:?\d{2})$/.test(norm) ? norm : norm + 'Z'
  const ms = Date.parse(iso)
  return isFinite(ms) ? ms / 1000 : 0
}

function _avatarCls(role) {
  const r = String(role || '').toLowerCase()
  if (r === 'supervisor') return 'avatar-supervisor'
  if (r === 'expert') return 'avatar-expert'
  if (r === 'user') return 'avatar-user'
  return 'avatar-large'
}

export const useChatStore = defineStore('chat', () => {
  const t = useI18n().global.t
  const session = useSessionStore()
  const ws = useWsStore()

  // 角色显示名（随语言设置变化）
  function roleName(role) {
    const r = String(role || '').toLowerCase()
    if (r === 'user') return t('chat.roleUser')
    if (r === 'supervisor') return t('chat.roleSupervisor')
    if (r === 'expert') return t('chat.roleExpert')
    return t('chat.roleOrchestrator')
  }

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
  // ── 会话投影（Phase 4） ──
  // 服务端把派生状态（todos/runners/tokenUsage/contextPressure）作为整值快照推来，
  // 每帧带 seq（与事件日志同一计数器）：前端按 key 覆盖，seq 不大于已应用值就丢弃。
  // 这样重连补发/回放读到的旧快照不会盖掉更新的状态。
  const projections = ref({})      // key → value（最近一次应用的快照）
  const todos = ref([])            // todos 投影
  const tokenUsage = ref(null)     // tokenUsage 投影 {prompt, completion, total, by_tier}
  const contextTokens = ref(0)     // contextPressure 投影（当前上下文占用 token）
  const contextWindowSize = ref(0) // contextPressure 投影（上下文窗口大小）
  const _projSeq = {}              // key → 已应用的最大 seq（非响应式）
  // ── 可应答请求（Phase 4.b） ──
  // 待审批/待回答的整快照（服务端 `session_request` 帧）：刷新/重连后据此重建横幅，
  // 用户在横幅里作答即可继续（以前刷新后横幅直接消失，回合永久挂住）。
  const pendingRequests = ref([])
  const hasPendingRequest = computed(() => pendingRequests.value.length > 0)
  // 过程流：本轮连续思考 + 调度语言（无身份标签），持久化到 DB，会话恢复后重建
  const processFlow = ref([])
  // 过程流面板的模型状态快照（显示"当时"情况，非实时）
  const processSnapshot = ref(null)
  // DSH 式统一轨迹时间线（本轮实时）：技能/思考/工具/完成 按时间排序串成一条线
  const trajectory = ref([])
  // 事件日志回放标记：回放期间不往实时轨迹里塞条目（避免把历史塞进当前轮）
  let _replaying = false
  // 运行轨迹：工具调用/委托等中间步骤（借鉴 dsh，从对话流分离）
  const traces = ref([])
  // 流式正文尾巴（Phase 3）：`chunk` 帧边收边显示，最终 `message` 到达时替换
  const streamTail = ref(null)
  const _thinkingSeen = new Set()

  function _tierOf(d) {
    return String(d.data?.tier || d.data?.dialog_tier || d.tier || '').toLowerCase()
  }

  // 主管/专家的实际输出 → 每次创建新气泡（同身份多次发言不合并）
  // 该身份的推理/工具轨迹统一进过程流（见 §101），气泡只承载最终回复
  function addExpertMessage(d) {
    const tier = _tierOf(d)
    if (tier !== 'supervisor' && tier !== 'expert') return
    const ident = d.data?.identity_name || ''
    messages.value.push({
      _id: uid(),
      role: tier,
      kind: 'expert',
      name: ident || roleName(tier),
      avatarCls: tier === 'supervisor' ? 'avatar-supervisor' : 'avatar-expert',
      content: d.content || '',
      id: '',
    })
  }

  async function init() {
    // 新建会话：断开旧 WS、清空会话 id 与消息（懒创建，首条消息发送时才建新会话）
    try { ws.wsClient.disconnectAll() } catch {}
    session.sessionId = null
    session.currentTitle = t('chat.newSession')
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
    streamTail.value = null
    processFlow.value = []
    processSnapshot.value = null
    trajectory.value = []
    resetProjections()
    resetPending()
    _thinkingSeen.clear()
    ws.reset()
  }

  async function switchToSession(sid) {
    session.switchSession(sid)
    messages.value = []
    _thinkingSeen.clear()
    // 清空模型状态：避免显示旧会话的思考循环（新会话的 thinking_progress 会重新填充）
    runners.value = []
    resetProjections()
    resetPending()
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
    streamTail.value = null
    processFlow.value = []
    processSnapshot.value = null
    trajectory.value = []
    // 若会话中已有 role=process 聚合记录（新格式），则跳过逐条 thought 折叠（避免重复）
    const hasProcessAgg = msgs.some(m => m.role === 'process')
    // 用户消息 = 回放定位锚点（老会话没有 user_message 事件时按时间回退）
    const userAnchors = []  // [{ key, ts }]
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
      // 思考/对话步骤 → 用统一 classifyThinking 分类（与运行时同一套规则）
      if (d.role === 'thought') {
        const ident = d.identity_name || d.metadata?.identity_name || ''
        const evt = {
          content: d.content || '',
          data: {
            tier,
            dialog_tier: tier,
            identity_name: ident,
            // 必须还原 entry_type：否则主管/专家最终回复会被当成过程推理吞掉（§91/§101）
            entry_type: d.entry_type || d.metadata?.entry_type || '',
          },
        }
        const c = classifyThinking(evt)
        if (c.kind === 'skip' || c.kind === 'approval' || c.kind === 'intent') continue
        if (c.kind === 'tool_trace') {
          // 过程流聚合已包含工具/委托步骤 → 不再重复；否则与运行时 dispatchThinking 同款进过程流
          if (!hasProcessAgg) addProcessStep(c.text)
          continue
        }
        if (c.kind === 'expert') {
          // 每次输出独立气泡（同身份多次输出不合并）；推理/工具轨迹统一在过程流
          messages.value.push({
            _id: uid(),
            role: c.tier,
            kind: 'expert',
            name: ident || roleName(c.tier),
            avatarCls: _avatarCls(c.tier),
            content: c.text,
            id: d.id || '',
          })
          continue
        }
        if (c.kind === 'thinking') {
          if (c.tier === 'supervisor' || c.tier === 'expert') {
            // 主管/专家推理 = 过程内容 → 过程流（运行时 addThinkingStep 同款，不挂气泡思考区）
            if (!hasProcessAgg) addProcessStep(c.text)
            continue
          }
          if (hasProcessAgg) continue
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
      if (role === 'user') userAnchors.push({ key: msg.id || msg._id, ts: _tsSec(d.created_at) })
      // 大模型回复：把累积的思考挂到该回复的思考区（与运行时 addMessage thinking 一致）
      if ((role === 'large' || role === 'assistant') && pendingLargeThinking) {
        msg.thinking = pendingLargeThinking
        pendingLargeThinking = ''
      }
      messages.value.push(msg)
    }
    // 末尾仍未挂出的思考 → 丢弃（与运行时 consumeThinking 未消费一致）
    const found = session.sessions.find(x => x.session_id === sid)
    session.currentTitle = found?.title || found?.name || (sid.slice(0, 12) + '...')
    // 工具轨迹从事件日志回放（与运行时同一套 addToolCall/addToolResult）
    await replayToolEvents(sid, 500, userAnchors)
    // 派生状态从投影读回（todos/上下文占用/token 用量）；实时帧到达后按 seq 覆盖
    await loadProjections(sid)
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
    // 工具/委托等中间步骤 → 过程流（结构化轨迹由 addToolCall/addToolResult 提供）
    if (isTrace) {
      addProcessStep(treeRaw.slice(0, 200))
      return
    }
    const text = _cleanThinkingText(d)
    if (!text) return
    // DeepSeek 自带的 reason 字段（data.source==='reasoning'）→ 折叠进最终回复的思考区（保留，仅去标签）
    if (String(d?.data?.source || '').toLowerCase() === 'reasoning') {
      if (_thinkingSeen.has(text)) return
      _thinkingSeen.add(text)
      pendingThinking.value += (pendingThinking.value ? '\n\n' : '') + text
      // 同时作为时间线的一步（DSH 式：思考推理也纳入轨迹），与 agent 模式一致
      _pushTrajectory({ ts: Date.now(), kind: 'thinking', title: text.slice(0, 200) })
      return
    }
    // 主管/专家的推理 → 过程流（不混入总思考区）
    const tier = _tierOf(d)
    if (tier === 'supervisor' || tier === 'expert') {
      addProcessStep(text)
      _pushTrajectory({ ts: Date.now(), kind: 'thinking', title: text.slice(0, 200) })
      return
    }
    // 大模型连续思考/调度语言 → 过程流
    addProcessStep(text)
    _pushTrajectory({ ts: Date.now(), kind: 'thinking', title: text.slice(0, 200) })
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

  function clearStream() {
    streamTail.value = null
  }

  // 流式正文增量：同一次生成（turn 相同）累加到同一个尾巴；换轮/新任务则重置
  function appendStreamChunk(d) {
    const delta = d?.content || ''
    if (!delta) return
    const tier = _tierOf(d)
    const turn = Number(d?.data?.turn ?? 0)
    const tail = streamTail.value
    if (!tail || (turn && tail.turn !== turn)) {
      streamTail.value = {
        _id: 'sk' + (++_streamSeq),
        role: 'assistant',
        content: '',
        tier: tier || 'large',
        identityName: d?.data?.identity_name || '',
        turn,
      }
    }
    streamTail.value.content += delta
    if (!streamTail.value.identityName && d?.data?.identity_name) {
      streamTail.value.identityName = d.data.identity_name
    }
  }

  function finalizeStream(content) {
    clearStream()
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
      clearStream()
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
    const traj = trajectory.value || []
    if (!flow.length && !snap && !traj.length) return
    messages.value.push({
      _id: uid(),
      kind: 'process',
      role: 'system',
      content: flow.join('\n\n'),
      runners: snap,
      trajectory: traj,
      _processOpen: false,
    })
    processFlow.value = []
    processSnapshot.value = null
    trajectory.value = []
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

  async function sendMessage(content, attachments, webSearch = false) {
    if (!session.sessionId) {
      await session.createSession()
    }
    _stopped.value = false
    _lastInput.value = { content, attachments, webSearch }
    trajectory.value = []
    _processingSids.value.add(session.sessionId)
    await _ensureConnected()
    const ok = await _sendWithRetry({ type: 'input', content, model: currentModel.value, attachments, web_search: !!webSearch })
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
      web_search: !!_lastInput.value.webSearch,
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
    traces.value = []
    streamTail.value = null
    processFlow.value = []
    processSnapshot.value = null
    trajectory.value = []
    // 后端清空对话时同步删除投影与待办（chat_gateway.clear_session_messages）→ 本地也清掉
    resetProjections()
    resetPending()
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

  // ── 工具轨迹（结构化事件；Phase 0 由 model_runner 推送） ──
  // 一次调用 = 一条记录：call 建“运行中”行，result 原地补齐状态/耗时/预览，
  // 不再靠 _isToolTrace 正则从中文摘要里猜工具名。

  // 轨迹锚点：记下调用发生时“最后一条消息”，前端据此把工具卡片插进对话流
  // （Phase 3 交错）；历史回放拿不到可靠位置，置空 → 落回轨迹面板。
  function _lastMessageKey() {
    const list = messages.value || []
    for (let i = list.length - 1; i >= 0; i--) {
      const m = list[i]
      if (!m) continue
      const key = m.id || m._id
      if (key) return key
    }
    return ''
  }

  // ── DSH 式统一轨迹时间线 ──
  // 把 技能/思考/工具/完成 这些不同来源的步骤，按时间顺序串成一条可渲染的时间线。
  // 回放期间（_replaying）不写入，避免历史事件污染当前轮的实时轨迹。
  function _pushTrajectory(entry) {
    if (_replaying) return
    trajectory.value.push(entry)
  }

  // 工具轨迹 → 时间线条目种类（与图标/标签映射一致）
  function _toolKind(item) {
    const card = item.view && item.view.card
    if (card === 'terminal') return 'command'
    if (card === 'read') return 'read'
    if (card === 'diff') return 'diff'
    if (card === 'search') return 'search'
    if (card === 'web') return 'web'
    return 'tool'
  }
  function _toolTitle(item) {
    const v = item.view
    if (v && v.title) return v.title
    return item.name || 'tool'
  }

  function addSkillStep(d) {
    const p = d.data || {}
    const name = p.name || p.skill_id || ''
    const unload = d.event === 'skill_unloaded'
    _pushTrajectory({
      ts: Date.now(),
      kind: 'skill',
      title: (unload ? '停用技能 ' : '加载技能 ') + name,
      phase: p.phase || (unload ? 'done' : 'start'),
      skillId: p.skill_id || '',
    })
  }

  // 心理活动（内心独白）：作为"思考"步骤纳入时间线（chat_light / agent 均有）
  function addMentalStep(d) {
    const text = String(d?.content || '').trim()
    if (!text) return
    _pushTrajectory({ ts: Date.now(), kind: 'thinking', title: text.slice(0, 200) })
  }

  function markTurnDone() {
    if (_replaying) return
    const last = trajectory.value[trajectory.value.length - 1]
    if (last && last.kind === 'done') return
    _pushTrajectory({ ts: Date.now(), kind: 'done', title: '已完成' })
  }

  function addToolCall(d) {
    const p = d.data || {}
    // 同一 call_id 只建一行（重连补发/历史回放可能与实时事件重叠）
    if (p.call_id) {
      const existing = traces.value.find(it => it.kind === 'tool' && it.callId === p.call_id)
      if (existing) {
        if (p.args && Object.keys(p.args).length && !Object.keys(existing.args || {}).length) {
          existing.args = p.args
        }
        return existing
      }
    }
    const item = {
      _id: 'tr' + (++_traceSeq),
      kind: 'tool',
      callId: p.call_id || '',
      name: p.name || 'tool',
      args: p.args || {},
      status: p.status || 'running',
      durationMs: 0,
      preview: '',
      error: '',
      resultChars: 0,
      // 卡片渲染意图（服务端 tool_view 计算，实时与回放同源）
      view: p.view || null,
      tier: p.tier || '',
      identityName: p.identity_name || '',
      // 插入位置：最后一条消息的 id（回放传空字符串 → 走轨迹面板）
      afterKey: p.afterKey !== undefined ? p.afterKey : _lastMessageKey(),
      time: Date.now(),
    }
    traces.value.push(item)
    _pushTrajectory({
      ts: Date.now(), kind: _toolKind(item), title: _toolTitle(item),
      status: item.status, callId: item.callId, durationMs: 0,
      view: item.view, identityName: item.identityName,
    })
    return item
  }

  function addToolResult(d) {
    const p = d.data || {}
    // 找最近一条同 callId 的运行中记录（缺 callId 时退回最后一条未完成）
    let item = null
    for (let i = traces.value.length - 1; i >= 0; i--) {
      const it = traces.value[i]
      if (it.kind !== 'tool') continue
      const same = p.call_id ? it.callId === p.call_id : it.status === 'running'
      if (same) { item = it; break }
    }
    if (!item) {
      // 漏收 call（如运行期断线）→ 用 result 补一条已落定的记录
      item = addToolCall({ data: { ...p, status: p.status || 'ok' } })
    }
    item.status = p.status || 'ok'
    item.durationMs = p.duration_ms || 0
    item.preview = p.result_preview || ''
    item.error = p.error || ''
    item.resultChars = p.result_chars || 0
    // 结果卡片覆盖调用骨架（无 view 时清空 → 前端回退 generic）
    if (p.view) item.view = p.view
    else if (p.status && p.status !== 'running') item.view = null
    // 时间线里对应的工具条目同步状态/详情
    if (!_replaying) {
      const te = trajectory.value.find(e => e.callId === item.callId)
      if (te) {
        te.status = item.status
        te.durationMs = item.durationMs
        te.view = item.view
        te.preview = item.preview
        te.error = item.error
      }
    }
    return item
  }

  // ── 事件日志回放（Phase 1/3） ──
  // 刷新/重开会话时按 seq 读取持久事件，用与实时相同的方法重建工具轨迹。
  // 正文气泡与过程流仍由 chat_messages 重建；事件表只用来补工具轨迹与“位置”。
  // 按 seq 分页读取（长会话事件远超一页；最多 10 页 = 5000 条，避免卡死首屏）
  async function replayToolEvents(sid, pageSize = 500, userAnchors = []) {
    _replaying = true
    try {
    let after = 0
    // 当前轮用户消息 → 本轮的工具卡片插在它后面（对应实时侧的 _lastMessageKey）
    let anchor = ''
    // 事件日志是否属于新格式（出现过 user_message 事件）。决定能不能用助手终稿
    // 事件推进锚点：老会话没有用户消息事件，一推进就会把工具挂到上一轮回复下面。
    let sawUserEvent = false
    // 本次重建出的消息 id 集合：终稿事件只认确定存在的那条
    const knownIds = new Set()
    for (const m of messages.value || []) {
      if (m?.id) knownIds.add(m.id)
      if (m?._id) knownIds.add(m._id)
    }
    // 老会话（本次改动之前）没有 user_message 事件：按时间回退到“此前最近一条
    // 用户消息”。两侧时间同为 UTC，容忍 1s 内的同秒抖动；仍找不到就留空（折叠面板）。
    const byTime = (ev) => {
      const ts = _tsSec(ev.timestamp)
      if (!ts || !userAnchors.length) return ''
      let key = ''
      for (const a of userAnchors) {
        if (a.ts && a.ts <= ts + 1) key = a.key
      }
      return key
    }
    for (let page = 0; page < 10; page++) {
      let events = []
      try {
        const r = await endpoints.sessionEvents(sid, after, pageSize)
        events = (r.data?.events) || []
      } catch {
        return
      }
      if (!events.length) return
      for (const ev of events) {
        // 用户消息开新一轮：气泡本身由 chat_messages 重建，这里只推进锚点。
        if (ev.type === 'user_message') {
          sawUserEvent = true
          anchor = ev.data?.message_id || anchor
          continue
        }
        // 助手终稿也推进锚点，同一轮内才是“说一句 → 调工具 → 接着说”的交错。
        // 仅限新格式会话（老会话没 user_message 事件，推进会全错一轮），
        // 且只认确定落库的那条消息（终稿未落库/被截断时保留上一锚点，
        // 宁可不定位（落折叠面板）也不错位）。
        if (ev.type === 'message' && sawUserEvent) {
          const mid = ev.data?.message_id || ''
          if (mid && knownIds.has(mid)) anchor = mid
          continue
        }
        if (ev.type !== 'tool') continue
        const ev2 = { ...ev, data: { ...(ev.data || {}), afterKey: anchor || byTime(ev) } }
        if (ev.event === 'tool_call') addToolCall(ev2)
        else if (ev.event === 'tool_result') addToolResult(ev2)
      }
      const lastSeq = events[events.length - 1]._seq || 0
      if (events.length < pageSize || !lastSeq || lastSeq <= after) return
      after = lastSeq
    }
    } finally {
      _replaying = false
    }
  }

  // ── 会话投影的应用与恢复（Phase 4） ──

  // 模型状态快照 → 扁平列表：与 status/thinking_progress 帧同一套形状，
  // 实时帧与投影恢复共用同一函数，避免两处各写一份层级映射。
  function _flattenRunners(data) {
    const list = []
    const add = (r, tier, sup) => { if (r && r.model_id) list.push({ ...r, tier, supervisor: sup || '' }) }
    if (data?.large_model) add(data.large_model, 'large', '')
    ;(data?.active_supervisors || []).forEach(s => add(s, 'supervisor', ''))
    ;(data?.active_experts || []).forEach(e => add(e, 'expert', e?.supervisor || ''))
    return list
  }

  // 应用实时模型状态（无 seq：最新即真理）。上下文数只在有值时覆盖——
  // 心跳里的 0 不代表“上下文清空了”，直接盖会让上下文条闪一下。
  function applyRunnerSnapshot(data) {
    if (!data || typeof data !== 'object') return
    runners.value = _flattenRunners(data)
    if (data.context_tokens) contextTokens.value = data.context_tokens
    if (data.context_window_size) contextWindowSize.value = data.context_window_size
  }

  function _applyProjectionValue(key, value) {
    if (key === 'runners') { applyRunnerSnapshot(value); return }
    if (key === 'todos') { todos.value = Array.isArray(value?.todos) ? value.todos : []; return }
    if (key === 'tokenUsage') { tokenUsage.value = (value && typeof value === 'object') ? value : null; return }
    if (key === 'contextPressure') {
      // 投影是权威快照：0 也要应用（与实时心跳的“保持非零”不同）
      const tokens = Number(value?.tokens)
      const window = Number(value?.window)
      if (Number.isFinite(tokens) && tokens >= 0) contextTokens.value = tokens
      if (Number.isFinite(window) && window >= 0) contextWindowSize.value = window
    }
  }

  // 应用一帧投影；返回是否真的应用（旧 seq → false，便于测试与调试）
  function applyProjection(frame) {
    const key = String(frame?.key || '')
    if (!key) return false
    const seq = Number(frame?.seq || 0)
    const prev = _projSeq[key] || 0
    if (seq > 0 && seq <= prev) return false
    if (seq > 0) _projSeq[key] = seq
    projections.value = { ...projections.value, [key]: frame.value }
    _applyProjectionValue(key, frame.value)
    return true
  }

  function resetProjections() {
    Object.keys(_projSeq).forEach(k => { delete _projSeq[k] })
    projections.value = {}
    todos.value = []
    tokenUsage.value = null
    contextTokens.value = 0
    contextWindowSize.value = 0
  }

  // 切换/重开会话时读回当前快照（失败静默：投影只是加速恢复，不影响主流程）
  async function loadProjections(sid) {
    if (!sid) return
    let list = []
    try {
      const r = await endpoints.sessionProjections(sid)
      list = r?.data?.projections || []
    } catch { return }
    for (const p of list) applyProjection(p)
  }

  // ── 可应答请求的应用（Phase 4.b） ──
  // 快照是权威的：有的要求横幅存在，没有的说明服务端已结束（响应/自动拒绝/断线清理）
  // → 横幅置为失效，避免留下按钮却点了没反应。
  function applyPendingRequests(list) {
    const reqs = (Array.isArray(list) ? list : []).filter(r => r && r.request_id)
    pendingRequests.value = reqs
    const ids = new Set(reqs.map(r => r.request_id))
    for (const r of reqs) {
      if (String(r.kind) === 'question') addIntent({ data: { payload: r } })
      else addApproval({ data: { payload: r, stage_event: { target: r.target } } })
    }
    for (const m of messages.value) {
      if (m.kind !== 'approval' && m.kind !== 'intent') continue
      if (!m.requestId || ids.has(m.requestId)) continue
      if (m.kind === 'approval') {
        if (!m.resolved) { m.resolved = true; m.approved = null; m.stale = true }
      } else if (!m.answered) { m.answered = true; m.stale = true }
    }
  }

  function resetPending() {
    pendingRequests.value = []
  }

  // ── 统一思考事件分类（运行时 WS 与 loadHistory 恢复共用，保证展示规则唯一） ──
  // 返回: { kind: 'approval'|'intent'|'tool_trace'|'thinking'|'expert'|'skip', tier, text }
  function classifyThinking(d) {
    const tier = _tierOf(d)
    // 广播条目类型（thought/response/thinking）：response=最终回复气泡；其余=过程流
    const entryType = String(d.data?.entry_type || '').toLowerCase()
    // 流式正文增量：已由 chunk 帧渲染进流式气泡，不再重复写过程流
    if (entryType === 'streaming_delta' && (tier === 'large' || tier === '')) return { kind: 'skip', tier }
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
        // 旧文本摘要只进过程流；结构化轨迹由 tool 事件（addToolCall/Result）负责
        addProcessStep(c.text)
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
    stopped: _stopped, processingSid, runners, traces, streamTail, processFlow, processSnapshot, trajectory,
    projections, todos, tokenUsage, contextTokens, contextWindowSize,
    applyProjection, applyRunnerSnapshot, loadProjections, resetProjections,
    pendingRequests, hasPendingRequest, applyPendingRequests, resetPending,
    init, switchToSession, addMessage, addThinkingStep, addProcessStep,
    addExpertMessage, addToolCall, addToolResult, replayToolEvents, consumeThinking, dispatchThinking, classifyThinking,
    addSkillStep, addMentalStep, markTurnDone,
    appendStreamChunk, clearStream,
    finalizeStream, finalizeSession, sendMessage, retryLastInput, stop, clearMessages,
    setProcessSnapshot,
    deleteMessageAt, editMessageAt,
    addApproval, approve, addIntent, answerIntent,
    markProcessing,
  }
})
