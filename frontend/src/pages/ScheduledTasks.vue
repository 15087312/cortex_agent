<script setup>
import { ref, onMounted, computed } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useI18n } from 'vue-i18n'
import Icon from '@/components/Icon.vue'

const { t } = useI18n()
const toast = useToastStore()
const sessions = ref([])
const agents = ref([])
const selected = ref('')
const tasks = ref([])
const loading = ref(true)
const saving = ref(false)

const TASK_TYPES = [
  { value: 'daily', label: 'scheduledTasks.typeDaily' },
  { value: 'interval', label: 'scheduledTasks.typeInterval' },
  { value: 'once', label: 'scheduledTasks.typeOnce' },
  { value: 'cron', label: 'scheduledTasks.typeCron' },
]

function taskType(task) {
  const s = task.schedule
  if (typeof s === 'string' && s.includes(':')) return 'daily'
  if (s && typeof s === 'object') return s.kind === 'interval' ? 'interval' : s.kind === 'once' ? 'once' : s.kind === 'cron' ? 'cron' : 'daily'
  if (task.time) return 'daily'
  return 'daily'
}
function statusBadge(st) {
  if (!st) return null
  const map = { success: ['#3fb950', 'common.success'], error: ['#f85149', 'common.error'] }
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
    if (d.success) toast.show(t('scheduledTasks.saved'), 'success')
    else toast.show(t('common.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('common.saveFailed'), 'error') }
  finally { saving.value = false }
}

onMounted(loadSessions)
</script>

<template>
  <div>
    <div class="page-header">
      <h2>{{ $t('scheduledTasks.title') }}</h2>
      <button class="btn btn-sm" @click="loadSessions"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body">
      <div class="card" v-if="!loading">
        <div class="card-header">{{ $t('scheduledTasks.selectSession') }}</div>
        <select v-model="selected" class="input max-w-320" @change="loadTasks">
          <option v-for="s in sessions" :key="s.session_id" :value="s.session_id">{{ s.title || s.session_id.slice(0, 16) }}</option>
        </select>
      </div>

      <div class="card dash-mt" v-if="selected">
        <div class="card-header card-header-flex">
          <span>{{ $t('scheduledTasks.tasksHint') }}</span>
          <button class="btn btn-sm btn-primary" @click="addTask"><Icon name="plus" :size="13" /> {{ $t('scheduledTasks.addTask') }}</button>
        </div>

        <div v-if="tasks.length">
          <div v-for="(task, i) in tasks" :key="task.id" class="task-row">
            <div class="task-controls">
              <select v-model="task.type" class="input w-110" @change="onTypeChange(task)">
                <option v-for="t in TASK_TYPES" :key="t.value" :value="t.value">{{ $t(t.label) }}</option>
              </select>

              <template v-if="task.type === 'daily'">
                <input v-model="task.time" class="input w-90" placeholder="HH:MM" @change="task.schedule = task.time" />
              </template>
              <template v-else-if="task.type === 'interval'">
                <input v-model.number="task.every_minutes" type="number" min="1" class="input w-80" @change="task.schedule = { kind: 'interval', every_minutes: task.every_minutes }" />
                <span class="task-hint">{{ $t('scheduledTasks.minutes') }}</span>
              </template>
              <template v-else-if="task.type === 'once'">
                <input v-model="task.at" class="input w-90" placeholder="HH:MM" @change="task.schedule = { kind: 'once', at: task.at }" />
              </template>
              <template v-else>
                <input v-model="task.expr" class="input w-130" :placeholder="$t('scheduledTasks.cronPlaceholder')" @change="task.schedule = { kind: 'cron', expr: task.expr }" />
              </template>

              <label class="toggle-switch" :title="$t('common.enable')">
                <input type="checkbox" v-model="task.enabled" /><span class="toggle-slider"></span>
              </label>
              <span class="task-hint">{{ $t('common.enable') }}</span>
              <select v-model="task.action" class="input w-120" :title="$t('scheduledTasks.actionTitle')">
                <option value="chat">{{ $t('scheduledTasks.chatAction') }}</option>
              </select>
              <select v-model="task.agent_type" class="input w-140" :title="$t('scheduledTasks.agentTitle')">
                <option value="">{{ $t('scheduledTasks.directorDefault') }}</option>
                <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
              </select>
              <button class="btn btn-sm danger" @click="removeTask(i)"><Icon name="trash" :size="13" /></button>
              <span v-if="statusBadge(task.last_status)" :style="{ fontSize: '11px', padding: '2px 8px', borderRadius: '10px', background: statusBadge(task.last_status).color + '22', color: statusBadge(task.last_status).color }">
                {{ $t(statusBadge(task.last_status).label) }}{{ task.last_run ? ' · ' + task.last_run : '' }}
              </span>
            </div>
            <textarea v-model="task.prompt" rows="2" class="input task-textarea" :placeholder="$t('scheduledTasks.promptPlaceholder')"></textarea>
          </div>
        </div>
        <div v-else class="task-empty">{{ $t('scheduledTasks.emptyList') }}</div>

        <div class="task-save">
          <button class="btn btn-sm btn-primary" :disabled="saving" @click="saveTasks">{{ saving ? $t('common.saving') : $t('scheduledTasks.saveTask') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dash-mt { margin-top: 12px; }
.card-header-flex { display: flex; justify-content: space-between; align-items: center; }
.task-row { padding: 12px 0; border-bottom: 1px solid var(--border); }
.task-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.task-hint { font-size: 12px; color: var(--text-muted); }
.task-textarea { margin-top: 8px; font-size: 13px; }
.task-empty { text-align: center; padding: 24px; color: var(--text-muted); }
.task-save { text-align: right; padding-top: 12px; }
</style>
