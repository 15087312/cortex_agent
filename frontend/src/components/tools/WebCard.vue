<script setup>
// 联网卡片：搜索来源列表（web_search）/ 单页抓取摘要（web_fetch）
defineOptions({ name: 'WebCard' })
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, default: () => ({}) },
  status: { type: String, default: 'ok' },
})

const isPage = computed(() => props.view.kind === 'page')
const sources = computed(() => (Array.isArray(props.view.sources) ? props.view.sources : []))
const total = computed(() => props.view.total || sources.value.length)
const url = computed(() => props.view.url || '')
</script>

<template>
  <div class="tool-card tool-card-web">
    <div class="tool-card-head">
      <span class="tool-card-title">{{ view.title }}</span>
      <span v-if="isPage && view.status_code" class="tool-card-badge">{{ $t('chat.toolCard.status', { code: view.status_code }) }}</span>
      <span v-else-if="!isPage" class="tool-card-badge">{{ $t('chat.toolCard.sources', { count: total }) }}</span>
    </div>
    <!-- 单页抓取：标题链接 + 摘要 -->
    <div v-if="isPage" class="tool-card-output">
      <a v-if="url" class="tool-card-link" :href="url" target="_blank" rel="noopener noreferrer">{{ url }}</a>
      <div v-if="view.excerpt" class="tool-card-list-text">{{ view.excerpt }}</div>
      <div v-else-if="status === 'running'" class="tool-card-empty">{{ $t('chat.toolCard.waiting') }}</div>
    </div>
    <!-- 搜索：来源列表 -->
    <div v-else-if="sources.length" class="tool-card-output tool-card-list">
      <div v-for="(s, i) in sources" :key="i" class="tool-card-list-item">
        <a v-if="s.url" class="tool-card-link" :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title || s.url }}</a>
        <span v-else class="tool-card-path">{{ s.title }}</span>
        <span v-if="s.snippet" class="tool-card-list-text">{{ s.snippet }}</span>
      </div>
    </div>
    <div v-else class="tool-card-empty">
      {{ status === 'running' ? $t('chat.toolCard.waiting') : $t('chat.toolCard.noSources') }}
    </div>
  </div>
</template>
