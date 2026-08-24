<script setup>
import { ref, watch, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

// 展示当前会话实际参与的模型（从会话图谱节点推断），非选择器
const props = defineProps({ sessionId: { type: String, default: '' } })
const { t } = useI18n()
const models = ref([])

async function load() {
  if (!props.sessionId) { models.value = []; return }
  try {
    const r = await fetch('/api/stream/session/' + encodeURIComponent(props.sessionId) + '/graph', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    const nodes = d?.data?.graph?.nodes || []
    models.value = nodes
      .filter((n) => n.tier && n.tier !== 'user')
      .map((n) => ({ label: n.label || n.tier, tier: n.tier }))
    // 图谱无记录（如纯对话模式）→ 回退显示当前对话模型
    if (!models.value.length) models.value = [{ label: t('modelSelector.commander'), tier: 'large' }]
  } catch { models.value = [{ label: t('modelSelector.commander'), tier: 'large' }] }
}

onMounted(load)
watch(() => props.sessionId, load)
</script>

<template>
  <div class="chat-model-tags" v-if="models.length">
    <span v-for="m in models" :key="m.tier + m.label" class="chat-model-tag" :class="'tag-' + m.tier">{{ m.label }}</span>
  </div>
  <span v-else class="chat-model-tags chat-model-tag">{{ $t('modelSelector.defaultModel') }}</span>
</template>

<style scoped>
.chat-model-tags { display: inline-flex; align-items: center; gap: 4px; }
.chat-model-tag {
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: rgba(139,148,158,0.12); color: var(--text-muted); white-space: nowrap;
}
.tag-large { background: rgba(139,92,246,0.15); color: #a78bfa; }
.tag-supervisor { background: rgba(59,130,246,0.15); color: #60a5fa; }
.tag-expert { background: rgba(245,158,11,0.15); color: #fbbf24; }
</style>
