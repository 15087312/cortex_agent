<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'

const models = ref(null)
const tools = ref([])
const loading = ref(true)

const summary = computed(() => models.value?.summary || {})
const tierNodes = [
  { tier: 'large', role: 'orchestrator', label: '总指挥', icon: 'brain', color: '#8b5cf6', desc: '理解目标、规划任务' },
  { tier: 'supervisor', role: 'code_supervisor', label: '代码主管', icon: 'list', color: '#3b82f6', desc: '拆解任务、分派专家' },
  { tier: 'expert', role: 'code_writer', label: '实现专家', icon: 'wrench', color: '#f59e0b', desc: '执行具体实现' },
]
const totalTools = computed(() => tools.value.length || 0)
const commonTools = computed(() => tools.value.slice(0, 6))
const activeRunners = computed(() => models.value?.runners?.length || 0)

async function loadData() {
  try {
    const [m, t] = await Promise.all([
      endpoints.models().catch(() => null),
      endpoints.tools().catch(() => null),
    ])
    models.value = m?.data || null
    tools.value = t?.data?.tools || t?.data || []
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
        <div class="card-header">多 Agent 编排流（总指挥 → 主管 → 实现专家 → 工具）</div>
        <div class="graph-flow" v-if="!loading">
          <svg class="graph-svg" viewBox="0 0 920 260" xmlns="http://www.w3.org/2000/svg">
            <!-- 连线 -->
            <line x1="170" y1="130" x2="215" y2="130" class="graph-line" marker-end="url(#arrow)" />
            <line x1="365" y1="130" x2="410" y2="130" class="graph-line" marker-end="url(#arrow)" />
            <line x1="560" y1="130" x2="605" y2="130" class="graph-line" marker-end="url(#arrow)" />
            <line x1="755" y1="130" x2="800" y2="130" class="graph-line" marker-end="url(#arrow)" />
            <defs>
              <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
                <path d="M0,0 L6,3 L0,6 z" fill="#8b5cf6" />
              </marker>
            </defs>
          </svg>

          <!-- 输入节点 -->
          <div class="g-node g-input" style="left:20px;top:70px">
            <div class="g-icon" style="background:#22c55e"><Icon name="user" :size="18" /></div>
            <div class="g-name">用户输入</div>
            <div class="g-sub">语音 / 文本 / 感知</div>
          </div>
          <!-- 层级节点 -->
          <div v-for="(n, i) in tierNodes" :key="n.tier" class="g-node" :style="{ left: 215 + i * 195 + 'px', top: '70px', borderColor: n.color }">
            <div class="g-icon" :style="{ background: n.color }"><Icon :name="n.icon" :size="18" /></div>
            <div class="g-name">{{ n.label }}</div>
            <div class="g-sub">{{ n.role }}</div>
            <div class="g-active" :style="{ color: n.color }">
              <span class="g-dot" :style="{ background: (summary[n.tier]?.active || 0) > 0 ? '#22c55e' : '#94a3b8' }"></span>
              活跃 {{ summary[n.tier]?.active || 0 }} / {{ summary[n.tier]?.max || 1 }}
            </div>
          </div>
          <!-- 工具节点 -->
          <div class="g-node g-input" style="left:800px;top:70px;border-color:#06b6d4">
            <div class="g-icon" style="background:#06b6d4"><Icon name="wrench" :size="18" /></div>
            <div class="g-name">工具 ({{ totalTools }})</div>
            <div class="g-sub">{{ activeRunners ? '运行中' : '空闲' }}</div>
          </div>

          <!-- 工具徽标 -->
          <div class="g-tools">
            <span v-for="t in commonTools" :key="t.name" class="badge" style="font-family:monospace;background:rgba(6,182,212,.1);color:#06b6d4;font-size:10px">{{ t.name }}</span>
            <span v-if="totalTools > 6" class="badge badge-gray" style="font-size:10px">+{{ totalTools - 6 }}</span>
          </div>
        </div>
        <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>
      </div>

      <!-- 层级明细 -->
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px">
        <div v-for="n in tierNodes" :key="n.tier" class="card">
          <div class="card-header" style="color:var(--text-secondary)">{{ n.label }}（{{ n.tier }}）</div>
          <p style="font-size:13px;color:var(--text-muted);margin:0 0 10px">{{ n.desc }}</p>
          <div style="font-size:13px">
            活跃实例 <b :style="{ color: n.color }">{{ summary[n.tier]?.active || 0 }}</b> / {{ summary[n.tier]?.max || 1 }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.graph-flow { position: relative; height: 280px; overflow: hidden; }
.graph-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
.graph-line { stroke: #8b5cf6; stroke-width: 2; stroke-dasharray: 6 4; }
.g-node {
  position: absolute; width: 170px; padding: 12px; border-radius: 12px;
  background: var(--bg-secondary); border: 1.5px solid #8b5cf6;
  box-shadow: 0 6px 18px rgba(0,0,0,.12); text-align: center; z-index: 2;
}
.g-input { border-color: var(--border); }
.g-icon {
  width: 36px; height: 36px; border-radius: 10px; margin: 0 auto 8px;
  display: flex; align-items: center; justify-content: center; color: #fff;
  box-shadow: 0 4px 10px rgba(0,0,0,.2);
}
.g-name { font-size: 14px; font-weight: 600; color: var(--text-primary); }
.g-sub { font-size: 11px; color: var(--text-muted); margin-top: 2px; font-family: monospace; }
.g-active { font-size: 11px; margin-top: 8px; display: flex; align-items: center; justify-content: center; gap: 5px; }
.g-dot { width: 8px; height: 8px; border-radius: 50%; }
.g-tools {
  position: absolute; left: 790px; top: 175px; display: flex; gap: 6px;
  flex-wrap: wrap; max-width: 160px; z-index: 2;
}
</style>
