import { defineStore } from 'pinia'
import { ref } from 'vue'
import { useSessionStore } from './session.js'
import { useWsStore } from '@/ws/store.js'

let _uid = 0
function uid() { return 'm' + (++_uid) }

export const useChatStore = defineStore('chat', () => {
  const session = useSessionStore()
  const ws = useWsStore()

  const messages = ref([])
  const processing = ref(false)
  const currentModel = ref('large')
  const streamingIdx = ref(-1)

  async function init() {
    const sid = await session.createSession()
    ws.reset()
    streamingIdx.value = -1
    ws.connect(sid)
  }

  async function switchToSession(sid) {
    session.switchSession(sid)
    messages.value = []
    try {
      const dialog = await session.loadDialog(sid)
      messages.value = dialog.map(d => ({
        _id: uid(),
        role: d.role || d.sender || 'assistant',
        content: d.content || d.text || '',
      }))
    } catch {}
    ws.reset()
    streamingIdx.value = -1
    ws.connect(sid)
  }

  function addMessage(msg) {
    messages.value.push({ _id: uid(), ...msg })
  }

  function handleStreamContent(content) {
    if (!ws.isStreaming) {
      ws.startStreaming()
      streamingIdx.value = messages.value.length
      messages.value.push({ _id: uid(), role: 'assistant', content: '' })
    }
    ws.appendStreaming(content)
    const idx = streamingIdx.value
    if (idx >= 0 && messages.value[idx]) {
      messages.value[idx] = { ...messages.value[idx], content: ws.streamingContent }
    }
  }

  function finalizeStream(content) {
    const final = ws.finishStreaming(content)
    const idx = streamingIdx.value
    if (idx >= 0 && messages.value[idx]) {
      messages.value[idx] = { ...messages.value[idx], content: final }
    }
    streamingIdx.value = -1
    processing.value = false
  }

  async function sendMessage(content, attachments) {
    const sent = ws.wsClient.send({ type: 'input', content, model: currentModel.value, attachments })
    if (!sent) {
      await new Promise(r => setTimeout(r, 1000))
      ws.wsClient.send({ type: 'input', content, model: currentModel.value, attachments })
    }
  }

  function stop() {
    ws.wsClient.send({ type: 'stop' })
    finalizeStream('')
  }

  function clearMessages() {
    messages.value = []
    ws.reset()
    streamingIdx.value = -1
  }

  return {
    messages, processing, currentModel, streamingIdx,
    init, switchToSession, addMessage, handleStreamContent,
    finalizeStream, sendMessage, stop, clearMessages,
  }
})
