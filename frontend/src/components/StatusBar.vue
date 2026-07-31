<script setup>
import { useHealthStore } from '@/stores/health.js'
import { onMounted, onUnmounted } from 'vue'

const health = useHealthStore()

onMounted(() => health.startPolling())
onUnmounted(() => health.stop())
</script>

<template>
  <div class="status-bar">
    <div class="status-item">
      <span class="status-dot" :class="health.status"></span>
      <span>{{ health.status === 'healthy' ? '系统健康' : health.status === 'degraded' ? '系统降级' : '未连接' }}</span>
    </div>
    <div class="status-item"><span>{{ health.moduleCount }}</span></div>
    <div class="spacer"></div>
    <div class="status-item"><span>{{ health.backendText }}</span></div>
  </div>
</template>
