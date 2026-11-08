<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'
import { endpoints } from '@/api.js'
import { pickAudioMimeType } from '@/utils/audio.js'

const props = defineProps({
  processing: { type: Boolean, default: false },
  // 有待审批/待回答请求时输入区被接管（Phase 4.b）：先处理上方横幅，不能直接发新消息
  blocked: { type: Boolean, default: false },
  hint: { type: String, default: '' },
})

const emit = defineEmits(['send', 'toast'])
const { t } = useI18n()
const input = ref('')
const attachments = ref([])
const dragging = ref(false)
const fieldRef = ref(null)
// 联网搜索开关：纯对话模式可据此让后端先搜索互联网再回答
const webSearch = ref(false)

// ── 语音录制 ──
const recording = ref(false)
let mediaRecorder = null
let audioChunks = []

async function toggleRecording() {
  if (recording.value) {
    stopRecording()
  } else {
    await startRecording()
  }
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const mime = pickAudioMimeType()
    mediaRecorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
    audioChunks = []

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data)
    }

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop())
      const blob = new Blob(audioChunks, { type: mediaRecorder?.mimeType || mime || 'audio/webm' })
      audioChunks = []
      await transcribeAudio(blob)
    }

    mediaRecorder.start()
    recording.value = true
  } catch (e) {
    emit('toast', { message: t('chatInput.micError'), type: 'error' })
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
    recording.value = false
  }
}

async function transcribeAudio(blob) {
  try {
    const r = await endpoints.recognizeSpeech(blob)
    if (r?.success && r.data?.text) {
      input.value += (input.value ? ' ' : '') + r.data.text
      autoResize()
    } else {
      emit('toast', { message: t('chatInput.transcribeFailed'), type: 'error' })
    }
  } catch {
    emit('toast', { message: t('chatInput.transcribeFailed'), type: 'error' })
  }
}

// 输入框最大高度（超出滚动），自动随输入文字增长/收缩
const MAX_INPUT_H = 160

function autoResize() {
  const el = fieldRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, MAX_INPUT_H) + 'px'
  el.style.overflowY = el.scrollHeight > MAX_INPUT_H ? 'auto' : 'hidden'
}

function _focusField() {
  if (fieldRef.value) { fieldRef.value.focus(); fieldRef.value.setSelectionRange?.(fieldRef.value.value.length, fieldRef.value.value.length) }
}
function _onFocusRequest() { _focusField() }

onMounted(() => {
  window.addEventListener('cortex-focus-input', _onFocusRequest)
  autoResize()
})
onUnmounted(() => window.removeEventListener('cortex-focus-input', _onFocusRequest))

function handleSend() {
  if (props.processing || props.blocked) return
  const text = input.value.trim()
  const atts = attachments.value
  if (!text && atts.length === 0) return
  emit('send', { text, attachments: atts.map(a => ({ type: a.type, name: a.name, data: a.data })), webSearch: webSearch.value })
  input.value = ''
  attachments.value = []
  autoResize()
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function handleFiles(fileList) {
  const files = fileList || []
  for (const file of files) {
    const isImage = file.type.startsWith('image/')
    const isVideo = file.type.startsWith('video/')
    // 图片 >1MB、文本/pdf >1MB 拒收；视频放行到 50MB（后端 VIDEO_MAX_SIZE_MB 同值守卫）
    const tooLarge = isVideo ? file.size >= 1024 * 1024 * 50 : (!isImage && file.size >= 1024 * 1024)
    if (tooLarge) {
      emit('toast', { message: t('chatInput.fileTooLarge'), type: 'error' })
      continue
    }
    const reader = new FileReader()
    reader.onload = (e) => {
      attachments.value.push({ type: file.type || (isImage ? 'image/*' : isVideo ? 'video/*' : 'application/octet-stream'), data: e.target.result, name: file.name })
    }
    reader.readAsDataURL(file)
  }
}

function handlePaste(e) {
  const items = e.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault()
      handleFiles([item.getAsFile()])
      return
    }
  }
}

function handleDragOver(e) {
  if (e.dataTransfer?.types?.includes('Files')) {
    e.preventDefault()
    dragging.value = true
  }
}

function handleDragLeave() {
  dragging.value = false
}

function handleDrop(e) {
  dragging.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    e.preventDefault()
    handleFiles(files)
  }
}

function removeAttachment(i) {
  attachments.value.splice(i, 1)
}
</script>

<template>
  <div
    class="chat-input-area"
    :class="{ 'drag-over': dragging }"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
    <div v-if="attachments.length > 0" class="chat-attachments">
      <div v-for="(att, i) in attachments" :key="i" class="chat-attachment-item">
        <img v-if="att.type && att.type.startsWith('image/')" :src="att.data" class="chat-attachment-thumb" />
        <span v-else class="chat-attachment-file"><Icon name="file" :size="12" /> {{ att.name }}</span>
        <button class="chat-attachment-remove" @click="removeAttachment(i)">✕</button>
      </div>
    </div>
    <div class="chat-input-wrapper">
      <div class="chat-input-toolbar">
        <button class="chat-btn-icon" @click="$refs.fileInput.click()" :title="$t('chatInput.uploadFile')"><Icon name="paperclip" :size="16" /></button>
        <button class="chat-btn-icon" :class="{ 'chat-btn-recording': recording }" @click="toggleRecording" :title="recording ? $t('chatInput.stopRecording') : $t('chatInput.startRecording')"><Icon name="mic" :size="16" /></button>
        <button class="chat-btn-icon" :class="{ 'chat-btn-active': webSearch }" @click="webSearch = !webSearch" :title="$t('chatInput.webSearch')"><Icon name="globe" :size="16" /></button>
        <input ref="fileInput" type="file" multiple accept="image/*,.pdf,.txt,video/*" hidden @change="handleFiles($event.target.files)" />
      </div>
      <textarea
        ref="fieldRef"
        class="chat-input-field"
        v-model="input"
        :placeholder="blocked ? $t('chatInput.pendingPlaceholder') : (processing ? $t('chatInput.thinkingPlaceholder') : $t('chatInput.inputPlaceholder'))"
        rows="1"
        style="resize: none; overflow-y: hidden; line-height: 1.5; box-sizing: border-box;"
        @input="autoResize"
        @keydown="handleKeydown"
        @paste="handlePaste"
      ></textarea>
      <div class="chat-input-actions">
        <span class="chat-input-hint">{{ hint || $t('chatInput.enterHint') }}</span>
        <slot name="actions" />
        <button class="chat-send-btn" :disabled="processing || blocked" @click="handleSend"><Icon name="send" :size="16" /></button>
      </div>
    </div>
  </div>
</template>
