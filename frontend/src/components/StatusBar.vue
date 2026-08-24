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
      <span>{{ health.status === 'healthy' ? $t('statusbar.healthy') : health.status === 'degraded' ? $t('statusbar.degraded') : $t('statusbar.disconnected') }}</span>
    </div>
    <div class="status-item"><span>{{ health.moduleCount }}</span></div>
    <div class="spacer"></div>
    <div class="status-item"><span>{{ health.backendText }}</span></div>
  </div>
</template>
