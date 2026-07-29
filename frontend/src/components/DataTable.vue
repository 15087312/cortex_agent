<script setup>
defineProps({
  headers: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  emptyMsg: { type: String, default: '暂无数据' },
})
</script>

<template>
  <div v-if="!rows || rows.length === 0" class="empty-state">
    <span class="empty-icon">📭</span>
    <p class="empty-text">{{ emptyMsg }}</p>
  </div>
  <table v-else class="data-table">
    <thead>
      <tr>
        <th v-for="h in headers" :key="h">{{ h }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in rows" :key="i">
        <td v-for="(cell, j) in row" :key="j" v-html="cell"></td>
      </tr>
    </tbody>
  </table>
</template>
