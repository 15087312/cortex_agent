/**
 * Geolocation API composable.
 * Watches allow_geolocation config — starts watching when enabled, clears when disabled.
 */
import { ref, watch, onUnmounted } from 'vue'
import { useConfigStore } from '@/stores/config.js'

let _watchId = null

export function useGeolocation() {
  const configStore = useConfigStore()
  const supported = ref(false)
  const permission = ref(null) // 'granted' | 'denied' | 'prompt' | null
  const position = ref(null) // { latitude, longitude, accuracy, ... }
  const error = ref(null)

  function _start() {
    if (!supported.value) return
    // Query permission state
    if (navigator.permissions) {
      navigator.permissions.query({ name: 'geolocation' }).then((status) => {
        permission.value = status.state
        status.addEventListener('change', () => {
          permission.value = status.state
        })
      })
    }
    // Start watching
    _watchId = navigator.geolocation.watchPosition(
      (pos) => {
        position.value = {
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
          altitude: pos.coords.altitude,
          altitudeAccuracy: pos.coords.altitudeAccuracy,
          heading: pos.coords.heading,
          speed: pos.coords.speed,
          timestamp: pos.timestamp,
        }
        error.value = null
      },
      (err) => {
        error.value = { code: err.code, message: err.message }
      },
      { enableHighAccuracy: false, maximumAge: 60000, timeout: 15000 },
    )
  }

  function _stop() {
    if (_watchId != null) {
      navigator.geolocation.clearWatch(_watchId)
      _watchId = null
    }
    position.value = null
    error.value = null
  }

  supported.value = 'geolocation' in navigator

  watch(
    () => configStore.config?.allow_geolocation,
    (allowed) => {
      if (allowed) _start()
      else _stop()
    },
    { immediate: false },
  )

  onUnmounted(() => _stop())

  return { supported, permission, position, error }
}
