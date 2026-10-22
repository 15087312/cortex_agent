<script setup>
defineOptions({ name: 'Pet' })
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useToastStore } from '@/stores/toast.js'
import { endpoints, request } from '@/api.js'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const toast = useToastStore()

// ── Tab ──
const tab = ref('chat')

// ── 对话 ──
const messages = ref([])          // {role, content}
const input = ref('')
const sending = ref(false)
const abortCtl = ref(null)
const messagesWrap = ref(null)
let autoTimer = null // 连续思考模式的下一轮定时器
let stopAuto = false // 用户点「停止」后终止本页的持续思考
let userChatted = false // 本次页面会话中用户是否手动发过消息（direct 循环续聊的前提）
const sendQueue = [] // 排队等待发送的消息（AI 输出中用户新发的消息）

// ── 语音录制 ──
const recording = ref(false)
let petMediaRecorder = null
let petAudioChunks = []

async function toggleRecording() {
  if (recording.value) { stopRecording() } else { await startRecording() }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    petMediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' })
    petAudioChunks = []
    petMediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) petAudioChunks.push(e.data) }
    petMediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop())
      const blob = new Blob(petAudioChunks, { type: 'audio/webm' })
      petAudioChunks = []
      await transcribeAudio(blob)
    }
    petMediaRecorder.start()
    recording.value = true
  } catch (e) {
    toast.show(t('pet.micError'), 'error')
  }
}

function stopRecording() {
  if (petMediaRecorder && petMediaRecorder.state !== 'inactive') {
    petMediaRecorder.stop()
    recording.value = false
  }
}

async function transcribeAudio(blob) {
  try {
    const r = await endpoints.recognizeSpeech(blob)
    if (r?.success && r.data?.text) {
      input.value += (input.value ? ' ' : '') + r.data.text
    } else {
      toast.show(t('pet.transcribeFailed'), 'error')
    }
  } catch {
    toast.show(t('pet.transcribeFailed'), 'error')
  }
}

// 智能滚动：只有当用户原本就在底部附近时才跟随新输出；
// 用户向上翻阅历史时不强制拽回底部（距离底阈 60px）
let stickToBottom = true
function onMessagesScroll() {
  const el = messagesWrap.value
  if (!el) return
  stickToBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
}
function scrollDown({ force = false } = {}) {
  if (!force && !stickToBottom) return
  nextTick(() => {
    const el = messagesWrap.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

async function loadHistory() {
  try {
    const r = await endpoints.petHistory(100)
    messages.value = (r?.data?.items || []).map((m) => ({ id: m.id, role: m.role, content: m.content, thinking: '' }))
    // 互动消息装饰：用户消息内容匹配动作 prompt → 渲染成图标气泡（与 Chat 页一致）
    for (const m of messages.value) {
      if (m.role !== 'user' || !m.content) continue
      const act = petActs.value.find((a) => a.prompt === m.content)
      if (act) { m.actionId = act.id; m.actionLabel = act.label; m.actionIcon = act.icon; m.content = '' }
    }
    scrollDown()
  } catch { /* 后端未就绪时静默 */ }
}

// ── 语音播放（桌宠回复 TTS）──
let currentAudio = null
function playPetAudio(url) {
  try {
    stopPetAudio()
    currentAudio = new Audio(url)
    currentAudio.play().catch(() => {}) // 自动播放被浏览器拦截时静默（用户交互后恢复）
  } catch { /* 忽略播放异常，不影响文本 */ }
}
function stopPetAudio() {
  if (currentAudio) {
    try { currentAudio.pause(); currentAudio.currentTime = 0 } catch { /* */ }
    currentAudio = null
  }
}

// SSE 帧泵：与文本聊天/互动共用（token / chunk / round_complete / error / done）。
// 返回流结束时的最后一条消息引用（供 noReply / 失败提示写入）
async function pumpSse(res, { last, onRoundStart } = {}) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    // sse-starlette 默认用 \r\n 行分隔（帧尾为 \r\n\r\n），兼容纯 \n\n
    const frames = buf.split(/\r?\n\r?\n/)
    buf = frames.pop()
    frames.forEach((frame) => {
      // 合并全部 data: 行（多行 data 拼接；\r 由 trim 去除）
      const data = frame.split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim())
        .join('')
      if (!data) return
      const evName = frame.split('\n')
        .filter((l) => l.startsWith('event:'))
        .map((l) => l.slice(6).trim())[0] || ''
      let evt
      try { evt = JSON.parse(data) } catch { return }
      if (evName === 'done') {
        // SSE 结束：删掉本轮最后可能遗留的空白消息（round_complete 后未再收到内容）；
        // 语音开关开启时，done 帧携带后端合成好的音频 URL → 播放桌宠回复
        const tail = messages.value[messages.value.length - 1]
        if (tail && tail.role === 'assistant' && !tail.content && !tail.thinking) {
          messages.value.pop()
          if (tail === last) last = messages.value[messages.value.length - 1]
        }
        if (evt.audio_url) playPetAudio(evt.audio_url)
      } else if (evt.token != null) {
        last.content += evt.token
        scrollDown()
      } else if (evt.chunk != null) {
        last.thinking += evt.chunk
        scrollDown()
      } else if (evt.round_complete) {
        if (onRoundStart) onRoundStart()
        messages.value.push({ role: 'assistant', content: '', thinking: '' })
        last = messages.value[messages.value.length - 1]
        scrollDown()
      } else if (evt.error) {
        if (!last.content) last.content = t('pet.error') + '：' + evt.error
      }
    })
  }
  return last
}

