<script setup>
defineProps({
  icon: { type: String, default: '📭' },
  text: { type: String, default: '暂无数据' },
  subtitle: { type: String, default: '' },
})
</script>

<template>
  <div class="empty-state empty-state-glow">
    <span class="empty-icon">{{ icon }}</span>
    <p class="empty-text">{{ text }}</p>
    <p v-if="subtitle" class="empty-subtitle">{{ subtitle }}</p>
    <slot name="action" />
  </div>
</template>
