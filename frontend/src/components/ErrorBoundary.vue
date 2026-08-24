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
      <span>{{ $t('errorBoundary.loadFailed') }}: {{ error }}</span>
      <button class="btn btn-sm" @click="error = null">{{ $t('common.retry') }}</button>
    </div>
  </div>
  <slot v-else />
</template>