async function send(autoText) {
  const isAuto = autoText === '' // 空字符串 = 主动思考模式（页面加载自动触发）
  if (isAuto && stopAuto) return
  if (!isAuto) {
    userChatted = true
    stopAuto = false // 手动发消息 = 重新唤醒桌宠（自动续聊链路恢复）
  }
  const text = isAuto ? '' : input.value.trim()
  if (!isAuto && !text) return
  // AI 正在输出时，用户消息排队等待
  if (!isAuto && sending.value) {
    if (!input.value.trim()) return
    sendQueue.push(input.value.trim())
    input.value = ''
    toast.show(t('pet.queuedMessage'), 'info')
    return
  }
  if (!isAuto) input.value = ''
  if (!isAuto) messages.value.push({ role: 'user', content: text })
  messages.value.push({ role: 'assistant', content: '', thinking: '' })
  sending.value = true
  scrollDown({ force: true }) // 用户主动发言/新气泡：回到底部
  const ctl = new AbortController()
  abortCtl.value = ctl
  let last = messages.value[messages.value.length - 1]
  let roundCount = 0
  // 连续思考模式（custom 循环 + max_loop_rounds=0 或 direct 连续对话）：
  // SSE 正常结束后延迟自动发起下一轮主动思考，形成持续对话
  const shouldContinue = () =>
    cfg.value.max_loop_rounds === 0 &&
    (cfg.value.loop_type === 'custom' || userChatted)
  try {
    const res = await fetch('/api/stream/pet/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ text }),
      signal: ctl.signal,
    })
    if (!res.ok || !res.body) throw new Error('HTTP ' + res.status)
    last = await pumpSse(res, {
      last,
      onRoundStart: () => {
        // 一轮完整回复结束：第一轮关闭"接收中"
        if (roundCount === 0) sending.value = false
        roundCount++
      },
    })
    if (!isAuto && !last.content && roundCount === 0) last.content = t('pet.noReply')
    // 正常结束后重拉历史：为已持久化的消息补上 id（支持逐条删除）
    await loadHistory()
    // 连续思考模式：本次思考完成后继续下一轮（间隔由后端 min_think_interval 控制）。
    // 防重护栏：本轮产出与上一条助手消息完全相同 → 模型在复述，停止自动续聊
    const historyAfter = messages.value
    let lastTwo = null
    for (let i = historyAfter.length - 1; i >= 0; i--) {
      const m = historyAfter[i]
      if (m.role === 'assistant' && m.content) {
        if (lastTwo === null) { lastTwo = m.content; continue }
        if (lastTwo === m.content) { lastTwo = false }
        break
      }
    }
    if (lastTwo === false) {
      stopAuto = true
      toast.show(t('pet.stoppedRepetition'), 'info')
    }
    if (shouldContinue()) {
      if (autoTimer) clearTimeout(autoTimer)
      autoTimer = setTimeout(() => { autoTimer = null; if (!sending.value) send('') }, (cfg.value.min_think_interval || 5) * 1000)
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      if (!last.content) last.content = t('pet.sendFailed')
      if (!isAuto) toast.show(t('pet.sendFailed'), 'error')
    }
  } finally {
    sending.value = false
    abortCtl.value = null
    scrollDown()
    // 队列中有排队消息 → 自动发送下一条
    if (sendQueue.length > 0) {
      const next = sendQueue.shift()
      input.value = next
      send()
    }
  }
}

function onInputEnter() {
  // 回车发送前用户必然在底部（输入框就在底部），发送后强制跟随
  stickToBottom = true
  send()
}

function stopSend() {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  stopAuto = true // 停止按钮同时终止持续思考链
  stopPetAudio() // 同时停止语音播放
  if (abortCtl.value) abortCtl.value.abort()
}

