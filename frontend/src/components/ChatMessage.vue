<script setup>
import { computed } from 'vue'
import { parseMarkdownSegments } from '@/utils/markdown.js'
import CodeBlock from '@/components/CodeBlock.vue'

const props = defineProps({
  message: { type: Object, required: true },
  index: { type: Number, required: true },
  isStreaming: { type: Boolean, default: false },
})

const emit = defineEmits(['copy', 'delete'])

const isUser = computed(() => props.message.role === 'user')

/** 用户消息：纯文本转义 + 换行 */
const userHtml = computed(() => {
  return escapeHtml(props.message.content).replace(/\n/g, '<br>')
})

/** AI 消息：Markdown 结构化解析为 text / code 片段 */
const segments = computed(() => {
  if (isUser.value) return []
  return parseMarkdownSegments(props.message.content)
})

function escapeHtml(s) {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}
</script>

<template>
  <div class="message" :class="isUser ? 'user' : 'ai'">
    <div class="message-avatar">{{ isUser ? 'U' : 'AI' }}</div>
    <div class="message-bubble">
      <!-- 用户消息：纯文本 -->
      <div v-if="isUser" v-html="userHtml"></div>

      <!-- AI 消息：文本片段 v-html + 代码块 CodeBlock 组件 -->
      <template v-else>
        <template v-for="(seg, i) in segments" :key="i">
          <!-- 文本片段：v-html 渲染（Markdown 转 HTML，含段落/标题/列表等） -->
          <div v-if="seg.type === 'text'" v-html="seg.html"></div>

          <!-- 代码块：Vue 组件接管，@click 事件由 Vue 管理 -->
          <CodeBlock
            v-else-if="seg.type === 'code'"
            :language="seg.language"
            :code="seg.code"
            :highlighted-html="seg.highlightedHtml"
          />
        </template>
      </template>

      <span v-if="isStreaming" class="streaming-cursor">▊</span>
      <div class="message-actions">
        <button @click="emit('copy', index)" title="复制">📋</button>
        <button v-if="!isUser" @click="emit('delete', index)" title="删除">🗑</button>
      </div>
    </div>
  </div>
</template>
