<script setup>
defineProps({
  title: { type: String, default: '' },
})

const emit = defineEmits(['close'])
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="modal">
      <h3 v-if="title">{{ title }}</h3>
      <slot />
      <div class="modal-actions">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>
