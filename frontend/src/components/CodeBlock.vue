<script setup>
import { ref, onUnmounted } from 'vue'
import { copyCodeBlock } from '@/utils/markdown.js'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  language: { type: String, default: '' },
  code: { type: String, required: true },
  highlightedHtml: { type: String, default: '' },
})

const copied = ref(false)
let resetTimer = null

onUnmounted(() => {
  clearTimeout(resetTimer)
})

async function handleCopy() {
  try {
    await copyCodeBlock(props.code)
    copied.value = true
    clearTimeout(resetTimer)
    resetTimer = setTimeout(() => { copied.value = false }, 2000)
  } catch {
    // 复制失败静默处理
  }
}
</script>

<template>
  <div class="code-block">
    <div class="code-block-header">
      <span class="lang-label">{{ language || $t('common.code') }}</span>
      <button class="copy-btn" @click="handleCopy">
        <Icon :name="copied ? 'check' : 'copy'" :size="12" /> {{ copied ? $t('common.copied') : $t('common.copy') }}
      </button>
    </div>
    <!-- v-html 仅用于语法高亮（hljs 输出不含用户输入，安全） -->
    <div v-if="highlightedHtml" v-html="highlightedHtml"></div>
    <pre v-else><code>{{ code }}</code></pre>
  </div>
</template>
