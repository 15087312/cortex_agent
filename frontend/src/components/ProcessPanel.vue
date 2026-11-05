<script setup>
// 思考轨迹面板：本轮实时（live）或历史消息（trajectory prop）的 DSH 式时间线。
// 渲染逻辑已抽到可复用的 TrajectoryTimeline；本组件只负责解析数据源。
defineOptions({ name: 'ProcessPanel' })
import { computed } from 'vue'
import TrajectoryTimeline from './TrajectoryTimeline.vue'
import { useChatStore } from '@/stores/chat.js'

const chat = useChatStore()
const props = defineProps({
  content: { type: String, default: '' },       // 旧版纯文本回退
  runners: { type: Object, default: null },      // 兼容旧 props，现已不再渲染模型树
  trajectory: { type: Array, default: null },    // 历史消息持久化的时间线条目
  live: { type: Boolean, default: false },
  open: { type: Boolean, default: false },
})

const entries = computed(() => {
  if (props.live) return chat.trajectory || []
  if (props.trajectory && props.trajectory.length) return props.trajectory
  if (props.content) {
    return props.content.split('\n\n').filter(Boolean).map(t => ({ kind: 'thinking', title: t, ts: 0 }))
  }
  return []
})
</script>

<template>
  <TrajectoryTimeline :entries="entries" title="思考轨迹" :open="open" />
</template>
