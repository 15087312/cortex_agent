<script setup>
import { useToastStore } from '@/stores/toast.js'

const toast = useToastStore()
</script>

<template>
  <div class="toast-container">
    <div
      v-for="t in toast.toasts"
      :key="t.id"
      class="alert"
      :class="'alert-' + t.type"
      @click="toast.dismiss(t.id)"
    >
      {{ t.msg }}
    </div>
  </div>
</template>

<style scoped>
.toast-container {
  position: fixed; top: 16px; right: 16px; z-index: var(--z-toast);
  display: flex; flex-direction: column; gap: 8px; pointer-events: none;
}
.toast-container .alert {
  margin: 0; pointer-events: auto; box-shadow: var(--shadow-md);
  animation: slideUp var(--duration-slow) var(--ease-out); max-width: 360px;
  cursor: pointer;
}
</style>
