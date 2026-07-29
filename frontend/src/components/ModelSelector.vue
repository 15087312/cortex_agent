<script setup>
const model = defineModel()
</script>

<template>
  <select class="input chat-model-select" v-model="model">
    <option value="large">总指挥</option>
    <option value="supervisor">主管</option>
    <option value="expert">专家</option>
  </select>
</template>
