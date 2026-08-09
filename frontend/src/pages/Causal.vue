<script setup>
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
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

// ── SVG 力导向图 ──
const GRAPH_W = 900
const GRAPH_H = 500
const positions = ref({})
const displayNodes = ref([])
const displayEdges = ref([])

// ── 交互状态 ──
const hoveredNode = ref(null)
const graphContainer = ref(null)
const viewBox = ref({ x: 0, y: 0, w: GRAPH_W, h: GRAPH_H })
const isDragging = ref(false)
const dragStart = ref({ x: 0, y: 0 })
const viewBoxStart = ref({ x: 0, y: 0 })
const zoom = ref(1)

const connectedNodeIds = computed(() => {
  if (!hoveredNode.value) return new Set()
  const ids = new Set([hoveredNode.value])
  displayEdges.value.forEach(e => {
    if (e.from === hoveredNode.value) ids.add(e.to)
    if (e.to === hoveredNode.value) ids.add(e.from)
  })
  return ids
})

function nodeColor(type) {
  return { root: '#22C55E', cause: '#F59E0B', effect: '#3B82F6' }[type] || '#94A3B8'
}

function nodeStroke(type) {
  return { root: '#16A34A', cause: '#D97706', effect: '#2563EB' }[type] || '#64748B'
}

function nodeRadius(n) {
  const base = 10 + Math.min(12, (n.event_count || 0) * 0.4)
  return Math.min(30, base)
}

function nodeOpacity(node) {
  if (!hoveredNode.value) return 1
  return connectedNodeIds.value.has(node.id) ? 1 : 0.15
}

function edgeOpacity(edge) {
  if (!hoveredNode.value) return 0.35
  return (edge.from === hoveredNode.value || edge.to === hoveredNode.value) ? 0.9 : 0.06
}

