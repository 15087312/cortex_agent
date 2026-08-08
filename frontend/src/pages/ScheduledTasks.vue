<script setup>
import { ref, onMounted, computed } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const sessions = ref([])
const agents = ref([])
const selected = ref('')
const tasks = ref([])
const loading = ref(true)
const saving = ref(false)

const TASK_TYPES = [
  { value: 'daily', label: '每天定点' },
  { value: 'interval', label: '每 N 分钟' },
  { value: 'once', label: '单次触发' },
  { value: 'cron', label: 'Cron 表达式' },
]

function taskType(task) {
  const s = task.schedule
  if (typeof s === 'string' && s.includes(':')) return 'daily'
  if (s && typeof s === 'object') return s.kind === 'interval' ? 'interval' : s.kind === 'once' ? 'once' : s.kind === 'cron' ? 'cron' : 'daily'
  if (task.time) return 'daily'
  return 'daily'
}
function scheduleLabel(task) {
  const s = task.schedule
  const t = taskType(task)
  if (t === 'daily') return typeof s === 'string' ? s : (task.time || 'HH:MM')
  if (t === 'interval') return '每 ' + (s?.every_minutes ?? task.every_minutes ?? 30) + ' 分钟'
  if (t === 'once') return s?.at || task.at || '未设'
  if (t === 'cron') return s?.expr || task.expr || '未设'
  return ''
}
function statusBadge(st) {
  if (!st) return null
  const map = { success: ['#3fb950', '成功'], error: ['#f85149', '错误'] }
  const [color, label] = map[st] || ['#8b949e', st]
  return { color, label }
}
function scheduleOf(task) {
  const t = taskType(task)
  if (t === 'daily') return task.time || '09:00'
  if (t === 'interval') return { kind: 'interval', every_minutes: Number(task.every_minutes || 30) }
  if (t === 'once') return { kind: 'once', at: task.at || '09:00' }
  if (t === 'cron') return { kind: 'cron', expr: task.expr || '* * * * *' }
  return task.time || '09:00'
}

async function loadSessions() {
  try {
    const r = await fetch('/api/management/orchestration', { headers: { Accept: 'application/json' } }).then(x => x.json())
    agents.value = r?.data?.agents || []
  } catch {}
  try {
    const r = await endpoints.sessions()
    sessions.value = (r.data || []).sort((a, b) => (b.last_active || '').localeCompare(a.last_active || ''))
    if (!selected.value && sessions.value.length) selected.value = sessions.value[0].session_id
    if (selected.value) await loadTasks()
  } catch {} finally { loading.value = false }
}

async function loadTasks() {
  if (!selected.value) return
  try {
    const r = await fetch('/api/stream/session/' + encodeURIComponent(selected.value) + '/tasks', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    tasks.value = (d?.data?.tasks?.tasks) || []
    tasks.value.forEach((t) => { t.type = taskType(t) })
  } catch { tasks.value = [] }
}

function addTask() {
  tasks.value.push({ id: 't' + Date.now(), time: '09:00', schedule: '09:00', every_minutes: 30, at: '09:00', expr: '* * * * *', enabled: true, action: 'chat', prompt: '', type: 'daily' })
}

function removeTask(i) { tasks.value.splice(i, 1) }

function onTypeChange(task) {
  if (task.type === 'daily') task.schedule = task.time || '09:00'
  else if (task.type === 'interval') task.schedule = { kind: 'interval', every_minutes: Number(task.every_minutes || 30) }
  else if (task.type === 'once') task.schedule = { kind: 'once', at: task.at || '09:00' }
  else task.schedule = { kind: 'cron', expr: task.expr || '* * * * *' }
}

async function saveTasks() {
  saving.value = true
  try {
    const normalized = tasks.value.map(t => {
      const out = { id: t.id, enabled: !!t.enabled, action: t.action || 'chat', prompt: t.prompt || '', schedule: scheduleOf(t) }
      if (t.agent_type) out.agent_type = t.agent_type
      return out
    })
    const r = await fetch('/api/stream/session/' + encodeURIComponent(selected.value) + '/tasks', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tasks: { tasks: normalized } }),
    })
    const d = await r.json()
    if (d.success) toast.show('定时任务已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = false }
}

onMounted(loadSessions)
</script>

<template>
  <div>
    <div class="page-header">
      <h2>会话定时任务</h2>
      <button class="btn btn-sm" @click="loadSessions"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="card" v-if="!loading">
        <div class="card-header">选择会话（每会话定时任务独立）</div>
        <select v-model="selected" class="input" style="max-width:320px" @change="loadTasks">
          <option v-for="s in sessions" :key="s.session_id" :value="s.session_id">{{ s.title || s.session_id.slice(0, 16) }}</option>
        </select>
      </div>

      <div class="card" style="margin-top:12px" v-if="selected">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
          <span>定时任务（到点调用与主动搭话相同的大模型逻辑 → 消息推送）</span>
          <button class="btn btn-sm btn-primary" @click="addTask"><Icon name="plus" :size="13" /> 添加任务</button>
        </div>

        <div v-if="tasks.length">
          <div v-for="(task, i) in tasks" :key="task.id" style="padding:12px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <select v-model="task.type" class="input" style="width:110px" @change="onTypeChange(task)">
                <option v-for="t in TASK_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
              </select>

              <template v-if="task.type === 'daily'">
                <input v-model="task.time" class="input" style="width:90px" placeholder="HH:MM" @change="task.schedule = task.time" />
              </template>
              <template v-else-if="task.type === 'interval'">
                <input v-model.number="task.every_minutes" type="number" min="1" class="input" style="width:80px" @change="task.schedule = { kind: 'interval', every_minutes: task.every_minutes }" />
                <span style="font-size:12px;color:var(--text-muted)">分钟</span>
              </template>
              <template v-else-if="task.type === 'once'">
                <input v-model="task.at" class="input" style="width:90px" placeholder="HH:MM" @change="task.schedule = { kind: 'once', at: task.at }" />
              </template>
              <template v-else>
                <input v-model="task.expr" class="input" style="width:130px" placeholder="分 时 日 月 周" @change="task.schedule = { kind: 'cron', expr: task.expr }" />
              </template>

              <label class="toggle-switch" title="启用">
                <input type="checkbox" v-model="task.enabled" /><span class="toggle-slider"></span>
              </label>
              <span style="font-size:12px;color:var(--text-muted)">启用</span>
              <select v-model="task.action" class="input" style="width:120px" title="触发的逻辑">
                <option value="chat">chat（大模型）</option>
              </select>
              <select v-model="task.agent_type" class="input" style="width:140px" title="使用的角色人格">
                <option value="">总指挥（默认）</option>
                <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
              </select>
              <button class="btn btn-sm danger" @click="removeTask(i)"><Icon name="trash" :size="13" /></button>
              <span v-if="statusBadge(task.last_status)" :style="{ fontSize: '11px', padding: '2px 8px', borderRadius: '10px', background: statusBadge(task.last_status).color + '22', color: statusBadge(task.last_status).color }">
                {{ statusBadge(task.last_status).label }}{{ task.last_run ? ' · ' + task.last_run : '' }}
              </span>
            </div>
            <textarea v-model="task.prompt" rows="2" class="input" style="margin-top:8px;font-size:13px" placeholder="可选提示词（留空用默认提醒语）"></textarea>
          </div>
        </div>
        <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无定时任务，点击"添加任务"创建</div>

        <div style="text-align:right;padding-top:12px">
          <button class="btn btn-sm btn-primary" :disabled="saving" @click="saveTasks">{{ saving ? '保存中...' : '保存任务' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
