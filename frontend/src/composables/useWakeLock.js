/**
 * Screen Wake Lock composable.
 * Watches prevent_sleep config — requests wake lock when enabled, releases when disabled.
 * Re-acquires on visibility change (tab focus).
 */
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useConfigStore } from '@/stores/config.js'

let _sentinel = null

export function useWakeLock() {
  const configStore = useConfigStore()
  const supported = ref(false)
  const active = ref(false)

  async function _acquire() {
    if (!supported.value) return false
    try {
      _sentinel = await navigator.wakeLock.request('screen')
      _sentinel.addEventListener('release', () => { active.value = false })
      active.value = true
      return true
    } catch {
      active.value = false
      return false
    }
  }

  async function _release() {
    if (_sentinel) {
      try { await _sentinel.release() } catch { /* already released */ }
      _sentinel = null
      active.value = false
    }
  }

  function _onVisibilityChange() {
    if (document.visibilityState === 'visible' && configStore.config?.prevent_sleep) {
      _acquire()
    }
  }

  onMounted(() => {
    supported.value = 'wakeLock' in navigator
    document.addEventListener('visibilitychange', _onVisibilityChange)
  })

  onUnmounted(() => {
    document.removeEventListener('visibilitychange', _onVisibilityChange)
    _release()
  })

  // Watch config toggles — reactively request/release
  watch(
    () => configStore.config?.prevent_sleep,
    (enabled) => {
      if (enabled) _acquire()
      else _release()
    },
    { immediate: true },
  )

  return { supported, active }
}
