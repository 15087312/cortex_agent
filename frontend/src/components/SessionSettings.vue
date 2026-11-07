<script setup>
import { ref, onMounted } from 'vue'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  sessionId: { type: String, required: true },
  title: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const { t } = useI18n()
const toast = useToastStore()
const tab = ref('tasks')
const loading = ref(true)
const saving = ref(false)

// ── 定时任务配置 ──
const tasks = ref([])
const TASK_TYPES = [
  { value: 'daily', labelKey: 'sessionSettings.taskTypeDaily' },
  { value: 'interval', labelKey: 'sessionSettings.taskTypeInterval' },
  { value: 'once', labelKey: 'sessionSettings.taskTypeOnce' },
  { value: 'cron', labelKey: 'sessionSettings.taskTypeCron' },
]
const agents = ref([])

function taskType(task) {
  const s = task.schedule
  if (typeof s === 'string' && s.includes(':')) return 'daily'
  if (s && typeof s === 'object') return s.kind === 'interval' ? 'interval' : s.kind === 'once' ? 'once' : s.kind === 'cron' ? 'cron' : 'daily'
  if (task.time) return 'daily'
  return 'daily'
}
function scheduleOf(task) {
  const t = taskType(task)
  if (t === 'daily') return task.time || '09:00'
  if (t === 'interval') return { kind: 'interval', every_minutes: Number(task.every_minutes || 30) }
  if (t === 'once') return { kind: 'once', at: task.at || '09:00' }
  if (t === 'cron') return { kind: 'cron', expr: task.expr || '* * * * *' }
  return task.time || '09:00'
}
function addTask() {
  tasks.value.push({ id: 't' + Date.now(), type: 'daily', time: '09:00', schedule: '09:00', every_minutes: 30, at: '09:00', expr: '* * * * *', enabled: true, action: 'chat', agent_type: '', prompt: '' })
}
function removeTask(i) { tasks.value.splice(i, 1) }
function onTypeChange(task) {
  if (task.type === 'daily') task.schedule = task.time || '09:00'
  else if (task.type === 'interval') task.schedule = { kind: 'interval', every_minutes: Number(task.every_minutes || 30) }
  else if (task.type === 'once') task.schedule = { kind: 'once', at: task.at || '09:00' }
  else task.schedule = { kind: 'cron', expr: task.expr || '* * * * *' }
}
function statusBadge(st) {
  if (!st) return null
  const map = { success: ['#3fb950', t('sessionSettings.statusSuccess')], error: ['#f85149', t('sessionSettings.statusError')] }
  const [color, label] = map[st] || ['#8b949e', st]
  return { color, label }
}

async function saveTasks() {
  saving.value = true
  const normalized = tasks.value.map((t) => {
    const out = { id: t.id, enabled: !!t.enabled, action: t.action || 'chat', prompt: t.prompt || '', schedule: scheduleOf(t) }
    if (t.agent_type) out.agent_type = t.agent_type
    return out
  })
  try {
    const r = await fetch('/api/stream/session/' + encodeURIComponent(props.sessionId) + '/tasks', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tasks: { tasks: normalized } }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('sessionSettings.tasksSaved'), 'success')
    else toast.show(t('sessionSettings.saveFailedWith', { err: (d.error?.message || '') }), 'error')
  } catch (e) { toast.show(t('sessionSettings.saveFailed'), 'error') }
  finally { saving.value = false }
}

async function loadAll() {
  loading.value = true
  try {
    const [t, a] = await Promise.all([
      fetch('/api/stream/session/' + encodeURIComponent(props.sessionId) + '/tasks', { headers: { Accept: 'application/json' } }).then(r => r.json()).catch(() => null),
      fetch('/api/management/orchestration', { headers: { Accept: 'application/json' } }).then(r => r.json()).catch(() => null),
    ])
    tasks.value = (t?.data?.tasks?.tasks) || []
    tasks.value.forEach((x) => { x.type = taskType(x) })
    agents.value = a?.data?.agents || []
  } catch {} finally { loading.value = false }
}

onMounted(loadAll)
</script>

