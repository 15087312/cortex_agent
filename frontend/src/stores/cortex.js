// Cortex Store → :8000 (对话+打字机+重试)
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { createSession, listSessions, getMessages, deleteSession } from '@/api.js'
import { cortexWs } from '@/ws/cortex-client.js'
import { useToastStore } from '@/stores/toast.js'

let _uid = 0
function uid() { return 'cx' + (++_uid) }

export const useCortexStore = defineStore('cortex', () => {
  const sessionId = ref(null)
  const sessions = ref([])
  const currentTitle = ref('新对话')
  const messages = ref([])
  const processing = ref(false)
  const isStreaming = ref(false)
  const streamingContent = ref('')
  const streamingIdx = ref(-1)
  const currentModel = ref('large')
  const isConnected = ref(false)

  const _queue = []
  let _timer = null
  let _ended = false
  let _stopped = false
  const _SPEED = 30

  const sessionList = computed(() =>
    sessions.value.map(s => ({
      session_id: s.session_id,
      title: s.title || s.session_id?.slice(0, 12) || '未命名',
      last_active: s.last_active || s.created_at || '',
      message_count: s.message_count || 0,
    }))
  )

  async function loadSessions() {
    try { const data = await listSessions(); sessions.value = data.sessions || [] }
    catch { sessions.value = [] }
  }

  async function initSession() {
    _unbindWsEvents(); cortexWs.disconnect(); isConnected.value = false
    try { const data = await createSession(); sessionId.value = data.session_id }
    catch { sessionId.value = 'ses_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8) }
    currentTitle.value = '新对话'
    messages.value = []; _stopped = false; processing.value = false; isStreaming.value = false
    streamingContent.value = ''; streamingIdx.value = -1
    try { await cortexWs.connect(sessionId.value); isConnected.value = true; _bindWsEvents() }
    catch { isConnected.value = false }
    await loadSessions()
    return sessionId.value
  }

  async function switchSession(sid) {
    _unbindWsEvents(); cortexWs.disconnect()
    sessionId.value = sid
    messages.value = []; _stopped = false; processing.value = false; isStreaming.value = false
    streamingContent.value = ''; streamingIdx.value = -1
    try {
      const data = await getMessages(sid)
      const msgs = data.messages || []
      messages.value = msgs.map(m => ({ _id: uid(), role: m.role, content: m.content || '' }))
      currentTitle.value = sessions.value.find(s => s.session_id === sid)?.title || '对话'
    } catch { messages.value = [] }
    try { await cortexWs.connect(sid); isConnected.value = true; _bindWsEvents() }
    catch { isConnected.value = false }
  }

  async function deleteSession(sid) {
    try { await deleteSession(sid) } catch {}
    sessions.value = sessions.value.filter(s => s.session_id !== sid)
    if (sid === sessionId.value) await initSession()
  }

  function sendMessage(content) {
    if (!content || !content.trim() || processing.value) return
    messages.value.push({ _id: uid(), role: 'user', content })
    processing.value = true
    _sendWithRetry(content, 0)
  }

  function _sendWithRetry(content, attempt) {
    const sent = cortexWs.send({ type: 'input', content })
    if (sent) return
    if (attempt >= 2) {
      processing.value = false
      messages.value.push({ _id: uid(), role: 'system', content: '⚠️ 发送失败，WebSocket 未连接' })
      try { useToastStore().show('Cortex 后端未连接', 'error') } catch {}
      return
    }
    setTimeout(() => { if (processing.value) _sendWithRetry(content, attempt + 1) }, 1000 * (attempt + 1))
  }

  function stopGeneration() {
    _stopped = true; clearTimeout(_timer); _queue.length = 0
    processing.value = false; isStreaming.value = false
  }

  function _bindWsEvents() {
    cortexWs.on('message', _onToken)
    cortexWs.on('done', _onDone)
    cortexWs.on('error', _onError)
  }

  function _unbindWsEvents() {
    cortexWs.off('message', _onToken)
    cortexWs.off('done', _onDone)
    cortexWs.off('error', _onError)
  }

  function _onToken(d) {
    if (_stopped) return
    if (!isStreaming.value) {
      isStreaming.value = true; streamingContent.value = ''
      streamingIdx.value = messages.value.length
      messages.value.push({ _id: uid(), role: 'assistant', content: '' })
      _queue.length = 0; _ended = false; _pump()
    }
    _queue.push(d.content || '')
  }

  function _onDone() { _ended = true; if (_queue.length === 0) _finalize() }

  function _onError(d) {
    _finalize()
    messages.value.push({ _id: uid(), role: 'system', content: '⚠️ ' + (d.content || '未知错误') })
  }

  function _pump() {
    clearTimeout(_timer)
    if (_queue.length === 0) {
      if (_ended) return _finalize()
      _timer = setTimeout(_pump, _SPEED); return
    }
    streamingContent.value += _queue.shift()
    const i = streamingIdx.value
    if (messages.value[i]) messages.value[i] = { ...messages.value[i], content: streamingContent.value }
    _timer = setTimeout(_pump, _SPEED)
  }

  function _finalize() {
    clearTimeout(_timer); _queue.length = 0; _ended = false
    isStreaming.value = false; streamingContent.value = ''
    streamingIdx.value = -1; processing.value = false
  }

  function clearMessages() { messages.value = []; stopGeneration() }

  function disconnect() {
    clearTimeout(_timer); _queue.length = 0
    _unbindWsEvents(); cortexWs.disconnect(); isConnected.value = false
  }

  return {
    sessionId, sessions, currentTitle, sessionList,
    messages, processing, isStreaming, streamingContent, streamingIdx, currentModel, isConnected,
    loadSessions, initSession, switchSession, deleteSession,
    sendMessage, stopGeneration, clearMessages, disconnect,
  }
})