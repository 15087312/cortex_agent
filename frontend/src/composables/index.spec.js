import { describe, it, expect } from 'vitest'

describe('composables/index.js', () => {
  it('re-export useWakeLock 与 useGeolocation', async () => {
    const mod = await import('@/composables/index.js')
    expect(typeof mod.useWakeLock).toBe('function')
    expect(typeof mod.useGeolocation).toBe('function')
  })

  it('与直接导入的实现一致', async () => {
    const barrel = await import('@/composables/index.js')
    const directWake = await import('@/composables/useWakeLock.js')
    const directGeo = await import('@/composables/useGeolocation.js')
    expect(barrel.useWakeLock).toBe(directWake.useWakeLock)
    expect(barrel.useGeolocation).toBe(directGeo.useGeolocation)
  })
})
