<script setup>
defineOptions({ name: 'Chat' })
import { ref, computed, onMounted, onActivated, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { useChatStore } from '@/stores/chat.js'
import { useSessionStore } from '@/stores/session.js'
import { useToastStore } from '@/stores/toast.js'
import { useWsStore } from '@/ws/store.js'
import { endpoints } from '@/api.js'
import { useConfirm, usePrompt } from '@/composables/useDialog.js'
import ChatMessage from '@/components/ChatMessage.vue'
import ChatInput from '@/components/ChatInput.vue'
import SessionList from '@/components/SessionList.vue'
import ModelSelector from '@/components/ModelSelector.vue'
import ThinkingIndicator from '@/components/ThinkingIndicator.vue'
import ThinkingStatusPanel from '@/components/ThinkingStatusPanel.vue'
import ProcessPanel from '@/components/ProcessPanel.vue'
import SessionSettings from '@/components/SessionSettings.vue'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'

const route = useRoute()
const chat = useChatStore()
const session = useSessionStore()
const ws = useWsStore()
const toast = useToastStore()
const confirm = useConfirm()
const prompt = usePrompt()
const { t } = useI18n()
const sessionListCollapsed = ref(false)
const showSettings = ref(false)
const todos = ref([])
const showTodos = ref(false)
const tracesOpen = ref(false)
const todoDone = computed(() => todos.value.filter((t) => t.status === 'completed').length)
// 上下文窗口占用（估算 token，由 thinking_progress 状态事件填充）
const contextTokens = ref(0)
const contextWindowSize = ref(0)

// 当前激活技能：从 thinking_progress 的 large 模型 runner 提取（后端 active_skill 字段）
const activeSkill = computed(() => {
  const large = chat.runners.find((r) => r.tier === 'large')
  return large?.active_skill || ''
})
async function loadTodos() {
  try {
    const sid = session.sessionId || ''
    const r = await fetch('/api/management/todos?session_id=' + encodeURIComponent(sid), { headers: { Accept: 'application/json' } })
    const d = await r.json()
    todos.value = d?.data?.todos || []
  } catch {}
}
async function toggleTodo(t) {
  const next = t.status === 'completed' ? 'pending' : 'completed'
  try {
    const sid = session.sessionId || ''
    const r = await fetch('/api/management/todos/' + encodeURIComponent(t.id) + '/status?session_id=' + encodeURIComponent(sid), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: next }),
    })
    const d = await r.json()
    if (d.success) t.status = next
    else toast.show(t('chat.updateFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch { toast.show(t('chat.updateFailed'), 'error') }
}
const messagesWrap = ref(null)
let watchTimer = null

// ── 分批渲染：最多同时渲染 RENDER_LIMIT 条，超出时顶部可加载更早 ──
const RENDER_LIMIT = 50
const RENDER_STEP = 50
const renderLimit = ref(RENDER_LIMIT)

const visibleMessages = computed(() =>
  chat.messages.slice(Math.max(0, chat.messages.length - renderLimit.value))
)
const msgOffset = computed(() => chat.messages.length - visibleMessages.value.length)
const hasMoreMessages = computed(() => chat.messages.length > visibleMessages.value.length)

// 正在等待 AI 回复：最后一条消息是 user（或消息为空）→ 在用户消息下方显示“正在思考”
const showThinkingWaiting = computed(() =>
  chat.processing &&
  (chat.messages.length === 0 || chat.messages[chat.messages.length - 1].role === 'user')
)

function loadMoreMessages() {
  if (loadingOlder.value) return
  loadingOlder.value = true
  const el = messagesWrap.value
  const prevHeight = el ? el.scrollHeight : 0
  renderLimit.value += RENDER_STEP
  nextTick(() => {
    if (el) el.scrollTop = el.scrollHeight - prevHeight
    loadingOlder.value = false
  })
}

// ── 滚动状态：底部悬浮按钮 + 上滑到顶部附近自动加载更早消息（分批渲染，不一次性全量） ──
const isNearBottom = ref(true)
const loadingOlder = ref(false)
const NEAR_BOTTOM_PX = 120
const LOAD_MORE_TOP_PX = 80

function onMessagesScroll() {
  const el = messagesWrap.value
  if (!el) return
  const distBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  isNearBottom.value = distBottom < NEAR_BOTTOM_PX
  if (el.scrollTop < LOAD_MORE_TOP_PX && hasMoreMessages.value && !loadingOlder.value) {
    loadMoreMessages()
  }
}

// ── 标题行内编辑 ──
const editingTitle = ref(false)
const titleDraft = ref('')

function startEditTitle() {
  if (!session.sessionId) return
  editingTitle.value = true
  titleDraft.value = session.currentTitle
}
async function commitTitle() {
  editingTitle.value = false
  const val = titleDraft.value.trim() || t('chat.newSession')
  if (session.sessionId && val && val !== session.currentTitle) {
    session.currentTitle = val
    try {
      await endpoints.updateSessionTitle(session.sessionId, val)
      await session.loadSessions()
    } catch {
      toast.show(t('chat.titleSaveFailed'), 'error')
    }
  }
}

// ── WS 事件处理 ──
// 事件归属判断：WS 可能保持处理中会话的连接（切走后仍监听其 done），
// 其他会话的 thinking/message 不混入当前消息流
const _isCurrent = (d) => !d.session_id || d.session_id === session.sessionId

// 错误回复前缀 → 渲染为报错横幅
const ERROR_PREFIXES = ['[思考失败]', '[模型调用失败]', '[工具调用达到上限', '[安全拦截]', '[安全审查拦截]', '[系统通知]']

const _onThinking = (d) => {
  if (!_isCurrent(d)) return
  // 统一分派：安全审批/提问/思考折叠/专家气泡 由 store 的 dispatchThinking 处理（与恢复同一规则）
  const kind = chat.dispatchThinking(d)
  if (kind === 'approval' || kind === 'intent' || kind === 'thinking' || kind === 'expert' || kind === 'tool_trace') {
    scrollBottom()
  }
}

const _onMessage = (d) => {
  if (!_isCurrent(d)) return
  // 心理活动：独立标注显示（不受停止状态影响）
  if (d.msg_type === 'mental' || d.event === 'mental') {
    if (!d.content) return
    chat.addMessage({
      role: 'system',
      content: d.content,
      kind: 'mental',
      id: '',
    })
    scrollBottom()
    return
  }
  // 用户已主动停止：忽略后端补发的 message，避免"按了停止又冒出回复"
  if (chat.stopped) return
  const content = d.content || ''
  if (content) {
    // 思考元数据（内心独白 + 事件记忆 + 会话记忆）→ 附加到消息下方展开栏
    // 会话记忆：优先展示后端实际注入 AI 的【对话历史】原文（同一数据源，保证与 AI 看到的一致）；
    // 后端未提供时回退为本地消息组装（兜底）
    const m = d.data?.meta || {}
    const sessionMemory = m.conversation_history || chat.messages
      .filter(x => x.role === 'user' || x.role === 'assistant')
      .slice(-6)
      .map(x => `[${x.role === 'user' ? 'user' : 'assistant'}]: ${(x.content || '').slice(0, 200)}`)
      .join('\n')
    // 错误回复检测 → 渲染为报错横幅（红色）
    const isError = ERROR_PREFIXES.some(p => content.startsWith(p))
    // 非流式：直接渲染最终答案 + 打字机；本轮的思考过程合并到消息 thinking（框内折叠）
    const thinkText = chat.consumeThinking()
    chat.addMessage({
      role: 'assistant',
      content,
      id: d.data?.message_id || '',
      identity_name: d.data?.identity_name || '',
      typing: true,
      error: isError,
      thinking: thinkText,
      meta: {
        innerMonologue: m.inner_monologue || '',
        eventMemory: m.event_memory || '',
        sessionMemory,
      },
    })
  } else {
    chat.finalizeStream('')
  }
  scrollBottom()
}

const _onDone = (d) => {
  if (d.session_id && d.session_id !== session.sessionId) {
    // 处理中其他会话完成 → 仅清理该会话状态（并行会话互不干扰）
    chat.finalizeSession(d.session_id)
    return
  }
  chat.finalizeStream('')
}
const _onError = (d) => {
  if (d.session_id && d.session_id !== session.sessionId) {
    // 其他会话的错误：仅清理该会话处理状态，不影响当前会话
    chat.finalizeSession(d.session_id)
    return
  }
  chat.finalizeStream('')
  toast.show(t('chat.errorPrefix') + ': ' + (d.content || t('chat.unknown')), 'error')
}

const _onStatus = (d) => {
  if (!_isCurrent(d)) return
  chat.elapsed = d.data?.elapsed_s || 0
  // 上下文窗口占用（估算 token，用于显示上下文占比）。
  // 保持最近一次非零值：后端仅在大模型构建 prompt 时更新 tokens，
  // 中间的心跳可能带 0，直接覆盖会让上下文条闪烁/消失。
  if (d.data?.context_tokens) contextTokens.value = d.data.context_tokens
  if (d.data?.context_window_size) contextWindowSize.value = d.data.context_window_size
  // 解析 thinking_progress → 在线模型状态（大循环 指挥/主管/专家 层级）
  const list = []
  const add = (r, tier, sup) => { if (r && r.model_id) list.push({ ...r, tier, supervisor: sup || '' }) }
  if (d.data?.large_model) add(d.data.large_model, 'large', '')
  ;(d.data?.active_supervisors || []).forEach(s => add(s, 'supervisor', ''))
  ;(d.data?.active_experts || []).forEach(e => add(e, 'expert', e.supervisor || ''))
  chat.runners = list
  // 过程流面板的模型状态沿用本次快照（显示"当时"运行的模型，非实时刷新）
  chat.setProcessSnapshot({
    large_model: d.data?.large_model || null,
    active_supervisors: d.data?.active_supervisors || [],
    active_experts: d.data?.active_experts || [],
    context_tokens: d.data?.context_tokens || 0,
    context_window_size: d.data?.context_window_size || 0,
  })
}

const _onAck = (d) => {
  if (!_isCurrent(d)) return
  // 任何 ack（received / busy）带 message_id 都回填最后一条 user 消息（供删除/编辑使用）。
  // busy 时后端也已保存 user 消息（不再丢弃），故需回填其 id 保持一致
  if (d.data?.message_id) {
    const last = chat.messages[chat.messages.length - 1]
    if (last && last.role === 'user' && !last.id) last.id = d.data.message_id
  }
  if (d.event === 'busy') {
    // 后端正忙且丢弃了本条 input → 提示并稍后重发（避免"一直加载"）
    chat.hint = t('chat.sessionBusy')
    setTimeout(() => chat.retryLastInput(), 2500)
  }
}

const _onProactive = (d) => {
  const content = d.content || ''
  if (!content) return
  // 主动搭话发到"上一次对话会话"——若当前不在该会话，刷新会话列表以便用户看到，不混入当前消息流
  if (d.session_id && d.session_id !== session.sessionId) {
    session.loadSessions()
    return
  }
  chat.addMessage({ role: 'assistant', content, id: d.data?.message_id || '', proactive: true })
  scrollBottom()
}

// todo 变更推送：后端 todo 工具执行后推送，收到即按需拉取（替代 3s 轮询）
const _onTodo = (d) => {
  // 只刷新当前会话的 todo（后端按 session 隔离推送）
  if (d.session_id && d.session_id !== session.sessionId) return
  loadTodos()
}

function scrollBottom(force = false) {
  nextTick(() => {
    const el = messagesWrap.value
    if (!el) return
    // 仅在用户接近底部时自动滚到底（force=进入会话/发送消息时强制）
    if (!force && el.scrollHeight - el.scrollTop - el.clientHeight > 120) return
    el.scrollTop = el.scrollHeight
    isNearBottom.value = true
  })
}

onMounted(async () => {
  await session.loadSessions()
  await chat.init()
  // 支持从仪表盘等通过 ?session= 跳转到指定会话
  // 必须走 switchToSession：加载历史消息 + 建立该会话的 WS 连接，
  // 仅设置 sessionId（switchSession）会导致历史不加载、回复接收不到
  const qsid = route.query?.session
  if (qsid) {
    try {
      await chat.switchToSession(String(qsid))
      renderLimit.value = RENDER_LIMIT
      scrollBottom(true)
    } catch {}
  }
  ws.wsClient.on('thinking', _onThinking)
  ws.wsClient.on('message', _onMessage)
  ws.wsClient.on('mental', _onMessage)  // 心理活动事件（msg_type='mental'）复用 _onMessage 分支
  ws.wsClient.on('done', _onDone)
  ws.wsClient.on('error', _onError)
  ws.wsClient.on('ack', _onAck)
  ws.wsClient.on('status', _onStatus)
  ws.wsClient.on('proactive', _onProactive)
  ws.wsClient.on('todo', _onTodo)
  // 连接看门狗：处理中断开 → 复位加载态并提示，避免"一直加载"
  watchTimer = setInterval(() => {
    if (chat.processing && !ws.wsClient.isConnected(session.sessionId)) {
      chat.finalizeStream('')
      toast.show(t('chat.connectionLost'), 'error')
    }
  }, 2000)
  // 初始加载 todo（切换会话时由 _onTodo / 会话变更刷新）
  loadTodos()
})

// KeepAlive 缓存下 onMounted 只首次执行——切页回来刷新会话列表
onActivated(() => {
  session.loadSessions()
})

onUnmounted(() => {
  ws.wsClient.off('thinking', _onThinking)
  ws.wsClient.off('message', _onMessage)
  ws.wsClient.off('mental', _onMessage)
  ws.wsClient.off('done', _onDone)
  ws.wsClient.off('error', _onError)
  ws.wsClient.off('ack', _onAck)
  ws.wsClient.off('status', _onStatus)
  ws.wsClient.off('proactive', _onProactive)
  ws.wsClient.off('todo', _onTodo)
  if (watchTimer) clearInterval(watchTimer)
})

function handleSend({ text, attachments }) {
  if (chat.processing) return
  chat.sendMessage(text, attachments)
  const images = (attachments || [])
    .filter(a => a && String(a.type || '').startsWith('image/'))
    .map(a => a.data)
  chat.addMessage({ role: 'user', content: text, images })
  chat.hint = t('chat.thinking')
  scrollBottom()
}

async function handleNewSession() {
  await chat.init()
  contextTokens.value = 0
  contextWindowSize.value = 0
}

async function handleSessionSelect(sid) {
  await chat.switchToSession(sid)
  // 上下文占用按会话隔离：切换会话后清空，等待新会话的 thinking_progress 重新填充
  contextTokens.value = 0
  contextWindowSize.value = 0
  // 进入会话默认定位到最新消息（强制，不受"接近底部才滚"守卫限制）
  renderLimit.value = RENDER_LIMIT
  scrollBottom(true)
}

async function handleSessionDelete(sid) {
  // 批量删除完成（SessionList 已处理），仅刷新会话列表
  if (!sid) {
    await session.loadSessions()
    return
  }
  if (!(await confirm(t('chat.confirmDeleteSession')))) return
  await session.deleteSession(sid)
  if (sid === session.sessionId) {
    // 删除当前会话 → 切换到最近一个，否则回到新会话
    if (session.sessions.length > 0) {
      await chat.switchToSession(session.sessions[0].session_id)
    } else {
      await chat.init()
    }
    renderLimit.value = RENDER_LIMIT
    scrollBottom(true)
  }
}

async function handleSessionRename(sid) {
  const s = session.sessions.find(x => x.session_id === sid)
  const val = await prompt(t('chat.renameTitle'), s?.title || '')
  if (val === null || !val.trim()) return
  try {
    await endpoints.updateSessionTitle(sid, val.trim())
    await session.loadSessions()
    if (sid === session.sessionId) session.currentTitle = val.trim()
  } catch {
    toast.show(t('chat.renameFailed'), 'error')
  }
}

function handleCopyMessage(idx) {
  const msg = chat.messages[idx]
  if (!msg?.content) return
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(msg.content)
      .then(() => toast.show(t('common.copied'), 'success'))
      .catch(() => copyTextFallback(msg.content))
  } else {
    copyTextFallback(msg.content)
  }
}

