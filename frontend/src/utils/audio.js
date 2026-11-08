/** 选择当前浏览器/WebEngine 支持的录音 MIME 类型（QtWebEngine 不一定支持 audio/webm）。 */
export function pickAudioMimeType() {
  if (typeof MediaRecorder === 'undefined') return ''
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/mp4',
  ]
  for (const type of candidates) {
    try {
      if (MediaRecorder.isTypeSupported(type)) return type
    } catch {
      // 某些 WebEngine 没有 isTypeSupported，继续尝试下一个
    }
  }
  return ''
}
