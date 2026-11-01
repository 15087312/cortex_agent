<script setup>
// 工具卡片分派：按 view.card 选具体卡片，未注册卡片落 generic（不崩、不特殊化）。
// 头部（名称/参数/状态/耗时）与错误行在这里统一渲染，具体卡片只管自己的内容区。
defineOptions({ name: 'ToolCard' })
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import TerminalCard from './TerminalCard.vue'
import DiffCard from './DiffCard.vue'
import ReadCard from './ReadCard.vue'
import SearchCard from './SearchCard.vue'
import WebCard from './WebCard.vue'
import GenericCard from './GenericCard.vue'

const { t } = useI18n()
const props = defineProps({
  trace: { type: Object, required: true },
})

const CARDS = {
  terminal: TerminalCard,
  diff: DiffCard,
  read: ReadCard,
  search: SearchCard,
  web: WebCard,
}
const view = computed(() => props.trace.view || null)
const card = computed(() => (view.value && CARDS[view.value.card]) || GenericCard)
const status = computed(() => props.trace.status || 'running')

const TRACE_STATUSES = ['running', 'ok', 'error', 'timeout', 'denied', 'invalid', 'interrupted']
const statusLabel = computed(() => t('chat.traceStatus.' + (TRACE_STATUSES.includes(status.value) ? status.value : 'ok')))

const argsFull = computed(() => {
  try {
    const text = JSON.stringify(props.trace.args || {})
    return text && text !== '{}' ? text : ''
  } catch {
    return ''
  }
})
const argsText = computed(() => (argsFull.value.length > 120 ? argsFull.value.slice(0, 120) + '…' : argsFull.value))

// 只有 generic 卡片需要原始预览（其余卡片的数据来自 view）
const cardProps = computed(() => {
  const base = { view: view.value || {}, status: status.value }
  return card.value === GenericCard ? { ...base, preview: props.trace.preview || '' } : base
})
</script>

<template>
  <div class="trace-tool" :data-card="view ? view.card : 'generic'">
    <div class="trace-item" :title="argsFull">
      <span class="trace-dot" :class="'tr-' + status"></span>
      <span class="trace-name">{{ trace.name }}</span>
      <span v-if="argsText" class="trace-args">{{ argsText }}</span>
      <span class="trace-status" :class="'tr-' + status">{{ statusLabel }}</span>
      <span v-if="trace.durationMs" class="trace-dur">{{ trace.durationMs }}ms</span>
    </div>
    <component :is="card" v-bind="cardProps" />
    <div v-if="trace.error" class="trace-preview is-error">{{ trace.error }}</div>
  </div>
</template>
