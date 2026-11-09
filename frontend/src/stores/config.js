import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '@/api.js'
import { wsClient } from '@/ws/client.js'

export const useConfigStore = defineStore('config', () => {
  const config = ref({})
  const modelStatus = ref({})

  async function loadConfig() {
    try { const r = await endpoints.config(); config.value = r.data || r || {} } catch {}
  }

  async function loadModelStatus() {
    try { const r = await endpoints.thinkingStatus(); modelStatus.value = r.data?.models || {} } catch {}
  }

  async function updateConfig(k, v) {
    await endpoints.updateConfig(k, v)
    // CORTEX_MODE 在 WebSocket 建连时由后端分流（chatonly / agent）。
    // 已连接的会话不会重新判断模式；断开后让下一条消息按新模式重新建连，
    // 否则设置页切换后，同一对话仍会继续走旧路线。
    if (String(k).toUpperCase() === 'CORTEX_MODE') wsClient.disconnectAll()
    await loadConfig()
  }

  return {
    config, modelStatus,
    loadConfig, loadModelStatus, updateConfig,
  }
})
