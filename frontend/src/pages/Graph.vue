<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'

const models = ref(null)
const tools = ref([])
const agents = ref([])
const loading = ref(true)

// ── 列定义（仅样式映射，节点数据从后端动态加载）──
const COLS = [
  { key: 'input', label: '用户输入', icon: 'user', color: '#22c55e' },
  { key: 'large', label: '总指挥', icon: 'brain', color: '#8b5cf6' },
  { key: 'supervisor', label: '主管', icon: 'list', color: '#3b82f6' },
  { key: 'expert', label: '实现专家', icon: 'wrench', color: '#f59e0b' },
  { key: 'tools', label: '工具', icon: 'wrench', color: '#06b6d4' },
]

const summary = computed(() => models.value?.summary || {})
const totalTools = computed(() => tools.value.length || 0)
const activeRunners = computed(() => models.value?.runners?.length || 0)
const commonTools = computed(() => (Array.isArray(tools.value) ? tools.value.slice(0, 6) : []))

// 按 tier 分组的真实 Agent（排除 customer 特殊角色）
const groups = computed(() => {
  const g = { large: [], supervisor: [], expert: [] }
  for (const a of agents.value) {
    if (a.role === 'customer') continue
    if (g[a.tier]) g[a.tier].push(a)
  }
  return g
})

// ── 动态布局：5 列，列内节点竖排，坐标全部计算 ──
const W = 1000
const COL_W = 200
const NODE_W = 160
const NODE_H = 86
const TOP = 30
const AREA_H = 400

function colX(colIdx) { return 10 + colIdx * COL_W }
function nodeCount(colIdx) {
  const c = COLS[colIdx]
  if (c.key === 'input' || c.key === 'tools') return 1
  return groups[c.key]?.length || 1
}
function nodeY(colIdx, i, n) {
  if (n <= 1) return TOP + AREA_H / 2 - NODE_H / 2
  const step = (AREA_H - NODE_H) / (n - 1)
  return TOP + i * step
}
function columnCenterY(colIdx) {
  const n = nodeCount(colIdx)
  if (n <= 1) return TOP + AREA_H / 2
  const step = (AREA_H - NODE_H) / (n - 1)
  return TOP + ((n - 1) / 2) * step + NODE_H / 2
}
const viewH = computed(() => TOP + AREA_H + 40)
const viewBox = computed(() => `0 0 ${W} ${viewH.value}`)

// 每列节点数据
function colNodes(col) {
  if (col.key === 'input') {
    return [{ id: 'input', label: '用户输入', sub: '语音 / 文本 / 感知', icon: 'user', color: '#22c55e' }]
  }
  if (col.key === 'tools') {
    return [{ id: 'tools', label: `工具 (${totalTools.value})`, sub: activeRunners.value ? '运行中' : '空闲', icon: 'wrench', color: '#06b6d4' }]
  }
  const list = groups[col.key] || []
  if (!list.length) {
    return [{ id: col.key, label: col.label, sub: '(无配置)', icon: col.icon, color: col.color }]
  }
  return list.map((a) => ({
    id: a.role,
    label: a.name || a.role,
    sub: a.role,
    icon: col.icon,
    color: col.color,
    personality: a.personality,
    model_id: a.model_id,
    tier: a.tier,
  }))
}

// 相邻列聚合连线
const edges = computed(() => {
  const e = []
  for (let c = 0; c < COLS.length - 1; c++) {
    const sx = colX(c) + NODE_W
    const dx = colX(c + 1)
    const sy = columnCenterY(c)
    const dy = columnCenterY(c + 1)
    e.push({ sx, sy, dx, dy })
  }
  return e
})

// 每列的活跃/上限汇总（仅 tier 列）
function tierActive(tier) { return summary[tier]?.active || 0 }
function tierMax(tier) { return summary[tier]?.max || 1 }

// ── 节点点击 → 预览该 Agent 的实际 system prompt 文本（对齐 DeterminFlow SystemPromptPage）──
const preview = ref({ role: '', name: '', text: '', loading: false })
const previewOpen = ref(false)

async function showPreview(node) {
  if (!node.role || node.role === 'input' || node.role === 'tools') return
  previewOpen.value = true
  preview.value = { role: node.role, name: node.label, text: '', loading: true }
  try {
    const r = await fetch('/management/orchestration/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: node.role, tier: node.tier || 'large' }),
    })
    const d = await r.json()
    preview.value.text = d?.data?.prompt || '(预览失败)'
  } catch {} finally { preview.value.loading = false }
}

