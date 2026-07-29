<script setup>
import { ref } from 'vue'

const emit = defineEmits(['send'])
const input = ref('')
const attachments = ref([])

function handleSend() {
  const text = input.value.trim()
  if (!text) return
  emit('send', { text, attachments: attachments.value.map(a => a.data) })
  input.value = ''
  attachments.value = []
}

function handleKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function handleFiles(files) {
  for (const file of files) {
    if (file.type.startsWith('image/') || file.size < 1024 * 1024) {
      const reader = new FileReader()
      reader.onload = (e) => {
        attachments.value.push({ type: file.type.startsWith('image/') ? 'image' : 'file', data: e.target.result, name: file.name })
      }
      reader.readAsDataURL(file)
    }
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

function removeAttachment(i) {
  attachments.value.splice(i, 1)
}
</script>

<template>
  <div class="chat-input-area">
    <div v-if="attachments.length > 0" class="chat-attachments">
      <div v-for="(att, i) in attachments" :key="i" class="chat-attachment-item">
        <img v-if="att.type === 'image'" :src="att.data" class="chat-attachment-thumb" />
        <span v-else class="chat-attachment-file">📄 {{ att.name }}</span>
        <button class="chat-attachment-remove" @click="removeAttachment(i)">✕</button>
      </div>
    </div>
    <div class="chat-input-wrapper">
      <div class="chat-input-toolbar">
        <button class="chat-btn-icon" @click="$refs.fileInput.click()" title="上传文件">📎</button>
        <input ref="fileInput" type="file" multiple accept="image/*,.pdf,.txt" hidden @change="handleFiles($event.target.files)" />
      </div>
      <textarea
        class="chat-input-field"
        v-model="input"
        placeholder="输入消息... (Enter发送, Shift+Enter换行)"
        rows="1"
        @keydown="handleKeydown"
        @paste="handlePaste"
      ></textarea>
      <div class="chat-input-actions">
        <span class="chat-input-hint">Enter 发送 · Shift+Enter 换行</span>
        <slot name="actions" />
        <button class="chat-send-btn" @click="handleSend">➤</button>
      </div>
    </div>
  </div>
</template>
