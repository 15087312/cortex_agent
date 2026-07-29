import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '@/api.js'

export const useHealthStore = defineStore('health', () => {
  const status = ref('checking')
  const moduleCount = ref('-')
  const backendText = ref('')
  let _interval = null

  async function check() {
    try {
      const r = await endpoints.health()
      const d = r.data
      status.value = d.status === 'healthy' ? 'healthy' : 'degraded'
      backendText.value = 'API: ' + (d.status || '-')
    } catch {
      status.value = 'offline'
      backendText.value = ''
    }
  }

  function startPolling() {
    check()
    _interval = setInterval(check, 15000)
  }

  function stop() {
    if (_interval) { clearInterval(_interval); _interval = null }
  }

  return { status, moduleCount, backendText, check, startPolling, stop }
})
