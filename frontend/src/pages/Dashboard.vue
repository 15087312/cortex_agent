<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'

const moduleOk = ref('-')
const apiStatus = ref('-')
const modules = ref([])

onMounted(async () => {
  try {
    const [dash, health] = await Promise.all([endpoints.dashboard().catch(() => null), endpoints.health().catch(() => null)])
    const mods = dash?.data?.modules || {}
    modules.value = Object.entries(mods).map(([n, s]) => ({ name: n, status: s }))
    moduleOk.value = Object.values(mods).filter(v => v === 'healthy').length + '/' + Object.keys(mods).length
    apiStatus.value = health?.data?.status === 'healthy' ? '健康' : health?.data?.status || '-'
  } catch {}
})

function statusLabel(s) { return s === 'healthy' ? '正常' : s === 'degraded' ? '降级' : s }
function statusBadge(s) { return s === 'healthy' ? 'badge-green' : s === 'degraded' ? 'badge-yellow' : 'badge-red' }
</script>

<template>
  <div>
    <div class="page-header"><h2>📊 仪表盘</h2></div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-icon">🟢</div><div class="stat-value">{{ moduleOk }}</div><div class="stat-label">模块健康</div></div>
        <div class="stat-card"><div class="stat-icon">⚡</div><div class="stat-value">{{ apiStatus }}</div><div class="stat-label">API状态</div></div>
        <div class="stat-card" style="cursor:pointer" @click="$router.push('/chat')"><div class="stat-icon">💬</div><div class="stat-value">开始对话</div><div class="stat-label">点击进入</div></div>
      </div>
      <div class="card">
        <div class="card-header">模块状态</div>
        <table class="data-table" v-if="modules.length > 0">
          <thead><tr><th>模块</th><th>状态</th></tr></thead>
          <tbody><tr v-for="m in modules" :key="m.name"><td><strong>{{ m.name }}</strong></td><td><span class="badge" :class="statusBadge(m.status)">{{ statusLabel(m.status) }}</span></td></tr></tbody>
        </table>
        <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>
      </div>
    </div>
  </div>
</template>
