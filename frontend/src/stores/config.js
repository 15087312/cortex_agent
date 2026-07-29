import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '@/api.js'

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
    await loadConfig()
  }

  return {
    config, modelStatus,
    loadConfig, loadModelStatus, updateConfig,
  }
})
