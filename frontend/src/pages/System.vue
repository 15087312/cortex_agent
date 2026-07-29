<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'

const info = ref({})
const think = ref({})
const db = ref({})
const health = ref({})
const att = ref({})

onMounted(async () => {
  try {
    const [ir, tr, dr, hr, ar] = await Promise.all([
      endpoints.systemInfo().catch(()=>null), endpoints.thinkingStatus().catch(()=>null),
      endpoints.database().catch(()=>null), endpoints.health().catch(()=>null),
      endpoints.get('/attention/status').catch(()=>null),
    ])
    info.value = ir?.data || {}; think.value = tr?.data || {}; db.value = dr?.data || {}; health.value = hr?.data || {}; att.value = ar?.data || {}
  } catch {}
})
function badgeClass(v) { return v ? 'badge-green' : 'badge-red' }
</script>

<template>
  <div>
    <div class="page-header"><h2>ℹ 系统信息</h2></div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-icon">🖥</div><div class="stat-value">{{ info.name||'Cortex Agent' }}</div><div class="stat-label">系统</div></div>
        <div class="stat-card"><div class="stat-icon">🏷</div><div class="stat-value">{{ info.version||'-' }}</div><div class="stat-label">版本</div></div>
        <div class="stat-card"><div class="stat-icon">{{ health.status==='healthy'?'🟢':'🟡' }}</div><div class="stat-value">{{ health.status==='healthy'?'健康':health.status||'-' }}</div><div class="stat-label">状态</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="card"><div class="card-header">🧠 思维模块</div><div>状态: <span class="badge" :class="think.status==='healthy'?'badge-green':'badge-yellow'">{{ think.status||'-' }}</span></div><div style="margin-top:8px">大模型: <span class="badge" :class="badgeClass(think.models?.big)">{{ think.models?.big?'可用':'不可用' }}</span></div><div>中模型: <span class="badge" :class="badgeClass(think.models?.medium)">{{ think.models?.medium?'可用':'不可用' }}</span></div><div>小模型: <span class="badge" :class="badgeClass(think.models?.small)">{{ think.models?.small?'可用':'不可用' }}</span></div></div>
        <div class="card"><div class="card-header">💾 数据库</div><div>类型: {{ db.type||'sqlite' }}</div><div>表: {{ (db.tables||[]).length }}</div><div>缓存命中: {{ db.cache?.hits||0 }}</div></div>
        <div class="card"><div class="card-header">🔍 健康检查</div><div v-if="!health.checks||Object.keys(health.checks).length===0">暂无</div><div v-else v-for="(s,n) in health.checks" :key="n">{{ n }}: <span class="badge" :class="s==='ok'?'badge-green':'badge-red'">{{ s }}</span></div></div>
        <div class="card"><div class="card-header">👁 注意力</div><div>状态: <span class="badge" :class="att.status==='healthy'?'badge-green':'badge-gray'">{{ att.status||'-' }}</span></div></div>
      </div>
    </div>
  </div>
</template>
