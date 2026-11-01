<script setup>
// 读取卡片：路径、行数、带行号的文件片段（read_text_file / read_file）
defineOptions({ name: 'ReadCard' })
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, default: () => ({}) },
  status: { type: String, default: 'ok' },
})

const lines = computed(() => (Array.isArray(props.view.lines) ? props.view.lines : []))
const totalLines = computed(() => props.view.total_lines || lines.value.length)

// 后端统一的紧凑格式：[[行号, 文本], ...]；同时兼容 {n, t} 形态
function lineNo(item) {
  if (Array.isArray(item)) return item[0]
  return item && (item.n ?? item.number ?? item.line)
}
function lineText(item) {
  if (Array.isArray(item)) return item[1]
  return (item && (item.t ?? item.text ?? item.line_text)) || ''
}
</script>

<template>
  <div class="tool-card tool-card-read">
    <div class="tool-card-head">
      <span class="tool-card-title">{{ view.title }}</span>
      <span v-if="view.path && view.path !== view.title" class="tool-card-note">{{ view.path }}</span>
      <span v-if="totalLines" class="tool-card-badge">{{ $t('chat.toolCard.lines', { count: totalLines }) }}</span>
      <span v-if="view.truncated" class="tool-card-badge">{{ $t('chat.toolCard.truncated') }}</span>
    </div>
    <div v-if="lines.length" class="tool-card-output tool-card-code">
      <div v-for="(item, i) in lines" :key="i" class="tool-card-code-line">
        <span class="tool-card-ln">{{ lineNo(item) }}</span>
        <span class="tool-card-code-text">{{ lineText(item) }}</span>
      </div>
    </div>
    <div v-else class="tool-card-empty">
      {{ status === 'running' ? $t('chat.toolCard.waiting') : $t('chat.toolCard.noOutput') }}
    </div>
  </div>
</template>