async function delMessage(m) {
  if (!m.id) return
  try {
    const r = await endpoints.deletePetHistoryMessage(m.id)
    if (r?.success) {
      const idx = messages.value.indexOf(m)
      if (idx >= 0) messages.value.splice(idx, 1)
      toast.show(t('pet.msgDeleted'), 'success')
    } else {
      toast.show(t('pet.saveFailed'), 'error')
    }
  } catch { toast.show(t('pet.saveFailed'), 'error') }
}

// ── 设置 ──
const cfg = ref({
  persona: '', api_url: '', api_model: '', api_format: '', api_provider: '',
  api_key_set: false, loop_type: 'direct', memory_enabled: true,
})
const apiKeyDraft = ref('')
const savingCfg = ref(false)
// 桌宠总开关（全局 DESKTOP_PET_ENABLED，运行时启停后端引擎）
const masterEnabled = ref(true)
const savingMaster = ref(false)

async function loadMasterEnabled() {
  try {
    const r = await request('GET', '/config/DESKTOP_PET_ENABLED')
    masterEnabled.value = r?.value !== false && r?.value !== 0
  } catch { /* 静默，保持默认 true */ }
}

async function toggleMaster() {
  await setMasterEnabled(!masterEnabled.value)
}

// 打开/关闭桌面宠物（进程级：拉起/停止窗口进程 + 同步引擎总开关）
// 窗口进程由后端 window_supervisor 统一持有：崩溃自动重启、Web 页可见状态。
async function setMasterEnabled(v) {
  if (savingMaster.value) return
  savingMaster.value = true
  try {
    if (v) {
      const r = await endpoints.updateConfig('DESKTOP_PET_ENABLED', true)
      if (r?.success === false) throw new Error('config')
      masterEnabled.value = true
      try {
        const w = await endpoints.petWindowStart()
        if (w?.success === false) {
          toast.show(t('pet.windowStartFailed') + '：' + (w?.error || ''), 'error')
        } else {
          toast.show(t('pet.enabled'), 'success')
        }
      } catch {
        toast.show(t('pet.windowStartFailed'), 'error')
      }
    } else {
      // 先停窗口进程，再关引擎开关（后端不可达时仍继续关开关）
      try { await endpoints.petWindowStop() } catch { /* 忽略，继续 */ }
      const r = await endpoints.updateConfig('DESKTOP_PET_ENABLED', false)
      if (r?.success === false) throw new Error('config')
      masterEnabled.value = false
      toast.show(t('pet.disabled'), 'success')
      if (abortCtl.value) abortCtl.value.abort() // 关闭时中断当前思考
    }
    await loadWindowStatus()
  } catch {
    toast.show(t('pet.saveFailed'), 'error')
  } finally {
    savingMaster.value = false
  }
}

// ── 桌宠窗口进程状态（后端监管：运行中/pid/崩溃重启次数）──
const petWindow = ref({ running: false, pid: null, restarts: 0, exit_code: null, gave_up_auto_restart: false })
async function loadWindowStatus() {
  try {
    const r = await endpoints.petWindowStatus()
    if (r?.data) petWindow.value = { ...petWindow.value, ...r.data }
  } catch { /* 静默 */ }
}
let windowPollTimer = null

const LOOP_TYPES = [
  { value: 'direct', labelKey: 'pet.loopDirect' },
  { value: 'custom', labelKey: 'pet.loopCustom' },
]

async function loadConfig() {
  try {
    const r = await endpoints.petConfig()
    const d = r?.data || {}
    cfg.value = {
      persona: d.persona || '', api_url: d.api_url || '', api_model: d.api_model || '',
      api_format: d.api_format || '', api_provider: d.api_provider || '',
      api_key_set: !!d.api_key_set, loop_type: d.loop_type || 'direct',
      memory_enabled: d.memory_enabled !== false,
      min_think_interval: d.min_think_interval ?? 5,
      tools_enabled: d.tools_enabled !== false,
      max_history: d.max_history ?? 8,
      max_output_tokens: d.max_output_tokens ?? 512,
      max_loop_rounds: d.max_loop_rounds ?? 0,
      idle_backoff_max: d.idle_backoff_max ?? 8,
      voice_enabled: !!d.voice_enabled,
      realtime_voice: !!d.realtime_voice,
    }
  } catch { /* 静默 */ }
}

