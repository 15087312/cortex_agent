<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { formatTime } from '@/utils/format.js'

const sessions = ref([])
const selected = ref(null)
const dialog = ref([])

onMounted(loadData)
async function loadData() { try { const r = await endpoints.managementSessions(); sessions.value = r.data?.sessions || [] } catch {} }
async function handleSelect(sid) { selected.value = sid; try { const r = await endpoints.sessionDialog(sid, 50); dialog.value = r.data?.dialog || [] } catch { dialog.value = [] } }
</script>

<template>
  <div>
    <div class="page-header"><h2>📋 会话监控</h2></div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ sessions.length }}</div><div class="stat-label">活跃会话</div></div>
        <div class="stat-card" style="cursor:pointer" @click="$router.push('/chat')"><div class="stat-icon">💬</div><div class="stat-value">新对话</div><div class="stat-label">跳转聊天</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">会话列表 ({{ sessions.length }})</div>
          <div v-if="sessions.length === 0" style="text-align:center;padding:40px;color:var(--text-muted)">暂无活跃会话</div>
          <div v-for="s in sessions" :key="s.session_id" style="padding:8px 10px;cursor:pointer;border-radius:4px;margin-bottom:4px" :style="{ background: selected===s.session_id?'var(--accent-bg)':'', borderLeft: selected===s.session_id?'3px solid var(--accent)':'3px solid transparent' }" @click="handleSelect(s.session_id)">
            <div style="display:flex;justify-content:space-between"><span style="font-size:13px;font-family:var(--font-mono)">{{ (s.session_id||'').slice(0,12) }}...</span><span class="badge" :class="(s.is_active||s.state==='active')?'badge-green':'badge-gray'">{{ (s.is_active||s.state==='active')?'活跃':'非活跃' }}</span></div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px">对话框: {{ s.dialog_size||0 }}条</div>
          </div>
        </div>
        <div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">对话框 ({{ dialog.length }}条)</div>
          <div v-if="!selected" style="text-align:center;padding:40px;color:var(--text-muted)">点击左侧查看</div>
          <div v-else-if="dialog.length === 0" style="text-align:center;padding:40px;color:var(--text-muted)">暂无消息</div>
          <div v-else v-for="e in dialog" :key="e.id||e.timestamp" style="padding:8px 10px;margin-bottom:6px;border-radius:6px;background:var(--bg-tertiary)">
            <div style="display:flex;justify-content:space-between;font-size:11px" :style="{color: e.role==='user'?'var(--accent)':e.role==='assistant'||e.role==='large'?'var(--success)':'var(--text-muted)'}"><span>{{ e.role==='user'?'👤 用户':e.role==='assistant'||e.role==='large'?'🤖 AI':'● 系统' }}</span><span style="color:var(--text-muted)">{{ formatTime(e.timestamp||e.time) }}</span></div>
            <div style="font-size:13px;line-height:1.5">{{ (e.content||e.text||'').slice(0,500) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
