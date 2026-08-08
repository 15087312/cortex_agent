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

// ── SVG 关系图（力导向布局）──
const GRAPH_W = 900
const GRAPH_H = 500
const positions = ref({})
const displayNodes = ref([])
const displayEdges = ref([])

function nodeColor(type) {
  return { root: '#22C55E', cause: '#F59E0B', effect: '#3B82F6' }[type] || '#94A3B8'
}
function nodeRadius(n) {
  const base = 10 + Math.min(12, (n.event_count || 0) * 0.4)
  return Math.min(30, base)
}
function computeLayout(limit = 80) {
  const sorted = [...nodes.value]
    .sort((a, b) => (b.event_count || 0) - (a.event_count || 0))
    .slice(0, limit)
  const ids = new Set(sorted.map((n) => n.id))
  const relEdges = edges.value.filter((e) => ids.has(e.from) && ids.has(e.to))
  const pos = {}
  const count = sorted.length
  sorted.forEach((node, i) => {
    const angle = (2 * Math.PI * i) / Math.max(1, count)
    const radius = Math.min(GRAPH_W, GRAPH_H) * 0.4
    pos[node.id] = { x: GRAPH_W / 2 + radius * Math.cos(angle), y: GRAPH_H / 2 + radius * Math.sin(angle) }
  })
  for (let iter = 0; iter < 120; iter++) {
    const keys = Object.keys(pos)
    for (let i = 0; i < keys.length; i++) {
      for (let j = i + 1; j < keys.length; j++) {
        const pointA = pos[keys[i]], pointB = pos[keys[j]]
        const dx = pointA.x - pointB.x, dy = pointA.y - pointB.y
        const dist = Math.max(0.1, Math.hypot(dx, dy))
        const force = 800 / (dist * dist)
        const fx = (dx / dist) * force, fy = (dy / dist) * force
        pointA.x += fx * 0.5; pointA.y += fy * 0.5
        pointB.x -= fx * 0.5; pointB.y -= fy * 0.5
      }
    }
    relEdges.forEach((edge) => {
      const pointA = pos[edge.from], pointB = pos[edge.to]
      if (!pointA || !pointB) return
      const dx = pointB.x - pointA.x, dy = pointB.y - pointA.y
      const dist = Math.max(0.1, Math.hypot(dx, dy))
      const force = dist * 0.01
      pointA.x += (dx / dist) * force; pointA.y += (dy / dist) * force
      pointB.x -= (dx / dist) * force; pointB.y -= (dy / dist) * force
    })
    keys.forEach((id) => {
      pos[id].x += (GRAPH_W / 2 - pos[id].x) * 0.002
      pos[id].y += (GRAPH_H / 2 - pos[id].y) * 0.002
    })
  }
  positions.value = pos
  displayNodes.value = sorted
  displayEdges.value = relEdges
}

onMounted(loadData)

async function loadData() {
  try {
    const r = await endpoints.causalGraph()
    nodes.value = r.data.nodes || []
    edges.value = r.data.edges || []
    stats.value = r.data.stats || {}
    computeLayout()
  } catch {}
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

      <!-- 因果关系图（SVG 力导向） -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">因果图谱（前 {{ displayNodes.length }} 节点 · 点击节点查看因果链）</div>
        <svg class="graph-svg" :viewBox="`0 0 ${GRAPH_W} ${GRAPH_H}`" xmlns="http://www.w3.org/2000/svg" v-if="displayNodes.length">
          <line
            v-for="edge in displayEdges" :key="edge.id"
            :x1="positions[edge.from]?.x" :y1="positions[edge.from]?.y"
            :x2="positions[edge.to]?.x" :y2="positions[edge.to]?.y"
            class="graph-line" stroke-width="1.5"
          />
          <g v-for="node in displayNodes" :key="node.id" @click="handleShowTree(node.id)" style="cursor:pointer">
            <circle
              :cx="positions[node.id]?.x" :cy="positions[node.id]?.y"
              :r="nodeRadius(node)" :fill="nodeColor(node.type)" fill-opacity="0.85"
            >
              <title>{{ node.label }}（{{ node.type }} · 置信度 {{ (node.confidence||0).toFixed(2) }} · {{ node.event_count||0 }} 事件）</title>
            </circle>
            <text :x="positions[node.id]?.x" :y="positions[node.id]?.y" text-anchor="middle" :dy="3" class="graph-label">{{ node.label.slice(0, 8) }}</text>
          </g>
        </svg>
        <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">因果数据将在此显示</div>
        <div style="display:flex;gap:16px;font-size:12px;color:var(--text-muted);padding-top:8px;flex-wrap:wrap">
          <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#22C55E;margin-right:4px"></span>root</span>
          <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#F59E0B;margin-right:4px"></span>cause</span>
          <span><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#3B82F6;margin-right:4px"></span>effect</span>
        </div>
      </div>

      <div class="card" style="margin-top:12px">
        <div class="card-header">因果节点</div>
        <table class="data-table" v-if="nodes.length > 0">
          <thead><tr><th>标签</th><th>类型</th><th>置信度</th><th>事件数</th><th>操作</th></tr></thead>
          <tbody><tr v-for="node in nodes" :key="node.id"><td><strong>{{ node.label }}</strong></td><td><span class="badge" :class="nodeBadgeClass(node.type)">{{ node.type }}</span></td><td>{{ (node.confidence||0).toFixed(2) }}</td><td>{{ node.event_count||0 }}</td><td><button class="btn btn-sm" @click="handleShowTree(node.id)" :disabled="treeLoading">因果链</button></td></tr></tbody>
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

<style scoped>
.graph-svg {
  width: 100%; height: 500px; background: var(--bg-secondary);
  border-radius: 8px; border: 1px solid var(--border);
}
.graph-line { stroke: #8b5cf6; stroke-opacity: .35; }
.graph-label { fill: #cbd5e1; font-size: 11px; pointer-events: none; }
</style>
