<script setup>
defineProps({
  label: { type: String, default: '' },
})
</script>

<template>
  <div class="thinking-indicator">
    <div class="thinking-dot"></div>
    <span>{{ label || $t('thinkingIndicator.thinking') }}</span>
    <span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>
  </div>
</template>