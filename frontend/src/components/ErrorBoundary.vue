<script setup>
import { ref, onErrorCaptured } from 'vue'

const error = ref(null)

onErrorCaptured((err) => {
  error.value = err?.message || String(err)
  return false
})
</script>

<template>
  <div v-if="error" class="error-boundary">
    <div class="alert alert-error">
      <span>页面加载失败: {{ error }}</span>
      <button class="btn btn-sm" @click="error = null">重试</button>
    </div>
  </div>
  <slot v-else />
</template>
