<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'

const info = ref({})
const think = ref({})
const db = ref({})
const health = ref({})
const att = ref({})

async function loadData() {
  try {
    const [ir, tr, dr, hr, ar] = await Promise.all([
      endpoints.systemInfo().catch(()=>null), endpoints.thinkingStatus().catch(()=>null),
      endpoints.database().catch(()=>null), endpoints.health().catch(()=>null),
      endpoints.get('/attention/status').catch(()=>null),
    ])
    info.value = ir?.data || {}; think.value = tr?.data || {}; db.value = dr?.data || {}; health.value = hr?.data || {}; att.value = ar?.data || {}
  } catch {}
}
function badgeClass(v) { return v ? 'badge-green' : 'badge-red' }
function attBadge(s) { return s === 'healthy' ? 'badge-green' : s ? 'badge-red' : 'badge-gray' }

let timer = null
onMounted(() => { loadData(); timer = setInterval(loadData, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-header">
      <h2>系统信息</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat-card"><div class="stat-icon" style="background:rgba(88,166,255,.15);color:#58a6ff"><Icon name="monitor" :size="18" /></div><div class="stat-value" style="font-size:16px">{{ info.name || 'Cortex Agent' }}</div><div class="stat-label">系统</div></div>
        <div class="stat-card"><div class="stat-icon" style="background:rgba(210,153,34,.15);color:#d29922"><Icon name="tag" :size="18" /></div><div class="stat-value">{{ info.version || '-' }}</div><div class="stat-label">版本</div></div>
        <div class="stat-card"><div class="stat-icon" :style="health.status==='healthy' ? 'background:rgba(63,185,80,.15);color:#3fb950' : 'background:rgba(248,81,73,.15);color:#f85149'"><Icon :name="health.status==='healthy' ? 'check' : 'alert'" :size="18" /></div><div class="stat-value">{{ health.status==='healthy' ? '健康' : health.status || '-' }}</div><div class="stat-label">状态</div></div>
        <div class="stat-card"><div class="stat-icon" style="background:rgba(163,113,247,.15);color:#a371f7"><Icon name="brain" :size="18" /></div><div class="stat-value">{{ think.status || '-' }}</div><div class="stat-label">思维</div></div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">
        <div class="card"><div class="card-header"><Icon name="brain" :size="15" /> 思维模块</div>
          <div class="system-info">
            <div class="system-info-row"><span class="info-label">状态</span><span class="info-value"><span class="badge" :class="think.status==='healthy' ? 'badge-green' : 'badge-yellow'">{{ think.status || '-' }}</span></span></div>
            <div class="system-info-row"><span class="info-label">大模型</span><span class="info-value"><span class="badge" :class="badgeClass(think.models?.big)">{{ think.models?.big ? '可用' : '不可用' }}</span></span></div>
            <div class="system-info-row"><span class="info-label">中模型</span><span class="info-value"><span class="badge" :class="badgeClass(think.models?.medium)">{{ think.models?.medium ? '可用' : '不可用' }}</span></span></div>
            <div class="system-info-row"><span class="info-label">小模型</span><span class="info-value"><span class="badge" :class="badgeClass(think.models?.small)">{{ think.models?.small ? '可用' : '不可用' }}</span></span></div>
          </div>
        </div>
        <div class="card"><div class="card-header"><Icon name="database" :size="15" /> 数据库</div>
          <div class="system-info">
            <div class="system-info-row"><span class="info-label">类型</span><span class="info-value">{{ db.type || 'sqlite' }}</span></div>
            <div class="system-info-row"><span class="info-label">表</span><span class="info-value">{{ (db.tables || []).length }}</span></div>
            <div class="system-info-row"><span class="info-label">缓存命中</span><span class="info-value">{{ db.cache?.hits || 0 }}</span></div>
          </div>
        </div>
        <div class="card"><div class="card-header"><Icon name="search" :size="15" /> 健康检查</div>
          <div v-if="!health.checks || Object.keys(health.checks).length === 0" class="system-info"><div class="system-info-row"><span class="info-value" style="color:var(--text-muted)">暂无</span></div></div>
          <div v-else class="system-info"><div class="system-info-row" v-for="(s, n) in health.checks" :key="n"><span class="info-label">{{ n }}</span><span class="info-value"><span class="badge" :class="s === 'ok' ? 'badge-green' : 'badge-red'">{{ s }}</span></span></div></div>
        </div>
        <div class="card"><div class="card-header"><Icon name="eye" :size="15" /> 注意力</div>
          <div class="system-info"><div class="system-info-row"><span class="info-label">状态</span><span class="info-value"><span class="badge" :class="attBadge(att.status)">{{ att.status || '-' }}</span></span></div></div>
        </div>
      </div>
    </div>
  </div>
</template>
