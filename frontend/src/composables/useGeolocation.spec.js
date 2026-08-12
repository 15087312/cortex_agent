import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useGeolocation } from './useGeolocation.js'
import { useConfigStore } from '@/stores/config.js'

describe('useGeolocation', () => {
  let watchCb = null
  let clearWatch

  beforeEach(() => {
    setActivePinia(createPinia())
    watchCb = null
    clearWatch = vi.fn()
    const watchPosition = vi.fn((success, error) => {
      watchCb = { success, error }
      return 42
    })
    vi.stubGlobal('navigator', {
      geolocation: { watchPosition, clearWatch },
      permissions: {
        query: vi.fn(async () => ({ state: 'granted', addEventListener: vi.fn() })),
      },
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  async function toggleConfig(store, val) {
    store.config = { allow_geolocation: val }
    await Promise.resolve()
    await Promise.resolve()
  }

  it('不支持 geolocation 时 supported=false', () => {
    // 用空对象：避免 geolocation 键仍存在导致 'geolocation' in navigator 为 true
    vi.stubGlobal('navigator', {})
    const g = useGeolocation()
    expect(g.supported.value).toBe(false)
  })

  it('配置允许时启动 watch 并填充位置', async () => {
    const store = useConfigStore()
    store.config = { allow_geolocation: false }
    const g = useGeolocation()
    await toggleConfig(store, true)
    expect(navigator.geolocation.watchPosition).toHaveBeenCalled()
    const pos = { coords: { latitude: 31.2, longitude: 121.5, accuracy: 10, altitude: null, altitudeAccuracy: null, heading: null, speed: null }, timestamp: 1 }
    watchCb.success(pos)
    expect(g.position.value.latitude).toBe(31.2)
    expect(g.position.value.longitude).toBe(121.5)
    expect(g.error.value).toBeNull()
  })

  it('位置错误时记录 error', async () => {
    const store = useConfigStore()
    store.config = { allow_geolocation: false }
    const g = useGeolocation()
    await toggleConfig(store, true)
    watchCb.error({ code: 1, message: 'denied' })
    expect(g.error.value).toEqual({ code: 1, message: 'denied' })
  })

  it('配置关闭时 clearWatch 并清空位置', async () => {
    const store = useConfigStore()
    store.config = { allow_geolocation: false }
    const g = useGeolocation()
    await toggleConfig(store, true)
    await toggleConfig(store, false)
    expect(clearWatch).toHaveBeenCalledWith(42)
    expect(g.position.value).toBeNull()
    expect(g.error.value).toBeNull()
  })
})
