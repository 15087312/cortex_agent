<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'

const sessions = ref([])
const props = defineProps({ compact: { type: Boolean, default: false } })
const selected = ref('')
const graph = ref({ nodes: [], edges: [] })
const loading = ref(true)
const graphLoading = ref(false)

const TIERS = [
  { tier: 'user', label: '用户', icon: 'user', color: '#22c55e' },
  { tier: 'large', label: '总指挥', icon: 'brain', color: '#8b5cf6' },
  { tier: 'supervisor', label: '主管', icon: 'list', color: '#3b82f6' },
  { tier: 'expert', label: '实现专家', icon: 'wrench', color: '#f59e0b' },
]
function tierOf(t) {
  return TIERS.find((x) => x.tier === t) || { tier: '', label: '未知', icon: 'bot', color: '#8b949e' }
}

async function loadSessions() {
  try {
    const r = await endpoints.sessions()
    sessions.value = (r.data || []).sort((a, b) => (b.last_active || '').localeCompare(a.last_active || ''))
    if (!selected.value && sessions.value.length) {
      selected.value = sessions.value[0].session_id
      await loadGraph()
    }
  } catch {} finally { loading.value = false }
}

async function loadGraph() {
  if (!selected.value) { graph.value = { nodes: [], edges: [] }; return }
  graphLoading.value = true
  try {
    const r = await fetch('/api/stream/session/' + encodeURIComponent(selected.value) + '/graph', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    graph.value = d?.data?.graph || { nodes: [], edges: [] }
  } catch { graph.value = { nodes: [], edges: [] } }
  finally { graphLoading.value = false }
}

// ── 动态分层布局：用户 | 总指挥 | 主管 | 实现专家 ──
const layout = computed(() => {
  const nodes = graph.value.nodes
  const known = TIERS.filter((c) => nodes.some((n) => (n.tier || '') === c.tier))
  const hasUnknown = nodes.some((n) => !TIERS.some((c) => c.tier === (n.tier || '')))
  const cols = known.concat(hasUnknown ? [{ tier: '', label: '未知', icon: 'bot', color: '#8b949e' }] : [])
  const W = 1120
  const colW = cols.length ? W / cols.length : W
  const nodeW = 168
  const nodeH = 74
  const top = 36
  const areaH = 430
  const pos = {}
  cols.forEach((col, ci) => {
    const colNodes = nodes.filter((n) => (n.tier || '') === col.tier)
    colNodes.forEach((n, i) => {
      const cx = ci * colW + colW / 2
      const cy = colNodes.length <= 1
        ? top + areaH / 2
        : top + i * (areaH - nodeH) / (colNodes.length - 1) + nodeH / 2
      pos[n.id] = { x: cx - nodeW / 2, y: cy - nodeH / 2, cx, cy, node: n }
    })
  })
  return { pos, W, H: top + areaH + 40 }
})

const viewBox = computed(() => `0 0 ${layout.value.W} ${layout.value.H}`)

// 边：呼唤实线 / 回复虚线，成对错开避免重叠；方向 = 消息发送方向（箭头指向接收者）
const edgeShapes = computed(() => {
  const { pos } = layout.value
  const shapes = []
  for (const e of graph.value.edges) {
    const f = pos[e.from]
    const t = pos[e.to]
    if (!f || !t) continue
    const off = e.type === '呼唤' ? -16 : 16
    const dl = (s) => (s.length > 8 ? s.slice(0, 7) + '…' : s)
    shapes.push({
      ...e,
      fromLabel: dl(f.node.label),
      toLabel: dl(t.node.label),
      hint: `${f.node.label} ${e.type} ${t.node.label}`,
      x1: f.cx, y1: f.cy + off,
      x2: t.cx, y2: t.cy + off,
      midX: (f.cx + t.cx) / 2,
      midY: (f.cy + t.cy) / 2 + off,
    })
  }
  return shapes
})

async function onSessionChange() { await loadGraph() }
onMounted(loadSessions)
</script>

<template>
  <div>
    <div class="page-header" v-if="!compact">
      <h2>会话图谱</h2>
      <button class="btn btn-sm" @click="loadGraph"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body" v-if="!loading">
      <div class="card">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <span>多 Agent 会话执行图谱（谁呼唤谁 / 谁回复谁）</span>
          <select v-model="selected" class="input" style="width:240px;font-size:13px" @change="onSessionChange">
            <option v-for="s in sessions" :key="s.session_id" :value="s.session_id">{{ s.title || s.session_id.slice(0, 16) }}</option>
          </select>
        </div>

        <div class="graph-flow" v-if="!graphLoading">
          <svg class="graph-svg" :viewBox="viewBox" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <marker id="arrow-call" markerWidth="12" markerHeight="12" refX="9" refY="4" orient="auto">
                <path d="M0,0 L9,4.5 L0,9 z" fill="#3b82f6" />
              </marker>
              <marker id="arrow-reply" markerWidth="12" markerHeight="12" refX="9" refY="4" orient="auto">
                <path d="M0,0 L9,4.5 L0,9 z" fill="#22c55e" />
              </marker>
            </defs>
            <!-- 呼唤：实线；回复：虚线。箭头 = 发送方向（指向接收者） -->
            <line
              v-for="e in edgeShapes" :key="e.from + e.to + e.type"
              :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2"
              :class="e.type === '呼唤' ? 'edge-call' : 'edge-reply'"
              :marker-end="'url(#arrow-' + (e.type === '呼唤' ? 'call' : 'reply') + ')'"
            >
              <title>{{ e.hint }}</title>
            </line>
            <text
              v-for="e in edgeShapes" :key="'t' + e.from + e.to + e.type"
              :x="e.midX" :y="e.midY"
              text-anchor="middle" :fill="e.type === '呼唤' ? '#3b82f6' : '#22c55e'"
              class="edge-label"
            >
              <title>{{ e.hint }}</title>{{ e.type }} {{ e.fromLabel }}→{{ e.toLabel }}
            </text>
          </svg>

          <!-- 节点（按 tier 分层） -->
          <div
            v-for="p in Object.values(layout.pos)" :key="p.node.id"
            class="g-node"
            :style="{ left: p.x + 'px', top: p.y + 'px', borderColor: tierOf(p.node.tier).color }"
            :title="'发言 ' + p.node.count + ' 次' + (p.node.last_content ? '\n' + p.node.last_content : '')"
          >
            <div class="g-icon" :style="{ background: tierOf(p.node.tier).color }">
              <Icon :name="tierOf(p.node.tier).icon" :size="16" />
            </div>
            <div class="g-name">{{ p.node.label }}</div>
            <div class="g-sub">{{ tierOf(p.node.tier).label }} · {{ p.node.count }}次</div>
          </div>

          <!-- 空态 -->
          <div v-if="!graph.nodes.length" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--text-muted)">该会话暂无执行图谱（对话触发多 Agent 协作后自动生成）</div>
        </div>
        <div v-else style="text-align:center;padding:60px;color:var(--text-muted)">加载中...</div>

        <!-- 图例 -->
        <div style="display:flex;gap:20px;margin-top:12px;font-size:12px;color:var(--text-muted);align-items:center">
          <span><span class="lg-call"></span> 呼唤（上级委托下级执行）</span>
          <span><span class="lg-reply"></span> 回复（结果反馈）</span>
          <span v-for="t in TIERS" :key="t.tier" style="display:flex;align-items:center;gap:4px"><span class="lg-dot" :style="{ background: t.color }"></span>{{ t.label }}</span>
        </div>
      </div>
    </div>
    <div class="page-body" v-else style="text-align:center;padding:60px;color:var(--text-muted)">加载中...</div>
  </div>
</template>

<style scoped>
.graph-flow { position: relative; height: 490px; overflow: hidden; }
.graph-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.edge-call { stroke: #3b82f6; stroke-width: 1.6; }
.edge-reply { stroke: #22c55e; stroke-width: 1.6; stroke-dasharray: 6 4; }
.edge-label { font-size: 11px; font-weight: 600; }
.edge-call:hover, .edge-reply:hover { stroke-width: 3; }
.g-node {
  position: absolute; width: 168px; padding: 8px; border-radius: 12px;
  background: var(--bg-secondary); border: 1.5px solid #8b5cf6;
  box-shadow: 0 6px 18px rgba(0,0,0,.12); text-align: center; z-index: 2;
}
.g-icon {
  width: 32px; height: 32px; border-radius: 9px; margin: 0 auto 6px;
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 4px 10px rgba(0,0,0,.2);
}
.g-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.g-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.lg-call { display: inline-block; width: 22px; height: 0; border-top: 2px solid #3b82f6; vertical-align: middle; margin-right: 4px; }
.lg-reply { display: inline-block; width: 22px; height: 0; border-top: 2px dashed #22c55e; vertical-align: middle; margin-right: 4px; }
.lg-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
</style>
