import { describe, it, expect, afterEach, vi } from 'vitest'
import { pickAudioMimeType } from './audio.js'

afterEach(() => vi.unstubAllGlobals())

describe('pickAudioMimeType', () => {
  it('选择 WebEngine 支持的第一个类型', () => {
    vi.stubGlobal('MediaRecorder', class {
      static isTypeSupported(t) { return t === 'audio/ogg' }
    })
    expect(pickAudioMimeType()).toBe('audio/ogg')
  })

  it('无支持类型时返回空串（交给浏览器默认）', () => {
    vi.stubGlobal('MediaRecorder', class {
      static isTypeSupported() { return false }
    })
    expect(pickAudioMimeType()).toBe('')
  })

  it('没有 MediaRecorder 时返回空串', () => {
    vi.stubGlobal('MediaRecorder', undefined)
    expect(pickAudioMimeType()).toBe('')
  })
})
