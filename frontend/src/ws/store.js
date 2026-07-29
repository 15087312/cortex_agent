import { defineStore } from 'pinia'
import { ref } from 'vue'
import { wsClient } from './client.js'

export const useWsStore = defineStore('ws', () => {
  const isConnected = ref(false)
  const isStreaming = ref(false)
  const streamingContent = ref('')

  function connect(sid) {
    wsClient.connect(sid).then(() => { isConnected.value = true }).catch(() => { isConnected.value = false })
  }

  function disconnect() {
    wsClient.disconnect()
    isConnected.value = false
  }

  function startStreaming() {
    isStreaming.value = true
    streamingContent.value = ''
  }

  function appendStreaming(content) {
    streamingContent.value += content
  }

  function finishStreaming(content) {
    const final = content || streamingContent.value
    isStreaming.value = false
    streamingContent.value = ''
    return final
  }

  function reset() {
    isStreaming.value = false
    streamingContent.value = ''
  }

  return {
    isConnected, isStreaming, streamingContent,
    connect, disconnect, startStreaming, appendStreaming, finishStreaming, reset,
    wsClient,
  }
})
