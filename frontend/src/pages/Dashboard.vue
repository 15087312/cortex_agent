<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'

const router = useRouter()
const moduleOk = ref('-')
const apiStatus = ref('-')
const modules = ref([])

async function loadData() {
  try {
    const [dash, health] = await Promise.all([endpoints.dashboard().catch(() => null), endpoints.health().catch(() => null)])
    const mods = dash?.data?.modules || {}
    modules.value = Object.entries(mods).map(([n, s]) => ({ name: n, status: s }))
    moduleOk.value = Object.values(mods).filter(v => v === 'healthy').length + '/' + Object.keys(mods).length
    apiStatus.value = health?.data?.status === 'healthy' ? '健康' : health?.data?.status || '-'
  } catch {}
}

let timer = null
onMounted(async () => {
  await loadData()
  timer = setInterval(loadData, 30000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

function statusLabel(s) { return s === 'healthy' ? '正常' : s === 'degraded' ? '降级' : s }
function statusBadge(s) { return s === 'healthy' ? 'badge-green' : s === 'degraded' ? 'badge-yellow' : 'badge-red' }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>仪表盘</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="health-grid">
        <div class="health-card">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 * (1 - (parseInt(moduleOk) || 0) / Math.max(parseInt(moduleOk.toString().split('/')[1]) || 1, 1)) }"/></svg>
            <div class="ring-label">{{ moduleOk }}</div>
          </div>
          <div class="health-name">模块健康</div>
        </div>
        <div class="health-card">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: apiStatus === '健康' ? 0 : 94 }"/></svg>
            <div class="ring-label">{{ apiStatus === '健康' ? 'OK' : '!' }}</div>
          </div>
          <div class="health-name">API 状态</div>
        </div>
        <div class="health-card" @click="router.push('/chat')" title="点击进入对话">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 }"/></svg>
            <div class="ring-label"><Icon name="message" :size="18" /></div>
          </div>
          <div class="health-name">开始对话</div>
        </div>
      </div>
      <div class="card" style="margin-top:12px">
        <div class="card-header">模块状态 ({{ modules.length }})</div>
        <table class="data-table" v-if="modules.length > 0">
          <thead><tr><th>模块</th><th>状态</th></tr></thead>
          <tbody><tr v-for="m in modules" :key="m.name"><td><strong>{{ m.name }}</strong></td><td><span class="badge" :class="statusBadge(m.status)">{{ statusLabel(m.status) }}</span></td></tr></tbody>
        </table>
        <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>
      </div>
      <div class="card" style="margin-top:12px">
        <div class="card-header">最近活跃</div>
        <div class="activity-timeline">
          <div class="activity-item"><span class="activity-time">刚刚</span>系统启动完成</div>
          <div class="activity-item"><span class="activity-time">{{ apiStatus === '健康' ? '就绪' : '等待中' }}</span>API 端点 {{ apiStatus === '健康' ? '连接正常' : '不可达' }}</div>
          <div class="activity-item"><span class="activity-time">模块 {{ moduleOk }}</span>模块健康度检测已完成</div>
        </div>
      </div>
    </div>
  </div>
</template>
