<script setup>
// 单个模型状态节点（大循环角色树），自引用递归渲染子节点（主管→专家）
defineOptions({ name: 'RunnerNode' })
const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
})

const STATUS_META = {
  thinking: { label: '思考中', cls: 'st-thinking' },
  tool_loop: { label: '调用工具', cls: 'st-tool' },
  waiting_delegation: { label: '等待委托', cls: 'st-waiting' },
  completed: { label: '已完成', cls: 'st-done' },
  error: { label: '出错', cls: 'st-error' },
  idle: { label: '待命', cls: 'st-idle' },
}

function meta(r) {
  return STATUS_META[r.status] || STATUS_META.idle
}

function detail(r) {
  const parts = []
  // React 工具循环：_generate_with_tools 内 chat→tool→execute 循环
  if (r.react_loop) {
    const rl = r.react_loop
    parts.push(`工具循环 ${(rl.turn || 0)}/${rl.max || r.max_turns || '?'}${rl.tool ? ` · ${rl.tool}` : ''}`)
  }
  // continue_think 循环：ContinuousThinker 每轮思考 + 等待
  if (r.think_loop) {
    const tl = r.think_loop
    parts.push(`思考 ${tl.round}/${tl.max || '?'}${tl.wait ? ` · 等待${tl.wait}s` : ''}`)
  }
  if (r.status === 'waiting_delegation') parts.push(r.status_detail || '等待子模型返回')
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
      <span class="think-badge" :class="meta(props.node).cls">{{ meta(props.node).label }}</span>
      <span class="think-detail">{{ detail(props.node) }}</span>
    </div>
    <div v-if="props.node.last_thought && props.node.status !== 'error'" class="think-last" :title="props.node.last_thought">{{ props.node.last_thought }}</div>
    <RunnerNode v-for="child in props.node.children" :key="child.model_id" :node="child" :depth="depth + 1" />
  </div>
</template>
