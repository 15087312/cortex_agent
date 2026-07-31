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
    <div class="page-header">      <h2>会话监控</h2></div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ sessions.length }}</div><div class="stat-label">活跃会话</div></div>
        <div class="stat-card" style="cursor:pointer" @click="$router.push('/chat')"><div class="stat-icon">💬</div><div class="stat-value">新对话</div><div class="stat-label">跳转聊天</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">会话列表 ({{ sessions.length }})</div>
          <div v-if="sessions.length === 0" class="empty-state" style="padding:32px"><span class="empty-icon">📭</span><p class="empty-text">暂无活跃会话</p></div>
          <div v-for="s in sessions" :key="s.session_id" class="session-monitor-item" :class="{ active: selected === s.session_id }" @click="handleSelect(s.session_id)">
            <div class="smi-header">
              <span class="smi-id">{{ (s.session_id||'').slice(0,12) }}...</span>
              <span class="badge" :class="(s.is_active||s.state==='active')?'badge-green':'badge-gray'">{{ (s.is_active||s.state==='active')?'活跃':'非活跃' }}</span>
            </div>
            <div class="smi-detail">对话框: {{ s.dialog_size||0 }}条</div>
          </div>
        </div>
        <div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">对话框 ({{ dialog.length }}条)</div>
          <div v-if="!selected" class="empty-state" style="padding:32px"><span class="empty-icon">💬</span><p class="empty-text">点击左侧查看</p></div>
          <div v-else-if="dialog.length === 0" class="empty-state" style="padding:32px"><span class="empty-icon">📭</span><p class="empty-text">暂无消息</p></div>
          <div v-else v-for="e in dialog" :key="e.id||e.timestamp" class="dialog-item">
            <div class="dialog-meta">
              <span class="dialog-role" :class="e.role">{{ e.role==='user'?'👤 用户':e.role==='assistant'||e.role==='large'?'🤖 AI':'● 系统' }}</span>
              <span class="dialog-time">{{ formatTime(e.timestamp||e.time) }}</span>
            </div>
            <div class="dialog-content">{{ (e.content||e.text||'').slice(0,500) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