function edgeWidth(edge) {
  if (!hoveredNode.value) return 1.5
  return (edge.from === hoveredNode.value || edge.to === hoveredNode.value) ? 2.5 : 1
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

// ── 缩放/平移 ──
function onWheel(e) {
  e.preventDefault()
  const scale = e.deltaY > 0 ? 1.12 : 0.88
  const svgRect = graphContainer.value.getBoundingClientRect()
  const mx = (e.clientX - svgRect.left) / svgRect.width
  const my = (e.clientY - svgRect.top) / svgRect.height
  const vb = viewBox.value
  const newW = vb.w * scale
  const newH = vb.h * scale
  viewBox.value = {
    x: vb.x + (vb.w - newW) * mx,
    y: vb.y + (vb.h - newH) * my,
    w: newW,
    h: newH
  }
  zoom.value *= scale
}

function onMouseDown(e) {
  if (e.target.closest('g')) return
  isDragging.value = true
  dragStart.value = { x: e.clientX, y: e.clientY }
  viewBoxStart.value = { x: viewBox.value.x, y: viewBox.value.y }
}

function onMouseMove(e) {
  if (!isDragging.value) return
  const svgRect = graphContainer.value.getBoundingClientRect()
  const dx = ((e.clientX - dragStart.value.x) / svgRect.width) * viewBox.value.w
  const dy = ((e.clientY - dragStart.value.y) / svgRect.height) * viewBox.value.h
  viewBox.value.x = viewBoxStart.value.x - dx
  viewBox.value.y = viewBoxStart.value.y - dy
}

function onMouseUp() { isDragging.value = false }

function resetView() {
  viewBox.value = { x: 0, y: 0, w: GRAPH_W, h: GRAPH_H }
  zoom.value = 1
}

onMounted(() => {
  loadData()
  window.addEventListener('mouseup', onMouseUp)
})
onBeforeUnmount(() => {
  window.removeEventListener('mouseup', onMouseUp)
})

async function loadData() {
  try {
    const r = await endpoints.causalGraph()
    nodes.value = r.data.nodes || []
    edges.value = r.data.edges || []
    stats.value = r.data.stats || {}
    computeLayout()
    resetView()
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

function nodeBadgeClass(type) {
  return type === 'root' ? 'badge-green' : type === 'cause' ? 'badge-yellow' : 'badge-blue'
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>因果图</h2>
      <div style="display:flex;gap:8px;align-items:center">
        <span v-if="zoom !== 1" style="font-size:12px;color:var(--text-muted)">{{ Math.round(zoom * 100) }}%</span>
        <button class="btn btn-sm" @click="resetView" v-if="zoom !== 1">重置视图</button>
        <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
      </div>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ stats.total_nodes || nodes.length }}</div><div class="stat-label">节点</div></div>
        <div class="stat-card"><div class="stat-value">{{ stats.total_edges || edges.length }}</div><div class="stat-label">边</div></div>
        <div class="stat-card"><div class="stat-value">{{ stats.total_events || 0 }}</div><div class="stat-label">关联事件</div></div>
        <div class="stat-card"><div class="stat-value">{{ stats.linked_events || 0 }}</div><div class="stat-label">已链接</div></div>
      </div>

      <!-- 因果关系图 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
          <span>因果图谱（前 {{ displayNodes.length }} 节点 · 点击节点查看因果链）</span>
          <span v-if="hoveredNode" style="font-size:12px;color:var(--text-muted)">
            {{ displayNodes.find(n => n.id === hoveredNode)?.label }} · {{ connectedNodeIds.size - 1 }} 个关联
          </span>
        </div>
        <div
          ref="graphContainer"
          class="graph-container"
          @wheel.prevent="onWheel"
          @mousedown="onMouseDown"
          @mousemove="onMouseMove"
          @mouseup="onMouseUp"
          @mouseleave="hoveredNode = null"
        >
          <svg
            class="graph-svg"
            :viewBox="`${viewBox.x} ${viewBox.y} ${viewBox.w} ${viewBox.h}`"
            xmlns="http://www.w3.org/2000/svg"
            v-if="displayNodes.length"
          >
            <defs>
              <marker id="arrow" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 4 L 0 8 z" fill="#8b5cf6" opacity="0.6" />
              </marker>
              <marker id="arrow-active" viewBox="0 0 10 8" refX="10" refY="4" markerWidth="8" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 4 L 0 8 z" fill="#8b5cf6" />
              </marker>
              <filter id="glow">
                <feGaussianBlur stdDeviation="2.5" result="blur" />
                <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
              </filter>
              <filter id="shadow">
                <feDropShadow dx="0" dy="1" stdDeviation="2" flood-opacity="0.15" />
              </filter>
            </defs>

            <!-- 边 -->
            <line
              v-for="edge in displayEdges" :key="edge.id"
              :x1="positions[edge.from]?.x" :y1="positions[edge.from]?.y"
              :x2="positions[edge.to]?.x" :y2="positions[edge.to]?.y"
              class="graph-line"
              :stroke-width="edgeWidth(edge)"
              :stroke-opacity="edgeOpacity(edge)"
              :marker-end="(hoveredNode && (edge.from === hoveredNode || edge.to === hoveredNode)) ? 'url(#arrow-active)' : 'url(#arrow)'"
            />

            <!-- 节点 -->
            <g
              v-for="node in displayNodes" :key="node.id"
              @click="handleShowTree(node.id)"
              @mouseenter="hoveredNode = node.id"
              @mouseleave="hoveredNode = null"
              class="graph-node"
              :style="{ cursor: 'pointer', opacity: nodeOpacity(node), transition: 'opacity 0.2s ease' }"
            >
              <!-- 外发光 -->
              <circle
                v-if="hoveredNode === node.id"
                :cx="positions[node.id]?.x" :cy="positions[node.id]?.y"
                :r="nodeRadius(node) + 5"
                :fill="nodeColor(node.type)" fill-opacity="0.12"
                filter="url(#glow)"
              />
              <!-- 主圆 -->
              <circle
                :cx="positions[node.id]?.x" :cy="positions[node.id]?.y"
                :r="nodeRadius(node)" :fill="nodeColor(node.type)" fill-opacity="0.85"
                :stroke="nodeStroke(node.type)" :stroke-width="hoveredNode === node.id ? 2.5 : 1.5"
                filter="url(#shadow)"
                class="graph-circle"
              />
              <!-- 置信度环 -->
              <circle
                :cx="positions[node.id]?.x" :cy="positions[node.id]?.y"
                :r="nodeRadius(node) + 1"
                fill="none" :stroke="nodeColor(node.type)" stroke-width="1.5"
                stroke-opacity="0.25"
                :stroke-dasharray="`${(node.confidence || 0) * 2 * Math.PI * (nodeRadius(node) + 1)} 999`"
                :stroke-linecap="'round'"
                :transform="`rotate(-90 ${positions[node.id]?.x} ${positions[node.id]?.y})`"
              />
              <!-- 标签 -->
              <text
                :x="positions[node.id]?.x" :y="positions[node.id]?.y"
                text-anchor="middle" dy="4" class="graph-label"
                :font-weight="hoveredNode === node.id ? '600' : '400'"
                :fill="hoveredNode === node.id ? '#ffffff' : '#cbd5e1'"
              >{{ node.label.slice(0, 8) }}</text>
            </g>
          </svg>
          <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">因果数据将在此显示</div>
        </div>
        <!-- 图例 -->
        <div class="graph-legend">
          <span class="legend-item"><span class="legend-dot" style="background:#22C55E"></span>root</span>
          <span class="legend-item"><span class="legend-dot" style="background:#F59E0B"></span>cause</span>
          <span class="legend-item"><span class="legend-dot" style="background:#3B82F6"></span>effect</span>
          <span class="legend-sep"></span>
          <span class="legend-hint">滚轮缩放 · 拖拽平移 · 点击节点</span>
        </div>
      </div>

      <!-- 因果节点表 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">因果节点</div>
        <table class="data-table" v-if="nodes.length > 0">
          <thead><tr><th>标签</th><th>类型</th><th>置信度</th><th>事件数</th><th>操作</th></tr></thead>
          <tbody>
            <tr
              v-for="node in nodes" :key="node.id"
              :class="{ 'row-highlight': hoveredNode === node.id }"
              @mouseenter="hoveredNode = node.id"
              @mouseleave="hoveredNode = null"
            >
              <td><strong>{{ node.label }}</strong></td>
              <td><span class="badge" :class="nodeBadgeClass(node.type)">{{ node.type }}</span></td>
              <td>
                <span class="confidence-bar">
                  <span class="confidence-fill" :style="{ width: (node.confidence||0)*100+'%', background: nodeColor(node.type) }"></span>
                </span>
                <span style="font-size:12px;margin-left:6px;color:var(--text-muted)">{{ (node.confidence||0).toFixed(2) }}</span>
              </td>
              <td>{{ node.event_count||0 }}</td>
              <td><button class="btn btn-sm" @click="handleShowTree(node.id)" :disabled="treeLoading">因果链</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state" style="padding:40px"><span class="empty-icon"><Icon name="network" :size="20" /></span><p class="empty-text">因果数据将在此显示</p></div>
      </div>

      <!-- 因果链详情 -->
      <div v-if="detail" class="card chain-card" style="margin-top:12px">
        <div class="card-header">
          <span style="display:flex;align-items:center;gap:8px">
            <Icon name="network" :size="16" />
            因果链: {{ tree?.anchor?.label || detail.node?.label }}
          </span>
          <span v-if="treeLoading" class="chain-loading">加载中...</span>
        </div>

        <div class="chain-detail-body">
          <!-- 当前节点 -->
          <div class="chain-anchor">
            <div class="chain-anchor-dot" :style="{ background: nodeColor(detail.node?.type) }"></div>
            <div class="chain-anchor-info">
              <div class="chain-anchor-label">{{ detail.node?.label }}</div>
              <div style="display:flex;gap:8px;align-items:center;margin-top:4px">
                <span class="badge" :class="nodeBadgeClass(detail.node?.type)">{{ detail.node?.type }}</span>
                <span v-if="detail.node?.confidence" style="font-size:12px;color:var(--text-muted)">置信度 {{ (detail.node?.confidence||0).toFixed(2) }}</span>
              </div>
            </div>
          </div>

          <!-- 前驱 -->
          <div v-if="detail.predecessors?.length" class="chain-section">
            <div class="chain-section-title">
              <span class="chain-arrow chain-arrow-back">←</span>
              前驱节点
              <span class="chain-count">{{ detail.predecessors.length }}</span>
            </div>
            <div class="chain-pills">
              <span
                v-for="p in detail.predecessors" :key="p.id"
                class="causal-pill predecessor"
                @click="handleShowTree(p.id)"
              >
                <span class="pill-dot predecessor-dot"></span>
                {{ p.label }}
                <span v-if="p.confidence" class="pill-conf">{{ (p.confidence||0).toFixed(2) }}</span>
              </span>
            </div>
          </div>

          <!-- 后继 -->
          <div v-if="detail.successors?.length" class="chain-section">
            <div class="chain-section-title">
              <span class="chain-arrow chain-arrow-fwd">→</span>
              后继节点
              <span class="chain-count">{{ detail.successors.length }}</span>
            </div>
            <div class="chain-pills">
              <span
                v-for="s in detail.successors" :key="s.id"
                class="causal-pill successor"
                @click="handleShowTree(s.id)"
              >
                <span class="pill-dot successor-dot"></span>
                {{ s.label }}
                <span v-if="s.confidence" class="pill-conf">{{ (s.confidence||0).toFixed(2) }}</span>
              </span>
            </div>
          </div>

          <!-- 空状态 -->
          <div v-if="!detail.predecessors?.length && !detail.successors?.length" style="padding:16px;text-align:center;color:var(--text-muted);font-size:13px">
            该节点暂无因果关系数据
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.graph-container {
  position: relative;
  overflow: hidden;
  border-radius: 0 0 8px 8px;
  cursor: grab;
}
.graph-container:active { cursor: grabbing; }
.graph-svg {
  width: 100%; height: 500px; background: var(--bg-secondary);
  border-radius: 0 0 8px 8px; border-top: 1px solid var(--border-light);
}
.graph-line {
  stroke: #8b5cf6;
  stroke-opacity: .35;
  transition: stroke-opacity 0.2s ease, stroke-width 0.2s ease;
}
.graph-node { transition: opacity 0.2s ease; }
.graph-circle { transition: stroke-width 0.15s ease; }
.graph-label {
  fill: #cbd5e1;
  font-size: 11px;
  pointer-events: none;
  transition: fill 0.15s ease, font-weight 0.15s ease;
  text-shadow: 0 1px 3px rgba(0,0,0,0.6);
}

/* 图例 */
.graph-legend {
  display: flex; align-items: center; gap: 16px;
  padding: 8px 12px; font-size: 12px; color: var(--text-muted);
  border-top: 1px solid var(--border-light);
}
.legend-item { display: flex; align-items: center; gap: 5px; }
.legend-dot {
  display: inline-block; width: 10px; height: 10px;
  border-radius: 50%; flex-shrink: 0;
}
.legend-sep { width: 1px; height: 14px; background: var(--border-light); }
.legend-hint { font-size: 11px; color: var(--text-muted); opacity: 0.7; }

/* 表格行高亮 */
.row-highlight { background: var(--accent-bg) !important; }

/* 置信度条 */
.confidence-bar {
  display: inline-block; width: 48px; height: 4px;
  background: var(--bg-tertiary); border-radius: 2px; vertical-align: middle;
  overflow: hidden;
}
.confidence-fill { display: block; height: 100%; border-radius: 2px; transition: width 0.3s ease; }

/* 因果链卡片 */
.chain-card { border-left: 3px solid var(--accent); }
.chain-loading {
  margin-left: 8px; font-size: 12px; color: var(--text-muted);
  animation: pulse 1.5s infinite;
}
.chain-detail-body { padding: 0 16px 16px; }

/* 锚点节点 */
.chain-anchor {
  display: flex; align-items: center; gap: 12px;
  padding: 12px 14px; margin: 12px 0;
  background: var(--bg-tertiary); border-radius: var(--radius);
  border: 1px solid var(--border-light);
}
.chain-anchor-dot {
  width: 14px; height: 14px; border-radius: 50%;
  flex-shrink: 0; box-shadow: 0 0 6px rgba(0,0,0,0.15);
}
.chain-anchor-label { font-weight: 600; font-size: 14px; color: var(--text-primary); }

/* 因果链分区 */
.chain-section { margin-top: 14px; }
.chain-section-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 500; color: var(--text-secondary);
  margin-bottom: 8px;
}
.chain-count {
  display: inline-flex; align-items: center; justify-content: center;
  min-width: 18px; height: 18px; padding: 0 5px;
  background: var(--bg-tertiary); border-radius: 9px;
  font-size: 11px; color: var(--text-muted);
}
.chain-arrow {
  display: inline-flex; align-items: center; justify-content: center;
  width: 20px; height: 20px; border-radius: 4px;
  font-size: 13px; font-weight: 700;
}
.chain-arrow-back { background: var(--accent-bg); color: var(--accent); }
.chain-arrow-fwd { background: rgba(34,197,94,0.1); color: var(--success); }

/* 药丸样式增强 */
.chain-pills { display: flex; flex-wrap: wrap; gap: 6px; }
.pill-dot {
  display: inline-block; width: 6px; height: 6px;
  border-radius: 50%; flex-shrink: 0; margin-right: 2px;
}
.predecessor-dot { background: var(--accent); }
.successor-dot { background: var(--success); }
.pill-conf {
  font-size: 10px; opacity: 0.6; margin-left: 4px;
  font-family: var(--font-mono);
}
</style>
