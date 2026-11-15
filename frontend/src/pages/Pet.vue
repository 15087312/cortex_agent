<script setup>
defineOptions({ name: 'Pet' })
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useToastStore } from '@/stores/toast.js'
import { endpoints, request } from '@/api.js'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'
import { useConfirm } from '@/composables/useDialog.js'
import { pickAudioMimeType } from '@/utils/audio.js'

const { t } = useI18n()
const toast = useToastStore()
const confirm = useConfirm()

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
let autoStartTimer = null // 开启总开关后延迟启动自动回复（等首屏历史/配置就绪）
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
    const mime = pickAudioMimeType()
    petMediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    petAudioChunks = []
    petMediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) petAudioChunks.push(e.data) }
    petMediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop())
      const blob = new Blob(petAudioChunks, { type: petMediaRecorder?.mimeType || mime || 'audio/webm' })
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
async function pumpSse(res, { last, onRoundStart, onError, onFallback } = {}) {
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
        if (evt.fallback && onFallback) onFallback()
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
      } else if (evt.tool && evt.tool.call_id != null) {
        const toolMsg = {
          _id: 't' + (++_uid),
          role: 'assistant',
          content: '',
          tool: evt.tool,
        }
        messages.value.push(toolMsg)
        last = toolMsg
        scrollDown()
      } else if (evt.error) {
        if (onError) onError(evt.error)
        last.content = (last.content ? last.content + '\n' : '') + t('pet.error') + '：' + evt.error
        scrollDown()
      }
    })
  }
  return last
}

