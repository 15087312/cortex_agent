<script setup>
// 单个模型状态节点（大循环角色树），自引用递归渲染子节点（主管→专家）
defineOptions({ name: 'RunnerNode' })
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
})

const STATUS_META = {
  thinking: { key: 'thinking', cls: 'st-thinking' },
  tool_loop: { key: 'tool_loop', cls: 'st-tool' },
  waiting_delegation: { key: 'waiting_delegation', cls: 'st-waiting' },
  completed: { key: 'completed', cls: 'st-done' },
  error: { key: 'error', cls: 'st-error' },
  idle: { key: 'idle', cls: 'st-idle' },
}

function meta(r) {
  return STATUS_META[r.status] || STATUS_META.idle
}

function statusLabel(r) {
  return t('runnerNode.status.' + meta(r).key)
}

function detail(r) {
  const parts = []
  // React 工具循环：_generate_with_tools 内 chat→tool→execute 循环
  if (r.react_loop) {
    const rl = r.react_loop
    parts.push(t('runnerNode.toolLoop', { turn: rl.turn || 0, max: rl.max || r.max_turns || '?' }) + (rl.tool ? ` · ${rl.tool}` : ''))
  }
  // continue_think 循环：ContinuousThinker 每轮思考 + 等待
  if (r.think_loop) {
    const tl = r.think_loop
    parts.push(t('runnerNode.thinkProgress', { round: tl.round, max: tl.max || '?' }) + (tl.wait ? ` · ${t('runnerNode.waitSec', { wait: tl.wait })}` : ''))
  }
  if (r.status === 'waiting_delegation') parts.push(r.status_detail || t('runnerNode.waitingSubmodel'))
  if (r.status === 'error') parts.push(r.status_detail || '')
  return parts.join(' · ')
}
</script>

<template>
  <div class="think-node" :class="'td-' + (props.node.tier || 'large')" :style="{ marginLeft: depth * 16 + 'px' }">
    <div class="think-node-row">
      <span class="think-avatar">{{ (props.node.name || '?').slice(0, 1) }}</span>
      <span class="think-name">{{ props.node.name || props.node.role || props.node.model_id }}</span>
      <span class="think-model">{{ props.node.model_id }}</span>
      <span v-if="props.node.active_skill" class="think-badge st-skill" :title="$t('runnerNode.currentSkill', { skill: props.node.active_skill })">⚡ {{ props.node.active_skill }}</span>
      <span class="think-badge" :class="meta(props.node).cls">{{ statusLabel(props.node) }}</span>
      <span class="think-detail">{{ detail(props.node) }}</span>
    </div>
    <div v-if="props.node.last_thought && props.node.status !== 'error'" class="think-last" :title="props.node.last_thought">{{ props.node.last_thought }}</div>
    <RunnerNode v-for="child in props.node.children" :key="child.model_id" :node="child" :depth="depth + 1" />
  </div>
</template>
