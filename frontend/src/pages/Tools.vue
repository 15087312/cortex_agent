<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const tools = ref([])
const events = ref([])
const bySource = ref(0)
const query = ref('')
const selected = ref(null)
const toolInfo = ref(null)
const toolParams = ref('{}')
const toolResult = ref(null)
const infoLoading = ref(false)

onMounted(loadData)

async function loadData() {
  try {
    const [tr, er] = await Promise.all([endpoints.tools().catch(() => null), endpoints.toolEvents(20).catch(() => null)])
    // 后端返回 dict {name: spec} 或数组两种结构
    const rawTools = tr?.data?.tools || {}
    tools.value = Array.isArray(rawTools)
      ? rawTools.map(t => (typeof t === 'string' ? { name: t } : t))
      : Object.keys(rawTools).map(n => ({ name: n, description: (rawTools[n] && typeof rawTools[n] === 'object' && rawTools[n].description) || '' }))
    bySource.value = tr?.data?.by_source ? Object.keys(tr.data.by_source).length : 0
    events.value = er?.data?.events || []
  } catch {}
}

const filteredTools = computed(() => {
  const q = query.value.toLowerCase()
  if (!q) return tools.value
  return tools.value.filter(t => (t.name || '').toLowerCase().includes(q))
})

async function handleSelect(name) {
  selected.value = name
  toolInfo.value = null
  toolResult.value = null
  toolParams.value = '{}'
  infoLoading.value = true
  try {
    const r = await endpoints.toolInfo(name)
    toolInfo.value = r.data
  } catch {
    toolInfo.value = null
  } finally {
    infoLoading.value = false
  }
}

async function handleCall() {
  let params = {}
  try { params = JSON.parse(toolParams.value) } catch { toast.show('JSON格式错误', 'error'); return }
  try { const r = await endpoints.callTool(selected.value, params); toolResult.value = JSON.stringify(r.data, null, 2) } catch (e) { toolResult.value = '错误: ' + (e.body?.error?.message || e.status) }
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>工具管理</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ tools.length }}</div><div class="stat-label">总工具</div></div>
        <div class="stat-card"><div class="stat-value">{{ bySource }}</div><div class="stat-label">来源分类</div></div>
        <div class="stat-card"><div class="stat-value">{{ events.length }}</div><div class="stat-label">最近调用</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="card" style="max-height:400px;overflow-y:auto">
          <div class="card-header">工具列表 ({{ tools.length }})</div>
          <input class="input" v-model="query" placeholder="搜索工具..." style="margin:8px 12px;width:calc(100% - 24px)" />
          <div v-if="filteredTools.length === 0" class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="wrench" :size="20" /></span><p class="empty-text">工具注册后自动出现于此</p></div>
          <div v-else v-for="t in filteredTools" :key="t.name" class="tool-item" :class="{ selected: selected === t.name }" @click="handleSelect(t.name)">
            {{ t.name }}
          </div>
        </div>
        <div class="card" style="max-height:400px;overflow-y:auto">
          <div class="card-header">工具详情</div>
          <div v-if="!selected" class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="list" :size="20" /></span><p class="empty-text">选择一个工具查看详情</p></div>
          <div v-else class="tool-detail">
            <div v-if="infoLoading" style="padding:12px;color:var(--text-muted)">加载中...</div>
            <template v-else>
              <div class="detail-row"><span class="detail-label">描述</span>{{ toolInfo?.description || toolInfo?.name || '-' }}</div>
              <div class="detail-row"><span class="detail-label">来源</span>{{ toolInfo?.source || 'builtin' }}</div>
              <div style="margin-top:12px"><strong>调用工具</strong></div>
              <textarea class="input" v-model="toolParams" style="width:100%;min-height:60px;font-family:var(--font-mono);margin-top:8px"></textarea>
              <div style="margin-top:8px"><button class="btn btn-sm" @click="handleCall"><Icon name="play" :size="14" /> 执行</button></div>
              <pre v-if="toolResult" class="json-output">{{ toolResult }}</pre>
            </template>
          </div>
        </div>
      </div>
      <div class="card" style="margin-top:12px"><div class="card-header">调用历史 ({{ events.length }})</div>
        <table class="data-table" v-if="events.length > 0"><thead><tr><th>工具</th><th>时间</th></tr></thead><tbody><tr v-for="e in events" :key="e.id || e.timestamp"><td>{{ e.tool_name || e.name || '' }}</td><td>{{ formatTime(e.timestamp || e.time) }}</td></tr></tbody></table>
        <div v-else class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="activity" :size="20" /></span><p class="empty-text">工具调用记录将显示在此</p></div>
      </div>
    </div>
  </div>
</template>