async function saveConfig() {
  savingCfg.value = true
  try {
    const patch = {
      persona: cfg.value.persona,
      api_url: cfg.value.api_url,
      api_model: cfg.value.api_model,
      api_format: cfg.value.api_format,
      api_provider: cfg.value.api_provider,
      loop_type: cfg.value.loop_type,
      memory_enabled: cfg.value.memory_enabled,
      min_think_interval: Number(cfg.value.min_think_interval) || 5,
      tools_enabled: cfg.value.tools_enabled,
      max_history: Math.max(1, Number(cfg.value.max_history) || 8),
      max_output_tokens: Math.max(1, Number(cfg.value.max_output_tokens) || 512),
      max_loop_rounds: Math.max(0, Number(cfg.value.max_loop_rounds) || 0),
      idle_backoff_max: Math.max(1, Number(cfg.value.idle_backoff_max) || 8),
      voice_enabled: !!cfg.value.voice_enabled,
      realtime_voice: !!cfg.value.realtime_voice,
    }
    if (apiKeyDraft.value.trim()) patch.api_key = apiKeyDraft.value.trim()
    const r = await endpoints.updatePetConfig(patch)
    if (r?.success) {
      toast.show(t('pet.saved'), 'success')
      apiKeyDraft.value = ''
      await loadConfig()
    } else {
      toast.show(t('pet.saveFailed'), 'error')
    }
  } catch {
    toast.show(t('pet.saveFailed'), 'error')
  } finally {
    savingCfg.value = false
  }
}

async function clearHistory() {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  try {
    const r = await endpoints.clearPetHistory()
    if (r?.success) { messages.value = []; toast.show(t('pet.historyCleared'), 'success') }
  } catch { toast.show(t('pet.saveFailed'), 'error') }
}

// ── 互动（与桌面桌宠圆环菜单同源：/stream/pet/actions + /state）──
const petCats = ref([])      // [{id,label,icon}]
const petActs = ref([])      // [{id,label,category,icon}]
const activeCat = ref('')    // 当前展开的动作分类
const petStateValues = ref(null) // {mood,satiety,energy,cleanliness}
const STATE_META = [
  { key: 'mood', icon: 'smile', labelKey: 'pet.stateMood' },
  { key: 'satiety', icon: 'utensils', labelKey: 'pet.stateSatiety' },
  { key: 'energy', icon: 'sun', labelKey: 'pet.stateEnergy' },
  { key: 'cleanliness', icon: 'sparkles', labelKey: 'pet.stateClean' },
]

async function loadActions() {
  try {
    const r = await request('GET', '/stream/pet/actions')
    petCats.value = r?.data?.categories || []
    petActs.value = r?.data?.actions || []
    // 动作后到：补装饰已加载历史里的互动消息
    for (const m of messages.value) {
      if (m.role !== 'user' || !m.content || m.actionId) continue
      const act = petActs.value.find((a) => a.prompt === m.content)
      if (act) { m.actionId = act.id; m.actionLabel = act.label; m.actionIcon = act.icon; m.content = '' }
    }
  } catch { /* 后端未就绪时静默 */ }
}

async function loadState() {
  try {
    const r = await endpoints.petState()
    petStateValues.value = r?.data?.values || null
  } catch { /* 静默 */ }
}

function actsOf(catId) {
  return petActs.value.filter((a) => a.category === catId)
}

async function sendAction(action) {
  if (sending.value) return
  activeCat.value = ''
  stopAuto = false
  userChatted = true // 互动算用户发言：direct 循环的自动续聊链路恢复
  messages.value.push({ role: 'user', content: '', actionId: action.id, actionLabel: action.label, actionIcon: action.icon })
  messages.value.push({ role: 'assistant', content: '', thinking: '' })
  sending.value = true
  scrollDown({ force: true })
  const ctl = new AbortController()
  abortCtl.value = ctl
  let last = messages.value[messages.value.length - 1]
  try {
    const res = await fetch('/api/stream/pet/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ action_id: action.id }),
      signal: ctl.signal,
    })
    if (!res.ok || !res.body) throw new Error('HTTP ' + res.status)
    last = await pumpSse(res, { last })
    await loadHistory()
    loadState() // 互动改变状态 → 刷新状态条
  } catch (e) {
    if (e.name !== 'AbortError' && last && !last.content) last.content = t('pet.sendFailed')
  } finally {
    sending.value = false
    abortCtl.value = null
    scrollDown()
  }
}

// ── 记忆 ──
const memories = ref([])
const memKind = ref('fact')
const memText = ref('')
const memLoading = ref(false)

const MEM_KINDS = [
  { value: 'fact', labelKey: 'pet.memKindFact' },
  { value: 'preference', labelKey: 'pet.memKindPref' },
  { value: 'event', labelKey: 'pet.memKindEvent' },
]

async function loadMemory() {
  memLoading.value = true
  try {
    const r = await endpoints.petMemory(100)
    memories.value = (r?.data?.items || []).map((m) => ({
      ...m,
      time: m.created_at ? new Date(m.created_at * 1000).toLocaleString() : '',
    }))
  } catch { /* 静默 */ } finally { memLoading.value = false }
}

