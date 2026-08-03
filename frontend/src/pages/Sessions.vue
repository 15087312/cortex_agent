<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const sessions = ref([])
const selected = ref(null)
const dialog = ref([])
const dialogLoading = ref(false)

onMounted(loadData)
async function loadData() { try { const r = await endpoints.managementSessions(); sessions.value = r.data?.sessions || [] } catch {} }

async function handleSelect(sid) {
  selected.value = sid
  dialog.value = []
  dialogLoading.value = true
  // 双源 fallback：优先 /stream 消息，空则回退 /management dialog
  let msgs = []
  try {
    const r = await endpoints.sessionMessages(sid, 50)
    msgs = (r.data && Array.isArray(r.data)) ? r.data : (r.data?.messages || [])
  } catch {}
  if (msgs.length === 0) {
    try {
      const r2 = await endpoints.sessionDialog(sid, 50)
      msgs = r2.data?.dialog || []
    } catch {}
  }
  dialog.value = msgs
  dialogLoading.value = false
}

function roleInfo(e) {
  const et = e.type || ''
  const tier = e.tier || ''
  const role = e.role || ''
  if (role === 'user' || et === 'user_input') return { label: '用户', cls: 'user' }
  if (role === 'assistant' || et === 'response' || et === 'thought') return { label: 'AI', cls: 'ai' }
  if (tier === 'supervisor') return { label: '主管', cls: 'supervisor' }
  if (tier === 'expert') return { label: '专家', cls: 'expert' }
  return { label: '系统', cls: 'system' }
}
function tsOf(e) { return e.timestamp || e.time || e.created_at }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>会话监控</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ sessions.length }}</div><div class="stat-label">活跃会话</div></div>
        <div class="stat-card" style="cursor:pointer" @click="$router.push('/chat')"><div class="stat-icon"><Icon name="message" :size="20" /></div><div class="stat-value">新对话</div><div class="stat-label">跳转聊天</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">会话列表 ({{ sessions.length }})</div>
          <div v-if="sessions.length === 0" class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="inbox" :size="20" /></span><p class="empty-text">暂无活跃会话</p></div>
          <div v-for="s in sessions" :key="s.session_id" class="session-monitor-item" :class="{ active: selected === s.session_id }" @click="handleSelect(s.session_id)">
            <div class="smi-header">
              <span class="smi-id">{{ (s.session_id||'').slice(0,12) }}...</span>
              <span class="badge" :class="(s.is_active||s.state==='active')?'badge-green':'badge-gray'">{{ (s.is_active||s.state==='active')?'活跃':'非活跃' }}</span>
            </div>
            <div class="smi-detail">对话框: {{ s.dialog_size||0 }}条</div>
          </div>
        </div>
        <div class="card" style="max-height:500px;overflow-y:auto"><div class="card-header">对话框 ({{ dialog.length }}条)</div>
          <div v-if="!selected" class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="message" :size="20" /></span><p class="empty-text">点击左侧查看</p></div>
          <div v-else-if="dialogLoading" class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="loader" :size="20" /></span><p class="empty-text">加载中...</p></div>
          <div v-else-if="dialog.length === 0" class="empty-state" style="padding:32px"><span class="empty-icon"><Icon name="inbox" :size="20" /></span><p class="empty-text">暂无消息</p></div>
          <div v-else v-for="e in dialog" :key="e.id||e.timestamp" class="dialog-item">
            <div class="dialog-meta">
              <span class="dialog-role" :class="roleInfo(e).cls">{{ roleInfo(e).label }}</span>
              <span class="dialog-time">{{ formatTime(tsOf(e)) }}</span>
            </div>
            <div class="dialog-content">{{ (e.content||e.text||'').slice(0,500) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
