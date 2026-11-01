<script setup>
// 通用卡片：未注册卡片的工具（或没有结构化展示信息时）只显示结果预览，保证不崩、不特殊化。
defineOptions({ name: 'GenericCard' })
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, default: () => ({}) },
  preview: { type: String, default: '' },
  status: { type: String, default: 'ok' },
})

const content = computed(() => props.view.content || props.preview || '')
</script>

<template>
  <div class="tool-card tool-card-generic">
    <pre v-if="content" class="tool-card-output">{{ content }}</pre>
    <div v-else-if="status === 'running'" class="tool-card-empty">{{ $t('chat.toolCard.waiting') }}</div>
  </div>
</template>
