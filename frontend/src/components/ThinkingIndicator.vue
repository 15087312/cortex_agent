<script setup>
defineProps({
  label: { type: String, default: '正在思考' },
})
</script>

<template>
  <div class="thinking-indicator">
    <div class="thinking-dot"></div>
    <span>{{ label }}</span>
    <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
  </div>
</template>