async function loadData() {
  try {
    const [modelsResp, toolsResp, orchResp] = await Promise.all([
      endpoints.models().catch(() => null),
      endpoints.tools().catch(() => null),
      fetch('/management/orchestration', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
    ])
    models.value = modelsResp?.data || null
    agents.value = orchResp?.data?.agents || []
    // /tools 返回结构是 { tools: {name: {...}} } dict——统一取数组
    const toolsData = toolsResp?.data
    const toolsObj = toolsData?.tools || {}
    tools.value = Array.isArray(toolsObj)
      ? toolsObj
      : Object.keys(toolsObj).map((n) => ({ name: n, ...(typeof toolsObj[n] === 'object' ? toolsObj[n] : {}) }))
  } catch {} finally { loading.value = false }
}

let timer = null
onMounted(async () => { await loadData(); timer = setInterval(loadData, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-header">
      <h2>编排图谱</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="card">
        <div class="card-header">多 Agent 编排流（用户 → 总指挥 → 主管 → 实现专家 → 工具）—— Agent 定义来自编排配置，动态渲染</div>
        <div class="graph-flow" v-if="!loading">
          <svg class="graph-svg" :viewBox="viewBox" xmlns="http://www.w3.org/2000/svg">
            <defs>
              <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6 z" fill="#8b5cf6" />
              </marker>
            </defs>
            <line v-for="(e, i) in edges" :key="i" :x1="e.sx" :y1="e.sy" :x2="e.dx" :y2="e.dy" class="graph-line" marker-end="url(#arrow)" />
          </svg>

          <!-- 动态节点 -->
          <template v-for="(col, ci) in COLS" :key="col.key">
            <div
              v-for="(node, ni) in colNodes(col)"
              :key="node.id"
              class="g-node"
              :class="{ clickable: node.role && node.role !== 'input' && node.role !== 'tools' }"
              :style="{ left: colX(ci) + 'px', top: nodeY(ci, ni, colNodes(col).length) + 'px', borderColor: node.color }"
              :title="node.personality ? node.personality : ''"
              @click="showPreview(node)"
            >
              <div class="g-icon" :style="{ background: node.color }"><Icon :name="node.icon" :size="18" /></div>
              <div class="g-name">{{ node.label }}</div>
              <div class="g-sub">{{ node.sub }}</div>
              <div v-if="node.tier" class="g-active" :style="{ color: node.color }">
                <span class="g-dot" :style="{ background: tierActive(node.tier) > 0 ? '#22c55e' : '#94a3b8' }"></span>
                活跃 {{ tierActive(node.tier) }} / {{ tierMax(node.tier) }}
              </div>
            </div>
          </template>

          <!-- 工具徽标 -->
          <div class="g-tools" :style="{ left: colX(4) + 8 + 'px', top: TOP + AREA_H - 30 + 'px' }">
            <span v-for="tool in commonTools" :key="tool.name" class="badge" style="font-family:monospace;background:rgba(6,182,212,.1);color:#06b6d4;font-size:10px">{{ tool.name }}</span>
            <span v-if="totalTools > 6" class="badge badge-gray" style="font-size:10px">+{{ totalTools - 6 }}</span>
          </div>
        </div>
        <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>
      </div>

      <!-- 层级明细（动态） -->
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px">
        <div v-for="col in COLS.slice(1, 4)" :key="col.key" class="card">
          <div class="card-header" style="color:var(--text-secondary)">{{ col.label }}（{{ col.key }}）</div>
          <div v-for="a in groups[col.key] || []" :key="a.role" style="padding:8px 0;border-bottom:1px solid var(--border);font-size:12px">
            <div style="display:flex;align-items:center;gap:8px">
              <span style="font-weight:600">{{ a.name }}</span>
              <span class="badge badge-gray" style="font-family:monospace;font-size:10px">{{ a.role }}</span>
              <span class="badge badge-blue" style="font-size:10px">{{ a.model_id || '默认模型' }}</span>
            </div>
            <div v-if="a.personality" style="color:var(--text-muted);margin-top:2px">{{ a.personality }}</div>
          </div>
          <div v-if="!groups[col.key]?.length" style="text-align:center;padding:12px;color:var(--text-muted);font-size:12px">暂无该层 Agent</div>
        </div>
      </div>
    </div>

    <!-- system prompt 文本预览弹窗（对齐 DeterminFlow SystemPromptPage） -->
    <div v-if="previewOpen" class="pv-overlay" @click.self="previewOpen = false">
      <div class="pv-panel">
        <div class="pv-head">
          <span style="font-weight:600">System Prompt 预览：{{ preview.name }}（{{ preview.role }}）</span>
          <button class="btn btn-sm" @click="previewOpen = false"><Icon name="x" :size="14" /> 关闭</button>
        </div>
        <pre v-if="!preview.loading" class="pv-text">{{ preview.text || '加载中...' }}</pre>
        <div v-else style="text-align:center;padding:30px;color:var(--text-muted)">加载中...</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.graph-flow { position: relative; height: 470px; overflow: hidden; }
.graph-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.graph-line { stroke: #8b5cf6; stroke-width: 2; stroke-dasharray: 6 4; }
.g-node {
  position: absolute; width: 160px; padding: 10px; border-radius: 12px;
  background: var(--bg-secondary); border: 1.5px solid #8b5cf6;
  box-shadow: 0 6px 18px rgba(0,0,0,.12); text-align: center; z-index: 2;
}
.g-icon {
  width: 34px; height: 34px; border-radius: 10px; margin: 0 auto 6px;
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 4px 10px rgba(0,0,0,.2);
}
.g-name { font-size: 13px; font-weight: 600; color: var(--text-primary); }
.g-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; font-family: monospace; }
.g-active { font-size: 11px; margin-top: 6px; display: flex; align-items: center; justify-content: center; gap: 5px; }
.g-dot { width: 8px; height: 8px; border-radius: 50%; }
.g-tools {
  position: absolute; display: flex; gap: 6px;
  flex-wrap: wrap; max-width: 180px; z-index: 2;
}
.g-node.clickable { cursor: pointer; }
.g-node.clickable:hover { filter: brightness(1.15); }
.pv-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000;
  display: flex; align-items: center; justify-content: center;
}
.pv-panel {
  width: 640px; max-width: 92vw; max-height: 88vh; overflow: hidden;
  background: var(--bg, #161b22); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 18px; display: flex; flex-direction: column;
}
.pv-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.pv-text {
  white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.6;
  overflow-y: auto; max-height: calc(88vh - 80px); margin: 0; padding: 12px;
  background: var(--bg-secondary, rgba(255,255,255,0.02)); border: 1px solid var(--border);
  border-radius: 8px;
}
</style>
