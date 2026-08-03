<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const nodes = ref([])
const edges = ref([])
const stats = ref({})
const detail = ref(null)
const tree = ref(null)
const treeLoading = ref(false)

onMounted(loadData)

async function loadData() {
  try { const r = await endpoints.causalGraph(); nodes.value = r.data.nodes || []; edges.value = r.data.edges || []; stats.value = r.data.stats || {} } catch {}
}
async function handleShowTree(id) {
  treeLoading.value = true
  try {
    const [nr, tr] = await Promise.all([endpoints.causalNode(id), endpoints.causalTree(id, 3)])
    detail.value = nr.data
    tree.value = tr.data
  } catch {
    toast.show('加载因果链失败', 'error')
  } finally {
    treeLoading.value = false
  }
}
function nodeBadgeClass(type) { return type === 'root' ? 'badge-green' : type === 'cause' ? 'badge-yellow' : 'badge-blue' }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>因果图</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ stats.total_nodes || nodes.length }}</div><div class="stat-label">节点</div></div>
        <div class="stat-card"><div class="stat-value">{{ stats.total_edges || edges.length }}</div><div class="stat-label">边</div></div>
        <div class="stat-card"><div class="stat-value">{{ stats.total_events || 0 }}</div><div class="stat-label">关联事件</div></div>
        <div class="stat-card"><div class="stat-value">{{ stats.linked_events || 0 }}</div><div class="stat-label">已链接</div></div>
      </div>
      <div class="card">
        <div class="card-header">因果节点</div>
        <table class="data-table" v-if="nodes.length > 0">
          <thead><tr><th>标签</th><th>类型</th><th>置信度</th><th>事件数</th><th>操作</th></tr></thead>
          <tbody><tr v-for="n in nodes" :key="n.id"><td><strong>{{ n.label }}</strong></td><td><span class="badge" :class="nodeBadgeClass(n.type)">{{ n.type }}</span></td><td>{{ (n.confidence||0).toFixed(2) }}</td><td>{{ n.event_count||0 }}</td><td><button class="btn btn-sm" @click="handleShowTree(n.id)" :disabled="treeLoading">因果链</button></td></tr></tbody>
        </table>
        <div v-else class="empty-state" style="padding:40px"><span class="empty-icon"><Icon name="network" :size="20" /></span><p class="empty-text">因果数据将在此显示</p></div>
      </div>
      <div v-if="detail" class="card" style="margin-top:12px">
        <div class="card-header">
          因果链: {{ tree?.anchor?.label || detail.node?.label }}
          <span v-if="treeLoading" style="margin-left:8px;font-size:12px;color:var(--text-muted)">加载中...</span>
        </div>
        <table class="data-table"><tbody><tr><td style="color:var(--text-muted)">节点</td><td>{{ detail.node?.label }}</td></tr><tr><td style="color:var(--text-muted)">类型</td><td>{{ detail.node?.type }}</td></tr></tbody></table>
         <div v-if="detail.predecessors?.length" style="margin-top:12px"><strong style="font-size:13px;color:var(--text-muted)">前驱节点</strong><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"><span v-for="p in detail.predecessors" :key="p.id" class="causal-pill predecessor" @click="handleShowTree(p.id)">{{ p.label }}</span></div></div>
         <div v-if="detail.successors?.length" style="margin-top:12px"><strong style="font-size:13px;color:var(--text-muted)">后继节点</strong><div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:8px"><span v-for="s in detail.successors" :key="s.id" class="causal-pill successor" @click="handleShowTree(s.id)">{{ s.label }}</span></div></div>
      </div>
    </div>
  </div>
</template>
