<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  text: { type: String, default: '' },
  // 流式进行中：显示呼吸点，并自动展开
  live: { type: Boolean, default: false },
  defaultOpen: { type: Boolean, default: true },
})

const open = ref(props.defaultOpen)
watch(
  () => props.live,
  (isLive) => { if (isLive) open.value = true },
)
</script>

<template>
  <div class="deep-thinking" :class="{ 'deep-thinking-live': live }">
    <button type="button" class="deep-thinking-head" @click="open = !open">
      <span class="deep-thinking-icon" aria-hidden="true">✦</span>
      <span class="deep-thinking-title">
        {{ live ? $t('chatMessage.deepThinkingLive') : $t('chatMessage.deepThinking') }}
      </span>
      <span v-if="live" class="deep-thinking-dot" aria-hidden="true"></span>
      <span class="deep-thinking-chevron" aria-hidden="true">{{ open ? '▾' : '▸' }}</span>
    </button>
    <div v-if="open" class="deep-thinking-body">{{ text }}</div>
  </div>
</template>

<style scoped>
.deep-thinking {
  max-width: 620px;
  margin-bottom: 8px;
  border: 1px solid var(--border-light);
  border-radius: 10px;
  background: var(--bg-secondary);
  overflow: hidden;
}
.deep-thinking-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.deep-thinking-head:hover { background: var(--bg-tertiary); }
.deep-thinking-icon { color: var(--accent); font-size: 11px; }
.deep-thinking-title { color: var(--text-secondary); font-weight: 500; }
.deep-thinking-chevron { margin-left: auto; color: var(--text-muted); }
.deep-thinking-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent);
  animation: deep-thinking-pulse 1.2s ease-in-out infinite;
}
.deep-thinking-body {
  max-height: 280px;
  padding: 8px 10px 10px;
  border-top: 1px solid var(--border-light);
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-y: auto;
}
@keyframes deep-thinking-pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.1); }
}
</style>
