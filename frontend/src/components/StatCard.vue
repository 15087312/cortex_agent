<script setup>
import { useRouter } from 'vue-router'

const props = defineProps({
  icon: String,
  value: [String, Number],
  label: String,
  to: String,
})

const router = useRouter()
</script>

<template>
  <div
    class="stat-card"
    :style="to ? 'cursor:pointer' : ''"
    @click="to && router.push(to)"
  >
    <div v-if="icon" class="stat-icon">{{ icon }}</div>
    <div class="stat-value">{{ value }}</div>
    <div class="stat-label">{{ label }}</div>
  </div>
</template>