function copyTextFallback(text) {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;width:1px;height:1px'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    toast.show(ok ? t('common.copied') : t('chat.copyFailed'), ok ? 'success' : 'error')
  } catch {
    toast.show(t('chat.copyFailed'), 'error')
  }
}

async function handleDeleteMessage(idx) {
  const msg = chat.messages[idx]
  if (!msg) return
  if (!(await confirm(t('chat.confirmDeleteMessage')))) return
  const ok = await chat.deleteMessageAt(idx)
  if (!ok) toast.show(t('chat.deleteFailed'), 'error')
}

async function handleEditMessage(idx) {
  const msg = chat.messages[idx]
  if (!msg?.id) {
    toast.show(t('chat.msgNotSaved'), 'warning')
    return
  }
  const val = await prompt(t('chat.editMsgTitle'), msg.content)
  if (val === null) return
  const ok = await chat.editMessageAt(idx, val)
  if (!ok) toast.show(t('chat.editFailed'), 'error')
}

async function handleClearChat() {
  if (chat.messages.length === 0) return
  if (!(await confirm(t('chat.confirmClear')))) return
  chat.clearMessages()
  try {
    if (session.sessionId) {
      await fetch('/api/stream/session/' + encodeURIComponent(session.sessionId) + '/messages', { method: 'DELETE' })
      await session.loadSessions()
    }
  } catch { toast.show(t('chat.localClearedBackendFailed'), 'error') }
}

