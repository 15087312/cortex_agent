<script setup>
defineProps({
  placeholder: { type: String, default: '搜索...' },
  modelValue: String,
})
const emit = defineEmits(['update:modelValue', 'search'])
</script>

<template>
  <div class="search-bar">
    <input
      class="input"
      :placeholder="placeholder"
      :value="modelValue"
      @input="emit('update:modelValue', $event.target.value)"
      @keyup.enter="emit('search')"
      style="flex:1"
    />
    <slot />
  </div>
</template>
