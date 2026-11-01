<script setup>
// 终端卡片：命令、cwd、exit code、输出（exec_command / run_command / run_script）
defineOptions({ name: 'TerminalCard' })
import { computed } from 'vue'

const props = defineProps({
  view: { type: Object, default: () => ({}) },
  status: { type: String, default: 'ok' },
})

const exitCode = computed(() => (typeof props.view.exit_code === 'number' ? props.view.exit_code : null))
const output = computed(() => props.view.output || '')
</script>

<template>
  <div class="tool-card tool-card-terminal">
    <div class="tool-card-head">
      <code class="tool-card-title">{{ view.title }}</code>
      <span v-if="view.cwd" class="tool-card-note">{{ view.cwd }}</span>
      <span
        v-if="exitCode !== null"
        class="tool-card-badge"
        :class="exitCode === 0 ? 'ok' : 'error'"
      >{{ $t('chat.toolCard.exitCode', { code: exitCode }) }}</span>
    </div>
    <pre v-if="output" class="tool-card-output">{{ output }}</pre>
    <div v-else class="tool-card-empty">
      {{ status === 'running' ? $t('chat.toolCard.waiting') : $t('chat.toolCard.noOutput') }}
    </div>
  </div>
</template>
