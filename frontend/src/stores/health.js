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
      // 模块数：优先从 /management/modules 拿真实数量，失败回退健康检查 checks 数
      try {
        const m = await fetch('/api/management/modules', { headers: { Accept: 'application/json' } }).then(x => x.json())
        const mods = m?.data?.modules
        moduleCount.value = Array.isArray(mods) ? String(mods.length) : String(Object.keys(mods || {}).length)
      } catch {
        moduleCount.value = String(Object.keys(d.checks || {}).length)
      }
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