<template>
  <div class="ss-overlay" @click.self="emit('close')">
    <div class="ss-panel">
      <div class="ss-head">
        <div style="display:flex;align-items:center;gap:8px;min-width:0">
          <Icon name="settings" :size="16" />
          <span style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ $t('sessionSettings.title') }}{{ title ? '：' + title : '' }}</span>
        </div>
        <button class="btn btn-sm" @click="emit('close')"><Icon name="x" :size="14" /> {{ $t('common.close') }}</button>
      </div>

      <div class="seg" style="margin:10px 0">
        <button :class="{ on: tab === 'tasks' }" @click="tab = 'tasks'">{{ $t('sessionSettings.tasksTab') }} ({{ tasks.length }})</button>
      </div>

      <div v-if="loading" style="text-align:center;padding:40px;color:var(--text-muted)">{{ $t('common.loading') }}</div>


      <!-- 定时任务 -->
      <div v-else style="max-height:calc(100vh - 220px);overflow-y:auto">
        <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
          <button class="btn btn-sm btn-primary" @click="addTask"><Icon name="plus" :size="13" /> {{ $t('sessionSettings.addTask') }}</button>
        </div>
        <div v-if="tasks.length">
          <div v-for="(task, i) in tasks" :key="task.id" style="padding:10px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
              <select v-model="task.type" class="input" style="width:96px" @change="onTypeChange(task)">
                <option v-for="t in TASK_TYPES" :key="t.value" :value="t.value">{{ $t(t.labelKey) }}</option>
              </select>
              <template v-if="task.type === 'daily'">
                <input v-model="task.time" class="input" style="width:72px" placeholder="HH:MM" @change="task.schedule = task.time" />
              </template>
              <template v-else-if="task.type === 'interval'">
                <input v-model.number="task.every_minutes" type="number" min="1" class="input" style="width:60px" @change="task.schedule = { kind: 'interval', every_minutes: task.every_minutes }" />
                <span class="ss-unit">{{ $t('sessionSettings.minutes') }}</span>
              </template>
              <template v-else-if="task.type === 'once'">
                <input v-model="task.at" class="input" style="width:72px" placeholder="HH:MM" @change="task.schedule = { kind: 'once', at: task.at }" />
              </template>
              <template v-else>
                <input v-model="task.expr" class="input" style="width:110px" :placeholder="$t('sessionSettings.cronPlaceholder')" @change="task.schedule = { kind: 'cron', expr: task.expr }" />
              </template>
              <label class="toggle-switch" :title="$t('sessionSettings.enabled')"><input type="checkbox" v-model="task.enabled" /><span class="toggle-slider"></span></label>
              <select v-model="task.agent_type" class="input" style="width:130px" :title="$t('sessionSettings.agentPersona')">
                <option value="">{{ $t('sessionSettings.commanderDefault') }}</option>
                <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
              </select>
              <button class="btn btn-sm danger" @click="removeTask(i)"><Icon name="trash" :size="12" /></button>
              <span v-if="statusBadge(task.last_status)" :style="{ fontSize: '11px', padding: '2px 8px', borderRadius: '10px', background: statusBadge(task.last_status).color + '22', color: statusBadge(task.last_status).color }">
                {{ statusBadge(task.last_status).label }}{{ task.last_run ? ' · ' + task.last_run : '' }}
              </span>
            </div>
            <textarea v-model="task.prompt" rows="2" class="input" style="width:100%;margin-top:6px;font-size:12px" :placeholder="$t('sessionSettings.promptPlaceholder')"></textarea>
          </div>
        </div>
        <div v-else style="text-align:center;padding:20px;color:var(--text-muted)">{{ $t('sessionSettings.noTasks') }}</div>
        <div style="text-align:right;margin-top:12px">
          <button class="btn btn-sm btn-primary" :disabled="saving" @click="saveTasks">{{ saving ? $t('sessionSettings.saving') : $t('sessionSettings.saveTasks') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ss-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000;
  display: flex; align-items: center; justify-content: center;
}
.ss-panel {
  width: 560px; max-width: 92vw; max-height: 90vh; overflow: hidden;
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 18px; display: flex; flex-direction: column;
}
.ss-head { display: flex; justify-content: space-between; align-items: center; min-width: 0; gap: 8px; }
.ss-head > div { flex: 1; min-width: 0; }
.ss-head > div > span { min-width: 0; }
.ss-head > .btn { flex-shrink: 0; }
.ss-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.ss-lbl { width: 72px; font-size: 13px; flex-shrink: 0; }
.ss-unit { font-size: 12px; color: var(--text-muted); }
.ss-unit.off { opacity: 0.4; }
</style>
