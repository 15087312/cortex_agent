<script setup>
defineProps({
  text: { type: String, default: '加载中...' },
  skeleton: { type: Boolean, default: false },
})
</script>

<template>
  <div class="loading-overlay">
    <div class="loading-container">
      <div v-if="skeleton" style="width:100%;max-width:360px">
        <div class="skeleton-avatar" style="margin-bottom:12px" />
        <div class="skeleton-line" style="width:80%;height:12px;margin-bottom:8px" />
        <div class="skeleton-line" style="width:60%;height:12px;margin-bottom:8px" />
        <div class="skeleton-line" style="width:90%;height:12px;margin-bottom:8px" />
      </div>
      <div v-else class="loading-spinner"></div>
      <div class="loading-text">{{ text }}</div>
    </div>
  </div>
</template>