async function addMemory() {
  const text = memText.value.trim()
  if (!text) return
  try {
    const r = await endpoints.addPetMemory(memKind.value, text)
    if (r?.success) {
      memText.value = ''
      await loadMemory()
      toast.show(t('pet.memAdded'), 'success')
    }
  } catch { toast.show(t('pet.saveFailed'), 'error') }
}

async function delMemory(mid) {
  try {
    const r = await endpoints.deletePetMemory(mid)
    if (r?.success) { await loadMemory() }
  } catch { /* 静默 */ }
}

onMounted(async () => {
  loadHistory()
  await loadConfig()
  await loadMasterEnabled()
  loadMemory()
  loadActions()
  loadState()
  loadWindowStatus()
  // 窗口进程状态轮询（10s）：崩溃/重启后页眉徽标同步
  if (windowPollTimer) clearInterval(windowPollTimer)
  windowPollTimer = setInterval(loadWindowStatus, 10000)
  // 不再自动说话：用户手动发送或点思考才触发
})

onUnmounted(() => {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  if (windowPollTimer) { clearInterval(windowPollTimer); windowPollTimer = null }
})
</script>

<template>
  <div class="pet-page">
    <div class="pet-header">
      <div class="pet-header-left">
        <span class="pet-title"><Icon name="bot" :size="16" /> {{ $t('nav.pet') }}</span>
        <span class="pet-badge">{{ cfg.loop_type }}</span>
      </div>
      <div class="pet-header-right">
        <span v-if="petWindow.running" class="badge badge-green" :title="'pid ' + petWindow.pid">{{ $t('pet.windowRunning') }} · {{ petWindow.pid }}</span>
        <span v-else class="badge badge-gray">{{ $t('pet.windowStopped') }}</span>
        <button v-if="!masterEnabled" class="btn btn-sm btn-primary" :disabled="savingMaster" @click="setMasterEnabled(true)"><Icon name="play" :size="13" /> {{ $t('pet.openPet') }}</button>
        <button v-else class="btn btn-sm" :disabled="savingMaster" @click="setMasterEnabled(false)"><Icon name="stop" :size="13" /> {{ $t('pet.closePet') }}</button>
        <div class="seg">
          <button :class="{ on: tab === 'chat' }" @click="tab = 'chat'">{{ $t('pet.tabChat') }}</button>
          <button :class="{ on: tab === 'settings' }" @click="tab = 'settings'">{{ $t('pet.tabSettings') }}</button>
          <button :class="{ on: tab === 'memory' }" @click="tab = 'memory'">{{ $t('pet.tabMemory') }}</button>
        </div>
      </div>
    </div>

    <!-- 对话 -->
    <div v-if="tab === 'chat'" class="pet-chat">
      <div ref="messagesWrap" class="pet-messages" @scroll="onMessagesScroll">
        <div v-if="messages.length === 0" class="pet-empty">{{ $t('pet.chatEmpty') }}</div>
        <div v-for="(m, i) in messages" :key="i" class="pet-msg" :class="m.role">
          <div class="pet-msg-role">
            {{ m.role === 'user' ? $t('pet.you') : $t('pet.pet') }}
            <button v-if="m.id && !sending" class="pet-msg-del" @click="delMessage(m)" :title="$t('common.delete')"><Icon name="trash" :size="11" /></button>
          </div>
          <div class="pet-msg-body">
            <!-- 用户互动消息：图标 + 动作名（互动提示词不展示，与 Chat 页互动图标一致） -->
            <div v-if="m.role === 'user' && m.actionIcon" class="pet-msg-action" :title="m.actionLabel">
              <Icon :name="m.actionIcon" :size="20" />
              <span>{{ m.actionLabel }}</span>
            </div>
            <div v-if="m.thinking" class="pet-thinking">
              <div class="pet-thinking-label">
                <span class="pet-thinking-dot"></span>
                {{ $t('pet.thinkingLabel') }}
              </div>
              <div class="pet-thinking-text">{{ m.thinking }}</div>
            </div>
            <div v-if="m.content">{{ m.content }}</div>
            <div v-else-if="!(m.role === 'user' && m.actionIcon) && sending && !m.thinking && m.role === 'assistant'" class="pet-waiting">
              <span class="pet-waiting-dots"><i></i><i></i><i></i></span>
              {{ $t('pet.thinking') }}
            </div>
          </div>
        </div>
      </div>
      <!-- 互动动作栏：与桌面桌宠圆环菜单同源（分类 → 动作） -->
      <div class="pet-interact">
        <div class="pet-interact-cats">
          <button
            v-for="c in petCats"
            :key="c.id"
            class="pet-interact-cat"
            :class="{ on: activeCat === c.id }"
            :title="c.label"
            @click="activeCat = activeCat === c.id ? '' : c.id"
          >
            <Icon :name="c.icon" :size="16" />
            <span class="pet-interact-cat-lbl">{{ c.label }}</span>
          </button>
        </div>
        <div v-if="activeCat" class="pet-interact-acts">
          <button
            v-for="a in actsOf(activeCat)"
            :key="a.id"
            class="pet-interact-act"
            :title="a.label"
            :disabled="sending"
            @click="sendAction(a)"
          >
            <Icon :name="a.icon" :size="15" />
            <span>{{ a.label }}</span>
          </button>
        </div>
      </div>
      <div class="pet-input-row">
        <input
          v-model="input"
          class="input pet-input"
          :placeholder="$t('pet.inputPlaceholder')"
          @keydown.enter="onInputEnter"
        />
        <button class="btn btn-icon" :class="{ 'btn-recording': recording }" @click="toggleRecording" :title="recording ? $t('pet.stopRecording') : $t('pet.startRecording')"><Icon name="mic" :size="14" /></button>
        <button class="btn btn-primary" @click="send"><Icon name="send" :size="14" /> {{ $t('pet.send') }}</button>
        <button v-if="sending" class="btn" @click="stopSend"><Icon name="x" :size="14" /> {{ $t('pet.stop') }}</button>
      </div>
      <!-- 状态条：心情/饱食/精力/清洁（与养成状态同源） -->
      <div v-if="petStateValues" class="pet-statebar">
        <div v-for="s in STATE_META" :key="s.key" class="pet-state-item" :title="$t(s.labelKey) + ' ' + petStateValues[s.key]">
          <Icon :name="s.icon" :size="13" />
          <div class="pet-state-bar"><div class="pet-state-fill" :class="'lv-' + s.key" :style="{ width: petStateValues[s.key] + '%' }"></div></div>
        </div>
      </div>
    </div>

    <!-- 设置 -->
    <div v-else-if="tab === 'settings'" class="pet-settings">
      <div class="settings-group">
        <div class="settings-group-title">{{ $t('pet.masterTitle') }}</div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.masterEnabled') }}</div><div class="d">{{ $t('pet.masterEnabledDesc') }}</div></div>
          <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="masterEnabled" :disabled="savingMaster" @change="toggleMaster" /><span class="toggle-slider"></span></label></div>
        </div>
      </div>

      <div class="settings-divider"></div>
      <div class="settings-group">
        <div class="settings-group-title">{{ $t('pet.personaTitle') }}</div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.persona') }}</div><div class="d">{{ $t('pet.personaDesc') }}</div></div>
          <div class="setting-ctl"><textarea v-model="cfg.persona" class="input pet-persona-input" rows="5"></textarea></div>
        </div>
      </div>

      <div class="settings-divider"></div>
      <div class="settings-group">
        <div class="settings-group-title">{{ $t('pet.apiTitle') }}</div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.apiUrl') }}</div><div class="d">{{ $t('pet.apiUrlDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-240" v-model="cfg.api_url" placeholder="https://api.openai.com/v1" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.apiKey') }}</div><div class="d">{{ cfg.api_key_set ? $t('pet.apiKeySet') : $t('pet.apiKeyDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-240" v-model="apiKeyDraft" type="password" :placeholder="cfg.api_key_set ? '••••••••' : 'sk-...'" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.apiModel') }}</div><div class="d">{{ $t('pet.apiModelDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-240" v-model="cfg.api_model" placeholder="gpt-4o-mini" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.apiFormat') }}</div><div class="d">{{ $t('pet.apiFormatDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-160" v-model="cfg.api_format" placeholder="openai / dashscope / anthropic" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.apiProvider') }}</div><div class="d">{{ $t('pet.apiProviderDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-160" v-model="cfg.api_provider" placeholder="openai / dashscope / anthropic" /></div>
        </div>
      </div>

      <div class="settings-divider"></div>
      <div class="settings-group">
        <div class="settings-group-title">{{ $t('pet.loopTitle') }}</div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.loopType') }}</div><div class="d">{{ $t('pet.loopTypeDesc') }}</div></div>
          <div class="setting-ctl">
            <select v-model="cfg.loop_type" class="input">
              <option v-for="l in LOOP_TYPES" :key="l.value" :value="l.value">{{ $t(l.labelKey) }}</option>
            </select>
          </div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.memoryEnabled') }}</div><div class="d">{{ $t('pet.memoryEnabledDesc') }}</div></div>
          <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="cfg.memory_enabled" /><span class="toggle-slider"></span></label></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.loopInterval') }}</div><div class="d">{{ $t('pet.loopIntervalDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-100" type="number" min="0" step="0.5" v-model.number="cfg.min_think_interval" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.loopToolsEnabled') }}</div><div class="d">{{ $t('pet.loopToolsEnabledDesc') }}</div></div>
          <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="cfg.tools_enabled" /><span class="toggle-slider"></span></label></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.loopHistory') }}</div><div class="d">{{ $t('pet.loopHistoryDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-100" type="number" min="1" max="16" v-model.number="cfg.max_history" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.loopMaxOutput') }}</div><div class="d">{{ $t('pet.loopMaxOutputDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-100" type="number" min="64" max="4096" step="64" v-model.number="cfg.max_output_tokens" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.loopRounds') }}</div><div class="d">{{ $t('pet.loopRoundsDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-100" type="number" min="0" max="50" v-model.number="cfg.max_loop_rounds" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.idleBackoffMax') }}</div><div class="d">{{ $t('pet.idleBackoffMaxDesc') }}</div></div>
          <div class="setting-ctl"><input class="input w-100" type="number" min="1" max="64" step="1" v-model.number="cfg.idle_backoff_max" /></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.voiceEnabled') }}</div><div class="d">{{ $t('pet.voiceEnabledDesc') }}</div></div>
          <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="cfg.voice_enabled" /><span class="toggle-slider"></span></label></div>
        </div>
        <div class="setting-row">
          <div class="lbl"><div class="t">{{ $t('pet.realtimeVoice') }}</div><div class="d">{{ $t('pet.realtimeVoiceDesc') }}</div></div>
          <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="cfg.realtime_voice" /><span class="toggle-slider"></span></label></div>
        </div>
      </div>

      <div class="pet-settings-actions">
        <button class="btn btn-primary" :disabled="savingCfg" @click="saveConfig">{{ savingCfg ? $t('pet.saving') : $t('pet.save') }}</button>
        <button class="btn" @click="clearHistory">{{ $t('pet.clearHistory') }}</button>
      </div>
    </div>

    <!-- 记忆 -->
    <div v-else class="pet-memory">
      <div class="pet-memory-add">
        <select v-model="memKind" class="input" style="width:130px">
          <option v-for="k in MEM_KINDS" :key="k.value" :value="k.value">{{ $t(k.labelKey) }}</option>
        </select>
        <input v-model="memText" class="input" style="flex:1" :placeholder="$t('pet.memPlaceholder')" @keydown.enter="addMemory" />
        <button class="btn btn-primary" @click="addMemory"><Icon name="plus" :size="14" /> {{ $t('pet.memAdd') }}</button>
      </div>
      <div v-if="memLoading" class="pet-empty">{{ $t('common.loading') }}</div>
      <div v-else-if="memories.length === 0" class="pet-empty">{{ $t('pet.memEmpty') }}</div>
      <div v-for="m in memories" :key="m.id" class="pet-mem-row">
        <span class="badge">{{ m.kind }}</span>
        <span class="pet-mem-text">{{ m.text }}</span>
        <span class="pet-mem-time">{{ m.time }}</span>
        <button class="pet-mem-del" @click="delMemory(m.id)" :title="$t('common.delete')"><Icon name="trash" :size="13" /></button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pet-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.pet-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color, #E4E3DD);
}
.pet-header-left { display: flex; align-items: center; gap: 8px; }
.pet-title { font-weight: 600; display: flex; align-items: center; gap: 6px; }
.pet-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(88,166,255,.12);
  color: var(--accent, #4a7dff);
}
.pet-chat { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.pet-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pet-empty { color: var(--text-muted, #6B7280); text-align: center; padding: 40px 0; }
.pet-msg { max-width: 78%; display: flex; flex-direction: column; gap: 2px; }
.pet-msg.user { align-self: flex-end; align-items: flex-end; }
.pet-msg.assistant { align-self: flex-start; align-items: flex-start; }
.pet-msg-role { font-size: 11px; color: var(--text-muted, #6B7280); display: flex; align-items: center; gap: 6px; }
.pet-msg-del {
  border: none; background: none; cursor: pointer;
  padding: 0 2px; color: var(--text-muted, #6B7280); opacity: .5;
  display: inline-flex; align-items: center;
}
.pet-msg-del:hover { opacity: 1; color: #f85149; }
.pet-msg-body {
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--card-color, #FFFFFF);
  border: 1px solid var(--border-color, #E4E3DD);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}
.pet-msg.user .pet-msg-body { background: rgba(88,166,255,.12); }
.pet-thinking {
  margin-bottom: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(99,102,241,.06);
  border-left: 3px solid rgba(99,102,241,.4);
  font-size: 12px;
  color: var(--text-secondary, #6B7280);
  white-space: pre-wrap;
  word-break: break-word;
}
.pet-thinking-label {
  font-weight: 600;
  margin-bottom: 4px;
  opacity: .85;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .5px;
}
.pet-thinking-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: rgba(99,102,241,.6);
  animation: pet-thinking-pulse 1.2s ease-in-out infinite;
}
@keyframes pet-thinking-pulse {
  0%, 100% { opacity: .4; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
.pet-thinking-text { max-height: 180px; overflow-y: auto; line-height: 1.5; }
.pet-waiting {
  color: var(--text-secondary, #6B7280);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.pet-waiting-dots { display: inline-flex; gap: 3px; }
.pet-waiting-dots i {
  width: 5px; height: 5px;
  border-radius: 50%;
  background: var(--text-secondary, #6B7280);
  animation: pet-waiting-bounce 1.4s ease-in-out infinite;
}
.pet-waiting-dots i:nth-child(2) { animation-delay: .2s; }
.pet-waiting-dots i:nth-child(3) { animation-delay: .4s; }
@keyframes pet-waiting-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: .4; }
  30% { transform: translateY(-4px); opacity: 1; }
}
.pet-input-row { display: flex; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--border-color, #E4E3DD); }
.pet-input { flex: 1; }
.btn-recording { background: var(--error-bg, rgba(239, 68, 68, 0.15)); color: var(--error, #ef4444); animation: pulse-recording 1.5s ease-in-out infinite; }
@keyframes pulse-recording { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.pet-settings { flex: 1; overflow-y: auto; padding: 16px; }
.pet-settings .settings-group { margin-bottom: 8px; }
.pet-persona-input { width: 100%; resize: vertical; font-family: inherit; }
.pet-settings-actions { display: flex; gap: 8px; margin-top: 16px; }
.pet-memory { flex: 1; overflow-y: auto; padding: 16px; }
.pet-memory-add { display: flex; gap: 8px; margin-bottom: 14px; }
.pet-mem-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--card-color, #FFFFFF);
  border: 1px solid var(--border-color, #E4E3DD);
  margin-bottom: 6px;
}
.pet-mem-text { flex: 1; word-break: break-word; }
.pet-mem-time { font-size: 11px; color: var(--text-muted, #6B7280); white-space: nowrap; }
.pet-mem-del { border: none; background: none; cursor: pointer; color: var(--text-muted, #6B7280); }
.pet-mem-del:hover { color: #f85149; }

/* ── 互动动作栏 ── */
.pet-interact { border-top: 1px solid var(--border-color, #E4E3DD); padding: 8px 12px 0; }
.pet-interact-cats { display: flex; gap: 6px; flex-wrap: wrap; }
.pet-interact-cat {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border-color, #E4E3DD);
  background: var(--card-color, #FFFFFF); color: var(--text-muted, #6B7280);
  font-size: 12px;
}
.pet-interact-cat:hover { color: var(--accent, #4a7dff); border-color: var(--accent, #4a7dff); }
.pet-interact-cat.on { color: var(--accent, #4a7dff); border-color: var(--accent, #4a7dff); background: rgba(88,166,255,.08); }
.pet-interact-acts { display: flex; gap: 6px; flex-wrap: wrap; padding: 8px 0 4px; }
.pet-interact-act {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border-radius: 999px; cursor: pointer;
  border: 1px dashed var(--border-color, #E4E3DD);
  background: transparent; color: var(--text-color, #1F2328);
  font-size: 12px;
}
.pet-interact-act:hover:not(:disabled) { border-color: var(--accent, #4a7dff); color: var(--accent, #4a7dff); }
.pet-interact-act:disabled { opacity: .5; cursor: not-allowed; }

/* 用户互动消息：图标 + 动作名 */
.pet-msg-action {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px;
  background: rgba(88,166,255,.1); color: var(--accent, #4a7dff);
  font-size: 13px;
}

/* ── 状态条 ── */
.pet-statebar {
  display: flex; gap: 14px; align-items: center;
  padding: 8px 16px 10px;
  border-top: 1px solid var(--border-color, #E4E3DD);
}
.pet-state-item { display: flex; align-items: center; gap: 5px; color: var(--text-muted, #6B7280); flex: 1; min-width: 0; }
.pet-state-bar { flex: 1; height: 5px; border-radius: 999px; background: var(--border-color, #E4E3DD); overflow: hidden; }
.pet-state-fill { height: 100%; border-radius: 999px; transition: width .4s ease; }
.pet-state-fill.lv-mood { background: #f7b955; }
.pet-state-fill.lv-satiety { background: #58a55c; }
.pet-state-fill.lv-energy { background: #4a7dff; }
.pet-state-fill.lv-cleanliness { background: #b07fe8; }
</style>
