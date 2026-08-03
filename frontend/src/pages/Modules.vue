<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const mods = ref([])
const total = ref(0)
const withApi = ref(0)
const withCore = ref(0)

async function loadData() {
  try {
    const r = await endpoints.modules()
    mods.value = (r.data.modules || []).map(m => ({ name: m.name || m, has_api: m.has_api, has_core: m.has_core, status: m.status || '未知' }))
    total.value = mods.value.length
    withApi.value = r.data.with_api || 0
    withCore.value = r.data.with_core || 0
  } catch {}
}

onMounted(loadData)

async function refreshMod(name) {
  try {
    await endpoints.refreshModule(name)
    toast.show(name + '已刷新', 'success')
    await loadData()
  } catch { toast.show('刷新失败', 'error') }
}
function statusClass(s) { return s === 'healthy' ? 'badge-green' : s === 'degraded' ? 'badge-yellow' : 'badge-red' }
function statusLabel(m) { return m.status === 'healthy' ? '正常' : m.status === 'degraded' ? '降级' : m.status }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>模块管理</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ total }}</div><div class="stat-label">总模块</div></div>
        <div class="stat-card"><div class="stat-value">{{ withApi }}</div><div class="stat-label">有API</div></div>
        <div class="stat-card"><div class="stat-value">{{ withCore }}</div><div class="stat-label">有Core</div></div>
      </div>
      <div class="card">
        <div class="card-header">模块列表 ({{ total }})</div>
        <table class="data-table" v-if="mods.length > 0">
          <thead><tr><th>模块名</th><th>API</th><th>Core</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="m in mods" :key="m.name">
              <td><strong>{{ m.name }}</strong></td>
              <td><span class="badge" :class="m.has_api ? 'badge-green' : 'badge-gray'"><Icon :name="m.has_api ? 'check' : 'x'" :size="12" /> {{ m.has_api ? '有' : '无' }}</span></td>
              <td><span class="badge" :class="m.has_core ? 'badge-green' : 'badge-gray'"><Icon :name="m.has_core ? 'check' : 'x'" :size="12" /> {{ m.has_core ? '有' : '无' }}</span></td>
              <td><span class="badge" :class="statusClass(m.status)">{{ statusLabel(m) }}</span></td>
              <td><button class="btn btn-sm" @click="refreshMod(m.name)">刷新</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state"><span class="empty-icon"><Icon name="inbox" :size="20" /></span><p class="empty-text">后端连接后将自动加载模块数据</p></div>
      </div>
    </div>
  </div>
</template>
