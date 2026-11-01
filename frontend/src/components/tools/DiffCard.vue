<script setup>
// 改动卡片：write_text_file 等写入类工具的行级 diff（+N / -M）
defineOptions({ name: 'DiffCard' })
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, default: () => ({}) },
  status: { type: String, default: 'ok' },
})

const diffs = computed(() => (Array.isArray(props.view.diffs) ? props.view.diffs : []))
const hasLines = computed(() => diffs.value.some(d => (d.lines || []).length))
const additions = computed(() => props.view.additions || 0)
const deletions = computed(() => props.view.deletions || 0)

function lineClass(line) {
  const text = String(line || '')
  if (text.startsWith('+++') || text.startsWith('---')) return 'diff-file'
  if (text.startsWith('@@')) return 'diff-hunk'
  if (text.startsWith('+')) return 'diff-add'
  if (text.startsWith('-')) return 'diff-del'
  return 'diff-ctx'
}
</script>

<template>
  <div class="tool-card tool-card-diff">
    <div class="tool-card-head">
      <span class="tool-card-title">{{ view.title }}</span>
      <span v-if="view.action" class="tool-card-note">{{ view.action }}</span>
      <span class="tool-card-badge ok">{{ $t('chat.toolCard.additions', { count: additions }) }}</span>
      <span class="tool-card-badge error">{{ $t('chat.toolCard.deletions', { count: deletions }) }}</span>
    </div>
    <template v-if="hasLines">
      <div v-for="(d, i) in diffs" :key="i" class="tool-card-diff-block">
        <div v-if="d.path && diffs.length > 1" class="tool-card-note">{{ d.path }}</div>
        <div class="tool-card-output tool-card-diff-lines">
          <div
            v-for="(line, j) in (d.lines || [])"
            :key="j"
            class="diff-line"
            :class="lineClass(line)"
          >{{ line }}</div>
        </div>
      </div>
    </template>
    <div v-else class="tool-card-empty">
      {{ status === 'running' ? $t('chat.toolCard.waiting') : $t('chat.toolCard.noDiff') }}
    </div>
  </div>
</template>
