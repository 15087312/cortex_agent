<script setup>
defineProps({
  text: { type: String, default: '加载中...' }
})
</script>

<template>
  <div class="loading-overlay">
    <div class="spinner"></div>
    <span>{{ text }}</span>
  </div>
</template>
