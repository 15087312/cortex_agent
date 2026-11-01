<script setup>
// 检索卡片：命中位置 + 片段（knowledge_search / grep / search_files 等）
defineOptions({ name: 'SearchCard' })
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, default: () => ({}) },
  status: { type: String, default: 'ok' },
})

const matches = computed(() => (Array.isArray(props.view.matches) ? props.view.matches : []))
const total = computed(() => props.view.total || matches.value.length)
</script>

<template>
  <div class="tool-card tool-card-search">
    <div class="tool-card-head">
      <span class="tool-card-title">{{ view.title }}</span>
      <span class="tool-card-badge">{{ $t('chat.toolCard.matches', { count: total }) }}</span>
      <span v-if="view.truncated" class="tool-card-badge">{{ $t('chat.toolCard.truncated') }}</span>
    </div>
    <div v-if="matches.length" class="tool-card-output tool-card-list">
      <div v-for="(m, i) in matches" :key="i" class="tool-card-list-item">
        <span v-if="m.path" class="tool-card-path">{{ m.path }}<template v-if="m.line">:{{ m.line }}</template></span>
        <span class="tool-card-list-text">{{ m.text }}</span>
      </div>
    </div>
    <div v-else class="tool-card-empty">
      {{ status === 'running' ? $t('chat.toolCard.waiting') : $t('chat.toolCard.noMatches') }}
    </div>
  </div>
</template>
