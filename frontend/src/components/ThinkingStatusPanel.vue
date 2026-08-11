<script setup>
// 思考循环状态面板：清晰呈现三层循环（大循环 指挥→主管→专家 / 连续思考 / 工具循环）
// 数据来自后端 thinking_progress 事件解析出的 runner 状态列表
defineOptions({ name: 'ThinkingStatusPanel' })
import { computed } from 'vue'
import Icon from './Icon.vue'
import RunnerNode from './RunnerNode.vue'

const props = defineProps({
  runners: { type: Array, default: () => [] },
  elapsed: { type: Number, default: 0 },
  contextTokens: { type: Number, default: 0 },
  contextWindowSize: { type: Number, default: 0 },
})

// 当前上下文窗口占用百分比（估算 token，来自后端 thinking_progress）
const contextPct = computed(() => {
  const win = props.contextWindowSize || 0
  if (!win || !props.contextTokens) return 0
  return Math.max(0, Math.min(100, Math.round((props.contextTokens / win) * 100)))
})

// 大循环层级树：large 为根，supervisor 挂到指挥下，expert 按 supervisor 挂到主管下
const tree = computed(() => {
  const byId = {}
  props.runners.forEach(r => { byId[r.model_id] = { ...r, children: [] } })
  const roots = []
  props.runners.forEach(r => {
    const node = byId[r.model_id]
    const parent = (r.supervisor && byId[r.supervisor]) ? byId[r.supervisor] : null
    if (parent) parent.children.push(node)
    else roots.push(node)
  })
  return roots
})

const errorRunner = computed(() => props.runners.find(r => r.status === 'error'))
</script>

<template>
  <div v-if="props.runners.length" class="think-panel-wrap">
    <div class="think-panel">
      <div class="think-panel-head">
        <span class="think-panel-title"><Icon name="activity" :size="13" /> 思考循环</span>
        <span class="think-panel-elapsed">{{ props.elapsed }}s</span>
      </div>
      <div v-if="contextPct > 0" class="context-usage" :title="'上下文占用 ' + (props.contextTokens || 0).toLocaleString() + ' / ' + (props.contextWindowSize || 0).toLocaleString() + ' tokens'">
        <span class="context-usage-label">上下文</span>
        <div class="context-usage-track"><div class="context-usage-fill" :class="{ warn: contextPct >= 70, danger: contextPct >= 90 }" :style="{ width: contextPct + '%' }" /></div>
        <span class="context-usage-pct">{{ contextPct }}%</span>
      </div>
      <div v-if="errorRunner" class="think-banner-error">
        <Icon name="alert" :size="14" />
        <span><b>{{ errorRunner.name }}</b> 出错：{{ errorRunner.status_detail }}</span>
      </div>
      <div class="think-tree">
        <RunnerNode v-for="node in tree" :key="node.model_id" :node="node" :depth="0" />
      </div>
    </div>
  </div>
</template>
