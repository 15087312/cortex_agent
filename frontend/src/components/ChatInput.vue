<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  processing: { type: Boolean, default: false },
  hint: { type: String, default: '' },
})

const emit = defineEmits(['send', 'toast'])
const input = ref('')
const attachments = ref([])
const dragging = ref(false)
const fieldRef = ref(null)

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
  if (props.processing) return
  const text = input.value.trim()
  const atts = attachments.value
  if (!text && atts.length === 0) return
  emit('send', { text, attachments: atts.map(a => ({ type: a.type, name: a.name, data: a.data })) })
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
    if (!isImage && file.size >= 1024 * 1024) {
      emit('toast', { message: '文件过大（最大 1MB）', type: 'error' })
      continue
    }
    const reader = new FileReader()
    reader.onload = (e) => {
      attachments.value.push({ type: file.type || (isImage ? 'image/*' : 'application/octet-stream'), data: e.target.result, name: file.name })
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
        <button class="chat-btn-icon" @click="$refs.fileInput.click()" title="上传文件"><Icon name="paperclip" :size="16" /></button>
        <input ref="fileInput" type="file" multiple accept="image/*,.pdf,.txt" hidden @change="handleFiles($event.target.files)" />
      </div>
      <textarea
        ref="fieldRef"
        class="chat-input-field"
        v-model="input"
        :placeholder="processing ? 'AI 思考中，请稍候...' : '输入消息... (Enter发送, Shift+Enter换行)'"
        rows="1"
        style="resize: none; overflow-y: hidden; line-height: 1.5; box-sizing: border-box;"
        @input="autoResize"
        @keydown="handleKeydown"
        @paste="handlePaste"
      ></textarea>
      <div class="chat-input-actions">
        <span class="chat-input-hint">{{ hint || 'Enter 发送 · Shift+Enter 换行' }}</span>
        <slot name="actions" />
        <button class="chat-send-btn" :disabled="processing" @click="handleSend"><Icon name="send" :size="16" /></button>
      </div>
    </div>
  </div>
</template>