function handleApprove(requestId, approved) {
  chat.approve(requestId, approved)
}

function handleAnswerIntent(requestId, answer) {
  chat.answerIntent(requestId, answer)
}
</script>

<template>
  <div class="chat-layout">
    <SessionList
      :sessions="session.sessions"
      :activeId="session.sessionId"
      :collapsed="sessionListCollapsed"
      @update:collapsed="v => sessionListCollapsed = v"
      @select="handleSessionSelect"
      @delete="handleSessionDelete"
      @rename="handleSessionRename"
      @new="handleNewSession"
      :style="{ width: sessionListCollapsed ? 40 : 260, flexShrink: 0 }"
    />
    <div class="chat-main">
      <div class="chat-header">
        <div class="chat-header-left">
          <span v-if="!editingTitle" class="chat-header-title" :class="{ editable: !!session.sessionId }" @click="startEditTitle">{{ session.currentTitle }}</span>
          <input
            v-else
            v-model="titleDraft"
            class="edit-title-input"
            maxlength="50"
            @blur="commitTitle"
            @keydown.enter="commitTitle"
            @keydown.esc="editingTitle = false"
          />
          <ModelSelector :session-id="session.sessionId" />
          <!-- 当前激活技能（来自 thinking_progress 的 large_model.active_skill） -->
          <span v-if="activeSkill" class="chat-skill-chip" :title="$t('chat.currentSkillTitle', { skill: activeSkill })">⚡ {{ activeSkill }}</span>
        </div>
        <div class="chat-header-right">
          <button class="chat-btn-icon" @click="showTodos = !showTodos" :title="$t('chat.todoList')"><Icon name="list" :size="15" /></button>
          <button class="chat-btn-icon" @click="showSettings = true" v-if="session.sessionId" :title="$t('chat.sessionSettingsTitle')"><Icon name="settings" :size="15" /></button>
          <button class="chat-btn-icon" @click="chat.stop()" v-if="chat.processing" :title="$t('chat.stop')"><Icon name="square" :size="15" /></button>
          <button class="chat-btn-icon" @click="handleClearChat" :title="$t('chat.clearChat')"><Icon name="trash" :size="15" /></button>
        </div>
      </div>

      <!-- 消息区 -->
      <div ref="messagesWrap" class="chat-messages" @scroll="onMessagesScroll">
        <div v-if="chat.messages.length === 0 && !chat.processing" class="chat-welcome">
          <div class="welcome-icon"><Icon name="message" :size="40" /></div>
          <h2>{{ $t('chat.startNewChat') }}</h2>
          <p>{{ $t('chat.welcomeDesc') }}</p>
          <div class="quick-actions">
            <div class="quick-action" @click="handleSend({ text: '你好，请介绍一下你自己', attachments: [] })">{{ $t('chat.quickGreeting') }}</div>
            <div class="quick-action" @click="handleSend({ text: '帮我分析一下项目结构', attachments: [] })">{{ $t('chat.quickAnalyze') }}</div>
            <div class="quick-action" @click="handleSend({ text: '给我写一段代码', attachments: [] })">{{ $t('chat.quickCode') }}</div>
          </div>
        </div>

        <!-- 思考循环状态面板：大循环（指挥→主管→专家）/ 连续思考 / 工具循环 -->
        <ThinkingStatusPanel v-if="chat.processing && chat.runners.length" :runners="chat.runners" :elapsed="chat.elapsed" :context-tokens="contextTokens" :context-window-size="contextWindowSize" />

        <!-- 本轮实时过程流：AI 运行过程中边思考边展示（结束后固化为消息，由 ProcessPanel 持久化恢复） -->
        <ProcessPanel
          v-if="chat.processing && chat.processFlow.length"
          :content="chat.processFlow.join('\n\n')"
          :runners="chat.processSnapshot"
          :open="true"
        />

        <!-- 运行轨迹：工具调用/委托等中间步骤（借鉴 dsh，从对话流分离） -->
        <div v-if="chat.traces.length" class="chat-traces">
          <div class="chat-traces-header" @click="tracesOpen = !tracesOpen">
            <span>{{ $t('chat.tracesTitle', { count: chat.traces.length }) }}</span>
            <span class="chevron">{{ tracesOpen ? $t('chat.tracesCollapse') : $t('chat.tracesExpand') }}</span>
          </div>
          <div v-if="tracesOpen" class="chat-traces-body">
            <div v-for="(tr, i) in chat.traces" :key="i" class="trace-item">
              <span class="trace-dot"></span>{{ tr.text }}
            </div>
          </div>
        </div>

        <div class="chat-load-more" v-if="hasMoreMessages" @click="loadMoreMessages">
          {{ loadingOlder ? $t('common.loading') : $t('chat.loadEarlier', { count: chat.messages.length - visibleMessages.length }) }}
        </div>

        <ChatMessage
          v-for="(item, i) in visibleMessages"
          :key="item._id"
          :message="item"
          :index="msgOffset + i"
          @copy="handleCopyMessage"
          @delete="handleDeleteMessage"
          @edit="handleEditMessage"
          @approve="handleApprove"
          @answer-intent="handleAnswerIntent"
        />

        <!-- 正在等待 AI 回复：显示在最后一条用户消息下方 -->
        <ThinkingIndicator v-if="showThinkingWaiting" :label="chat.elapsed ? $t('chat.thinkingElapsed', { elapsed: chat.elapsed }) : $t('chat.thinkingState')" />
      </div>

      <!-- 回到底部悬浮按钮：离开底部时出现 -->
      <transition name="chat-fade">
        <button
          v-if="!isNearBottom && chat.messages.length"
          class="chat-scroll-bottom"
          :title="$t('chat.scrollBottom')"
          @click="scrollBottom(true)"
        >
          <Icon name="down" :size="16" />
          <span v-if="chat.processing" class="chat-scroll-dot"></span>
        </button>
      </transition>

      <ChatInput :processing="chat.processing" :hint="chat.hint" @send="handleSend" @toast="(t) => toast.show(t.message, t.type)">
        <template #actions>
          <button v-if="chat.processing" class="btn btn-sm btn-stop" @click="chat.stop()"><Icon name="stop" :size="14" /> {{ $t('chat.stop') }}</button>
        </template>
      </ChatInput>
    </div>

    <!-- todo 待办面板（模型通过 todo 工具维护，按当前会话隔离） -->
    <div v-if="showTodos" class="todo-panel">
      <div class="todo-header">
        <span>{{ $t('chat.todoCount', { done: todoDone, total: todos.length }) }}</span>
        <button class="chat-btn-icon" @click="showTodos = false" :title="$t('chat.collapse')"><Icon name="right" :size="14" /></button>
      </div>
      <div class="todo-progress">
        <div class="todo-progress-bar" :style="{ width: (todos.length ? (todoDone / todos.length) * 100 : 0) + '%' }"></div>
      </div>
      <div class="todo-list">
        <div
          v-for="(t, i) in todos" :key="t.id || i"
          class="todo-item"
          :title="t.status === 'completed' ? $t('chat.todoClickRestore') : $t('chat.todoClickComplete')"
          @click="toggleTodo(t)"
        >
          <span class="todo-icon">{{ t.status === 'completed' ? '☑' : t.status === 'in_progress' ? '◐' : '☐' }}</span>
          <span class="todo-text" :class="{ done: t.status === 'completed' }">{{ t.content }}</span>
        </div>
        <div v-if="!todos.length" class="todo-empty">{{ $t('chat.todoEmpty') }}<br/><span class="todo-empty-hint">{{ $t('chat.todoEmptyHint') }}</span></div>
      </div>
    </div>

    <!-- 会话设置弹窗 -->
    <SessionSettings
      v-if="showSettings && session.sessionId"
      :session-id="session.sessionId"
      :title="session.currentTitle"
      @close="showSettings = false"
    />
  </div>
</template>