async function send(autoText) {
  const isAuto = autoText === '' // 空字符串 = 主动思考模式
  if (!masterEnabled.value) return // 桌宠关闭：不自动回复，也不接受手动对话
  // 任何一次发送都优先于“待启动的自动首轮”，避免重复触发
  if (autoStartTimer) { clearTimeout(autoStartTimer); autoStartTimer = null }
  if (isAuto && stopAuto) return
  if (!isAuto) {
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
  // 总开关开启且未设轮次上限 → 按 min_think_interval 持续自动回复
  const shouldContinue = () => masterEnabled.value && cfg.value.max_loop_rounds === 0
  try {
    const res = await fetch('/api/stream/pet/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ text }),
      signal: ctl.signal,
    })
    if (!res.ok || !res.body) throw new Error('HTTP ' + res.status)
    let streamError = ''
    let fallbackNotified = false
    last = await pumpSse(res, {
      last,
      onRoundStart: () => {
        // 一轮完整回复结束：第一轮关闭"接收中"
        if (roundCount === 0) sending.value = false
        roundCount++
      },
      onError: (msg) => { streamError = msg },
      onFallback: () => {
        if (fallbackNotified) return
        fallbackNotified = true
        toast.show(t('pet.apiFallback'), 'info')
      },
    })
    if (streamError) {
      // 模型/API 失败：保留本地气泡（错误原因已写入），不要用历史覆盖；
      // 同时停掉自动续聊，避免余额不足等错误被每 30s 反复重试。
      stopAuto = true
      if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
      if (autoStartTimer) { clearTimeout(autoStartTimer); autoStartTimer = null }
      if (!isAuto) toast.show(t('pet.error') + '：' + streamError, 'error')
    } else {
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
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      if (!last.content) last.content = t('pet.sendFailed')
      if (!isAuto) toast.show(t('pet.sendFailed'), 'error')
      stopAuto = true
      if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
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

// ── 人设（每个人设独立会话；编辑态与启用态分开，避免误切会话）──
const personas = ref([])
const activePersonaId = ref(null)   // 当前启用的人设（引擎正在用）
const editingPersonaId = ref(null)  // 编辑器里正在编辑的人设
const headerPersonaId = ref(null)   // 页眉快捷切换
const personaNameDraft = ref('')
const personaPromptDraft = ref('')
const personaCreating = ref(false)  // 新建模式（不污染当前人设内容）
const personaSaving = ref(false)

const personaDirty = computed(() => {
  if (personaCreating.value) {
    return !!(personaNameDraft.value.trim() || personaPromptDraft.value.trim())
  }
  const cur = personas.value.find((p) => p.id === editingPersonaId.value)
  if (!cur) return false
  return personaNameDraft.value !== (cur.name || '') || personaPromptDraft.value !== (cur.prompt || '')
})

async function loadMasterEnabled() {
  try {
    // 后端只提供 GET /config（整体配置），无 GET /config/{key}；
    // 读整体配置才能拿到上次保存的桌宠总开关（刷新/重启后遵循上次设置）。
    const r = await endpoints.config()
    const d = r?.data || {}
    masterEnabled.value = d.DESKTOP_PET_ENABLED !== false
  } catch { /* 静默，保持默认 true */ }
}

// 打开/关闭桌面宠物（进程级：拉起/停止窗口进程 + 同步引擎总开关）
// 窗口进程由后端 window_supervisor 统一持有：崩溃自动重启、Web 页可见状态。
function stopAutoLoop() {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  if (autoStartTimer) { clearTimeout(autoStartTimer); autoStartTimer = null }
  stopAuto = true
  if (abortCtl.value) abortCtl.value.abort()
  stopPetAudio()
}

function startAutoLoop() {
  if (!masterEnabled.value) return
  stopAuto = false
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  if (autoStartTimer) clearTimeout(autoStartTimer)
  // 等首屏历史/配置加载完再开始，避免与初始渲染竞态
  autoStartTimer = setTimeout(() => {
    autoStartTimer = null
    if (masterEnabled.value && !sending.value) send('')
  }, 200)
}

async function setMasterEnabled(v) {
  if (savingMaster.value || v === masterEnabled.value) return true
  savingMaster.value = true
  try {
    if (v) {
      const r = await endpoints.updateConfig('DESKTOP_PET_ENABLED', true)
      if (r?.success === false) throw new Error('config')
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
      toast.show(t('pet.disabled'), 'success')
      if (abortCtl.value) abortCtl.value.abort() // 关闭时中断当前思考
    }
    masterEnabled.value = v
    if (v) startAutoLoop()
    else stopAutoLoop()
    await loadWindowStatus()
    return true
  } catch {
    toast.show(t('pet.saveFailed'), 'error')
    return false
  } finally {
    savingMaster.value = false
  }
}

// 开关失败时把 DOM 勾选状态回滚到真实值（:checked 绑定在值未变化时不会重绘）
async function onMasterToggle(e) {
  const ok = await setMasterEnabled(e.target.checked)
  if (!ok) e.target.checked = masterEnabled.value
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

async function confirmClearHistory() {
  if (!(await confirm(t('pet.clearHistoryConfirm')))) return
  stopAuto = true
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  if (autoStartTimer) { clearTimeout(autoStartTimer); autoStartTimer = null }
  if (abortCtl.value) abortCtl.value.abort()
  stopPetAudio()
  await clearHistory()
}

// ── 人设管理：列表 + 独立编辑器（编辑不切会话，启用才切）──
function _fillEditor(p) {
  editingPersonaId.value = p?.id ?? null
  personaNameDraft.value = p?.name || ''
  personaPromptDraft.value = p?.prompt || ''
  personaCreating.value = false
}

async function loadPersonas() {
  try {
    const r = await endpoints.petPersonas()
    personas.value = r?.data?.items || []
    activePersonaId.value = r?.data?.active_id ?? (personas.value[0]?.id ?? null)
    headerPersonaId.value = activePersonaId.value
    // 编辑器跟随：没在新建、且编辑对象不存在时，回到当前启用的人设
    if (!personaCreating.value && !personas.value.some((p) => p.id === editingPersonaId.value)) {
      _fillEditor(personas.value.find((p) => p.id === activePersonaId.value) || personas.value[0])
    }
  } catch { /* 静默 */ }
}

async function editPersona(p) {
  if (!p) return
  if (!personaCreating.value && p.id === editingPersonaId.value) return
  if (personaDirty.value && !(await confirm(t('pet.personaDiscardConfirm')))) return
  _fillEditor(p)
}

function startCreatePersona() {
  editingPersonaId.value = null
  personaNameDraft.value = ''
  personaPromptDraft.value = ''
  personaCreating.value = true
}

async function cancelPersonaEdit() {
  if (personaDirty.value && !(await confirm(t('pet.personaDiscardConfirm')))) return
  _fillEditor(personas.value.find((p) => p.id === activePersonaId.value) || personas.value[0])
}

async function savePersona() {
  const name = personaNameDraft.value.trim()
  const prompt = personaPromptDraft.value.trim()
  if (!name) { toast.show(t('pet.personaEmptyName'), 'error'); return }
  if (!prompt) { toast.show(t('pet.personaPromptEmpty'), 'error'); return }
  personaSaving.value = true
  try {
    if (personaCreating.value) {
      const r = await endpoints.createPetPersona(name, prompt)
      if (!r?.success) { toast.show(t('pet.personaDuplicate'), 'error'); return }
      await loadPersonas()
      _fillEditor(r.data)
      // 新建后直接启用：少一步点击，也避免“建好了却仍用旧人设”的困惑
      await activatePersona(r.data.id, { silent: true })
      toast.show(t('pet.personaCreated'), 'success')
    } else {
      if (!editingPersonaId.value) return
      const r = await endpoints.updatePetPersona(editingPersonaId.value, { name, prompt })
      if (r?.success === false) { toast.show(t('pet.personaDuplicate'), 'error'); return }
      await loadPersonas()
      await loadConfig()
      toast.show(t('pet.personaSaved'), 'success')
    }
  } catch { toast.show(t('pet.saveFailed'), 'error') }
  finally { personaSaving.value = false }
}

// 启用人设：停掉旧人设的流式输出与自动循环，避免串会话
async function activatePersona(pid, { silent = false } = {}) {
  if (!pid) return
  stopAuto = true
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  if (autoStartTimer) { clearTimeout(autoStartTimer); autoStartTimer = null }
  if (abortCtl.value) abortCtl.value.abort()
  stopPetAudio()
  const r = await endpoints.activatePetPersona(pid)
  if (r?.success === false) throw new Error(r?.error || 'activate failed')
  activePersonaId.value = pid
  headerPersonaId.value = pid
  await loadConfig()
  await loadHistory()
  if (!silent) {
    const active = personas.value.find((p) => p.id === pid)
    toast.show(t('pet.personaSwitched', { name: active?.name || '' }), 'success')
  }
  // 恢复自动回复，但等一个正常思考间隔，让用户先看到该人设的既有会话
  if (masterEnabled.value) {
    stopAuto = false
    if (autoTimer) clearTimeout(autoTimer)
    autoTimer = setTimeout(() => {
      autoTimer = null
      if (!sending.value) send('')
    }, (cfg.value.min_think_interval || 5) * 1000)
  }
}

// 页眉下拉：切换到该人设（失败回滚选中项）
async function switchPersona() {
  const pid = Number(headerPersonaId.value)
  if (!pid || pid === activePersonaId.value) return
  try {
    await activatePersona(pid)
    if (!personaDirty.value) _fillEditor(personas.value.find((p) => p.id === pid))
  } catch {
    headerPersonaId.value = activePersonaId.value
    toast.show(t('pet.personaSwitchFailed'), 'error')
  }
}

async function deletePersona(p) {
  if (!p || personas.value.length <= 1) {
    toast.show(t('pet.personaLastCannotDelete'), 'error')
    return
  }
  if (!(await confirm(t('pet.personaDeleteConfirm', { name: p.name })))) return
  try {
    const r = await endpoints.deletePetPersona(p.id)
    if (!r?.success) throw new Error(r?.error || 'delete failed')
    const wasActive = p.id === activePersonaId.value
    if (p.id === editingPersonaId.value) editingPersonaId.value = null
    await loadPersonas()
    if (wasActive) {
      await activatePersona(activePersonaId.value, { silent: true })
      headerPersonaId.value = activePersonaId.value
    }
    toast.show(t('pet.personaDeleted'), 'success')
  } catch { toast.show(t('pet.saveFailed'), 'error') }
}

// ── 显示：<say> 是发声标记，气泡里不展示标签 ──
function stripSay(text) {
  return String(text || '').replace(/<\/?\s*say\s*>/gi, '').trim()
}

async function activateEditingPersona() {
  try {
    await activatePersona(editingPersonaId.value)
  } catch {
    toast.show(t('pet.personaSwitchFailed'), 'error')
  }
}

// ── 实时监听状态（后端麦克风）──
const voiceStatus = ref({ running: false, state: 'idle', tts_backend: '', pending: 0, cooldown: 0, last_error: '' })
let voicePollTimer = null

const voiceStateLabel = computed(() => {
  // 未运行时不能显示「空闲」——那会让人以为在听、只是没说话
  if (!voiceStatus.value.running) return t('pet.voiceOff')
  const s = voiceStatus.value.state
  return t(s === 'listening' ? 'pet.voiceListening' : s === 'transcribing' ? 'pet.voiceTranscribing' : 'pet.voiceIdle')
})

async function loadVoiceStatus() {
  try {
    const r = await request('GET', '/stream/pet/voice/status')
    if (r?.data) voiceStatus.value = { ...voiceStatus.value, ...r.data }
  } catch { /* 静默 */ }
}

// ── 互动（与桌面桌宠圆环菜单同源：/stream/pet/actions + /state）──
const petCats = ref([])      // [{id,label,icon}]
const petActs = ref([])      // [{id,label,category,icon}]
const activeCat = ref('')    // 当前展开的动作分类

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

function actsOf(catId) {
  return petActs.value.filter((a) => a.category === catId)
}

async function sendAction(action) {
  if (sending.value) return
  activeCat.value = ''
  stopAuto = false // 互动算用户发言：自动续聊链路恢复
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
  await loadConfig()
  await loadPersonas()
  await loadHistory()
  await loadMasterEnabled()
  loadMemory()
  loadActions()
  loadWindowStatus()
  loadVoiceStatus()
  // 窗口进程状态轮询（10s）：崩溃/重启后页眉徽标同步
  if (windowPollTimer) clearInterval(windowPollTimer)
  windowPollTimer = setInterval(loadWindowStatus, 10000)
  // 实时监听状态轮询（5s）：是否在听 / 待唤醒段数 / 发声后端
  if (voicePollTimer) clearInterval(voicePollTimer)
  voicePollTimer = setInterval(loadVoiceStatus, 5000)
  // 总开关开启 → 自动进入定时回复循环
  if (masterEnabled.value) startAutoLoop()
})

onUnmounted(() => {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null }
  if (autoStartTimer) { clearTimeout(autoStartTimer); autoStartTimer = null }
  if (windowPollTimer) { clearInterval(windowPollTimer); windowPollTimer = null }
  if (voicePollTimer) { clearInterval(voicePollTimer); voicePollTimer = null }
})
</script>

<template>
  <div class="pet-page">
    <div class="pet-header">
      <div class="pet-header-left">
        <span class="pet-title"><Icon name="bot" :size="16" /> {{ $t('nav.pet') }}</span>
        <span class="pet-badge">{{ cfg.loop_type }}</span>
        <select
          v-if="personas.length"
          v-model="headerPersonaId"
          class="input pet-persona-select"
          :title="$t('pet.personaSwitch')"
          @change="switchPersona"
        >
          <option v-for="p in personas" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
      </div>
      <div class="pet-header-right">
        <span v-if="petWindow.running" class="badge badge-green" :title="'pid ' + petWindow.pid">{{ $t('pet.windowRunning') }} · {{ petWindow.pid }}</span>
        <span v-else class="badge badge-gray">{{ $t('pet.windowStopped') }}</span>
        <button
          v-if="tab === 'chat' && messages.length"
          class="chat-btn-icon"
          :title="$t('pet.clearHistory')"
          @click="confirmClearHistory"
        ><Icon name="trash" :size="14" /></button>
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
            <div v-if="m.content" class="pet-msg-text">{{ stripSay(m.content) }}</div>
            <div v-else-if="!(m.role === 'user' && m.actionIcon) && sending && !m.thinking && m.role === 'assistant'" class="pet-waiting">
              <span class="pet-waiting-dots"><i></i><i></i><i></i></span>
              {{ $t('pet.thinking') }}
            </div>
            <div v-if="m.tool" class="pet-tool">
              <div class="pet-tool-label">
                <span class="pet-tool-dot"></span>
                {{ $t('pet.toolLabel') }} {{ m.tool.name }}
              </div>
              <div class="pet-tool-body">
                <div class="pet-tool-progress">{{ $t('pet.toolRunning') }}</div>
                <pre v-if="m.tool.args && Object.keys(m.tool.args).length" class="pet-tool-args">{{ JSON.stringify(m.tool.args, null, 2) }}</pre>
              </div>
            </div>
          </div>
          <div class="pet-tool-timeline" v-if="m.tool">
            <span class="pet-tool-timeline-dot"></span>
            {{ $t('pet.toolTimeline') }} {{ m.tool.name }}
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
          :disabled="!masterEnabled"
          :placeholder="masterEnabled ? $t('pet.inputPlaceholder') : $t('pet.masterDisabledHint')"
          @keydown.enter="onInputEnter"
        />
        <button class="btn btn-icon" :class="{ 'btn-recording': recording }" :disabled="!masterEnabled" @click="toggleRecording" :title="recording ? $t('pet.stopRecording') : $t('pet.startRecording')"><Icon name="mic" :size="14" /></button>
        <button class="btn btn-primary" :disabled="!masterEnabled" @click="send"><Icon name="send" :size="14" /> {{ $t('pet.send') }}</button>
        <button v-if="sending" class="btn" @click="stopSend"><Icon name="x" :size="14" /> {{ $t('pet.stop') }}</button>
      </div>
    </div>

    <!-- 设置 -->
    <div v-else-if="tab === 'settings'" class="pet-settings">
      <div class="settings-group">
        <div class="settings-group-title">{{ $t('pet.masterTitle') }}</div>
        <div class="setting-row">
          <div class="lbl">
            <div class="t">{{ $t('pet.masterEnabled') }}</div>
            <div class="d">{{ masterEnabled ? $t('pet.masterEnabledDesc') : $t('pet.masterDisabledDesc') }}</div>
          </div>
          <div class="setting-ctl">
            <label class="toggle-switch">
              <input
                type="checkbox"
                data-test="pet-master-toggle"
                :checked="masterEnabled"
                :disabled="savingMaster"
                @change="onMasterToggle"
              />
              <span class="toggle-slider"></span>
            </label>
          </div>
        </div>
      </div>

      <div class="settings-divider"></div>
      <div class="settings-group persona-manager">
        <div class="settings-group-title">{{ $t('pet.personaManage') }}</div>
        <div class="persona-grid">
          <!-- 左：人设列表（点击只编辑，不改会话） -->
          <div class="persona-list">
            <button
              v-for="p in personas"
              :key="p.id"
              class="persona-item"
              :class="{ on: !personaCreating && p.id === editingPersonaId, using: p.id === activePersonaId }"
              data-test="persona-item"
              @click="editPersona(p)"
            >
              <span class="persona-item-name">{{ p.name }}</span>
              <span v-if="p.id === activePersonaId" class="persona-item-tag">{{ $t('pet.personaInUse') }}</span>
            </button>
            <button class="persona-add" data-test="persona-add" @click="startCreatePersona">
              <Icon name="plus" :size="13" /> {{ $t('pet.personaNew') }}
            </button>
          </div>

          <!-- 右：编辑器（新建与编辑分开，不互相污染） -->
          <div class="persona-editor">
            <div class="persona-editor-head">
              <span class="persona-editor-title">
                {{ personaCreating ? $t('pet.personaNewTitle') : $t('pet.personaEditTitle') }}
              </span>
              <span v-if="personaDirty" class="persona-dirty" data-test="persona-dirty">{{ $t('pet.personaDirty') }}</span>
            </div>
            <label class="persona-field">
              <span class="persona-field-lbl">{{ $t('pet.personaNameLabel') }}</span>
              <input
                v-model="personaNameDraft"
                class="input"
                data-test="persona-name-input"
                maxlength="40"
                :placeholder="$t('pet.personaNamePlaceholder')"
              />
            </label>
            <label class="persona-field">
              <span class="persona-field-lbl">{{ $t('pet.personaPromptLabel') }}</span>
              <textarea
                v-model="personaPromptDraft"
                class="input pet-persona-input"
                data-test="persona-prompt-input"
                rows="8"
                :placeholder="$t('pet.personaPromptPlaceholder')"
              ></textarea>
            </label>
            <div class="persona-actions">
              <button class="btn btn-primary" data-test="persona-save" :disabled="personaSaving" @click="savePersona">
                {{ personaCreating ? $t('pet.personaCreate') : $t('pet.personaSaveChanges') }}
              </button>
              <button class="btn" data-test="persona-cancel" :disabled="personaSaving" @click="cancelPersonaEdit">{{ $t('pet.personaCancel') }}</button>
              <button
                v-if="!personaCreating && editingPersonaId && editingPersonaId !== activePersonaId"
                class="btn"
                data-test="persona-activate"
                :disabled="personaSaving"
                @click="activateEditingPersona"
              >{{ $t('pet.personaActivate') }}</button>
              <button
                v-if="!personaCreating && editingPersonaId && personas.length > 1"
                class="btn btn-danger"
                data-test="persona-delete"
                :disabled="personaSaving"
                @click="deletePersona(personas.find((p) => p.id === editingPersonaId))"
              >{{ $t('pet.personaDelete') }}</button>
            </div>
            <div class="persona-hint">{{ $t('pet.personaDesc') }}</div>
          </div>
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
          <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="cfg.realtime_voice" data-test="realtime-voice-toggle" /><span class="toggle-slider"></span></label></div>
        </div>
        <div class="setting-row voice-status">
          <div class="lbl">
            <div class="t">{{ $t('pet.voiceStatusTitle') }}</div>
            <div class="d voice-status-line">
              <span class="voice-chip" :class="voiceStatus.running ? 'on' : 'off'" data-test="voice-state">{{ voiceStateLabel }}</span>
              <span v-if="voiceStatus.tts_backend">{{ $t('pet.voiceTts') }} {{ voiceStatus.tts_backend }}</span>
              <span v-if="voiceStatus.pending">{{ $t('pet.voicePending', { n: voiceStatus.pending }) }}</span>
              <span>{{ $t('pet.voiceCooldownHint', { sec: voiceStatus.cooldown || cfg.min_think_interval || 0 }) }}</span>
            </div>
            <div v-if="voiceStatus.last_error" class="voice-err">{{ $t('pet.voiceMicError', { err: voiceStatus.last_error }) }}</div>
            <div class="voice-say-hint">{{ $t('pet.sayHint') }}</div>
          </div>
        </div>
      </div>

      <div class="pet-settings-actions">
        <button class="btn btn-primary" :disabled="savingCfg" @click="saveConfig">{{ savingCfg ? $t('pet.saving') : $t('pet.save') }}</button>
        <button class="btn" @click="confirmClearHistory">{{ $t('pet.clearHistory') }}</button>
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
  border-bottom: 1px solid var(--border-light);
}
.pet-header-left { display: flex; align-items: center; gap: 8px; }
.pet-header-right { display: flex; align-items: center; gap: 8px; }
.pet-title { font-weight: 600; display: flex; align-items: center; gap: 6px; }
.pet-persona-select { width: 112px; padding: 3px 8px; font-size: 12px; }
.pet-msg-text { white-space: pre-wrap; }
/* ── 人设管理：左列表 + 右编辑器 ── */
.persona-grid { display: grid; grid-template-columns: 200px 1fr; gap: 14px; align-items: start; }
.persona-list { display: flex; flex-direction: column; gap: 6px; }
.persona-item {
  display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
  padding: 8px 10px; border: 1px solid var(--border-light); border-radius: 8px;
  background: var(--bg-secondary); color: var(--text); cursor: pointer; font-size: 13px;
}
.persona-item:hover { border-color: var(--border); }
.persona-item.on { border-color: var(--primary); background: var(--bg); }
.persona-item-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.persona-item-tag {
  font-size: 10px; padding: 1px 6px; border-radius: 6px;
  background: var(--info-bg); color: var(--info); flex-shrink: 0;
}
.persona-add {
  display: flex; align-items: center; justify-content: center; gap: 4px;
  padding: 7px 10px; border: 1px dashed var(--border); border-radius: 8px;
  background: none; color: var(--text-muted); cursor: pointer; font-size: 12px;
}
.persona-add:hover { color: var(--primary); border-color: var(--primary); }
.persona-editor { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.persona-editor-head { display: flex; align-items: center; gap: 8px; }
.persona-editor-title { font-size: 13px; font-weight: 600; }
.persona-dirty { font-size: 11px; color: var(--warning, #d97706); }
.persona-field { display: flex; flex-direction: column; gap: 4px; }
.persona-field-lbl { font-size: 12px; color: var(--text-muted); }
.persona-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.persona-hint { font-size: 11px; color: var(--text-muted); }
/* ── 实时监听状态 ── */
.voice-status-line { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.voice-chip { font-size: 11px; padding: 1px 8px; border-radius: 999px; }
.voice-chip.on { background: var(--success-bg, #dcfce7); color: var(--success, #15803d); }
.voice-chip.off { background: var(--border-light); color: var(--text-muted); }
.voice-err { font-size: 11px; color: var(--danger, #dc2626); margin-top: 4px; }
.voice-say-hint { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.pet-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--info-bg);
  color: var(--info);
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
.pet-empty { color: var(--text-muted); text-align: center; padding: 40px 0; }
.pet-msg { max-width: 78%; display: flex; flex-direction: column; gap: 2px; }
.pet-msg.user { align-self: flex-end; align-items: flex-end; }
.pet-msg.assistant { align-self: flex-start; align-items: flex-start; }
.pet-msg-role { font-size: 11px; color: var(--text-muted); display: flex; align-items: center; gap: 6px; }
.pet-msg-del {
  border: none; background: none; cursor: pointer;
  padding: 0 2px; color: var(--text-muted); opacity: .5;
  display: inline-flex; align-items: center;
}
.pet-msg-del:hover { opacity: 1; color: var(--danger); }
.pet-msg-body {
  padding: 8px 12px;
  border-radius: 10px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}
.pet-msg.user .pet-msg-body { background: var(--bg-tertiary); border-color: var(--border-light); }
.pet-thinking {
  margin-bottom: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--bg-tertiary);
  border-left: 2px solid var(--purple);
  font-size: 12px;
  color: var(--text-secondary);
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
  background: var(--purple);
  animation: pet-thinking-pulse 1.2s ease-in-out infinite;
}
@keyframes pet-thinking-pulse {
  0%, 100% { opacity: .4; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1.2); }
}
.pet-thinking-text { max-height: 180px; overflow-y: auto; line-height: 1.5; }
.pet-waiting {
  color: var(--text-secondary);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.pet-waiting-dots { display: inline-flex; gap: 3px; }
.pet-waiting-dots i {
  width: 5px; height: 5px;
  border-radius: 50%;
  background: var(--text-secondary);
  animation: pet-waiting-bounce 1.4s ease-in-out infinite;
}
.pet-waiting-dots i:nth-child(2) { animation-delay: .2s; }
.pet-waiting-dots i:nth-child(3) { animation-delay: .4s; }
@keyframes pet-waiting-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: .4; }
  30% { transform: translateY(-4px); opacity: 1; }
}
.pet-input-row { display: flex; min-width: 0; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--border-light); }
.pet-input { flex: 1 1 0; width: 0; min-width: 0; }
.btn-recording { background: var(--danger-bg); color: var(--danger); animation: pulse-recording 1.5s ease-in-out infinite; }
@keyframes pulse-recording { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.pet-settings { flex: 1; overflow-y: auto; padding: 16px; }
.pet-settings .settings-group { margin-bottom: 8px; }
.pet-persona-input { width: 100%; resize: vertical; font-family: inherit; }
.pet-settings-actions { display: flex; gap: 8px; margin-top: 16px; }
.pet-memory { flex: 1; overflow-y: auto; padding: 16px; }
.pet-memory-add { display: flex; min-width: 0; gap: 8px; margin-bottom: 14px; }
.pet-memory-add .input { min-width: 0; }
.pet-mem-row {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  margin-bottom: 6px;
}
.pet-mem-text { flex: 1; min-width: 0; word-break: break-word; }
.pet-mem-time { flex-shrink: 0; font-size: 11px; color: var(--text-muted); white-space: nowrap; }
.pet-mem-del { border: none; background: none; cursor: pointer; color: var(--text-muted); }
.pet-mem-del:hover { color: var(--danger); }

/* ── 互动动作栏 ── */
.pet-interact { border-top: 1px solid var(--border-light); padding: 8px 12px 0; }
.pet-interact-cats { display: flex; gap: 6px; flex-wrap: wrap; }
.pet-interact-cat {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid var(--border-light);
  background: var(--bg-secondary); color: var(--text-muted);
  font-size: 12px;
}
.pet-interact-cat:hover { color: var(--text-primary); border-color: var(--border-accent); }
.pet-interact-cat.on { color: var(--info); border-color: var(--info); background: var(--info-bg); }
.pet-interact-acts { display: flex; gap: 6px; flex-wrap: wrap; padding: 8px 0 4px; }
.pet-interact-act {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 10px; border-radius: 999px; cursor: pointer;
  border: 1px dashed var(--border-light);
  background: transparent; color: var(--text-primary);
  font-size: 12px;
}
.pet-interact-act:hover:not(:disabled) { border-color: var(--border-accent); color: var(--text-primary); }
.pet-interact-act:disabled { opacity: .5; cursor: not-allowed; }

/* 用户互动消息：图标 + 动作名 */
.pet-msg-action {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px;
  background: var(--info-bg); color: var(--info);
  font-size: 13px;
}
</style>
