<script setup>
import { ref, onMounted } from 'vue'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  title: { type: String, default: '' },
})
const emit = defineEmits(['close'])

const toast = useToastStore()
const tab = ref('outreach')
const loading = ref(true)
const saving = ref(false)

// ── 主动搭话配置 ──
const oc = ref({
  enabled: false,
  cooldownMin: 30,
  scheduleOn: false, scheduleTime: '', scheduleJitter: 10,
  screenOn: false, screenRatio: 0.5, screenProb: 0.5, screenInterval: 30, screenCooldown: 30,
  idleOn: false, idleMinutes: 30, idleProb: 0.5, idleInterval: 60,
  windowsOn: false, timeWindowsText: '',
})

// ── 定时任务配置 ──
const tasks = ref([])
const TASK_TYPES = [
  { value: 'daily', label: '每天定点' },
  { value: 'interval', label: '每 N 分钟' },
  { value: 'once', label: '单次触发' },
  { value: 'cron', label: 'Cron 表达式' },
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
  const map = { success: ['#3fb950', '成功'], error: ['#f85149', '错误'] }
  const [color, label] = map[st] || ['#8b949e', st]
  return { color, label }
}

async function loadAll() {
  loading.value = true
  try {
    const [o, t, a] = await Promise.all([
      fetch('/api/stream/session/' + encodeURIComponent(props.sessionId) + '/outreach-config', { headers: { Accept: 'application/json' } }).then(r => r.json()).catch(() => null),
      fetch('/api/stream/session/' + encodeURIComponent(props.sessionId) + '/tasks', { headers: { Accept: 'application/json' } }).then(r => r.json()).catch(() => null),
      fetch('/api/management/orchestration', { headers: { Accept: 'application/json' } }).then(r => r.json()).catch(() => null),
    ])
    const cfg = o?.data?.outreach || {}
    const scr = cfg.screen || {}
    const idle = cfg.idle || {}
    const sched = cfg.schedule || {}
    oc.value = {
      enabled: !!cfg.enabled,
      cooldownMin: cfg.cooldown_minutes ?? 30,
      scheduleOn: !!sched.enabled,
      scheduleTime: sched.time || '',
      scheduleJitter: sched.jitter_minutes ?? 10,
      screenOn: !!scr.enabled,
      screenRatio: scr.change_ratio ?? 0.5,
      screenProb: scr.probability ?? 0.5,
      screenInterval: scr.check_interval_seconds ?? 30,
      screenCooldown: scr.cooldown_minutes ?? 30,
      idleOn: !!idle.enabled,
      idleMinutes: idle.idle_minutes ?? 30,
      idleProb: idle.probability ?? 0.5,
      idleInterval: idle.check_interval_seconds ?? 60,
      windowsOn: !!cfg.time_windows_enabled,
      timeWindowsText: (cfg.time_windows || []).map((w) => `${w.start}-${w.end}` + (w.probability != null ? `@${w.probability}` : '')).join(','),
    }
    tasks.value = (t?.data?.tasks?.tasks) || []
    tasks.value.forEach((x) => { x.type = taskType(x) })
    agents.value = a?.data?.agents || []
  } catch {} finally { loading.value = false }
}

async function saveOutreach() {
  saving.value = true
  const timeWindows = oc.value.timeWindowsText.split(',').map((x) => x.trim()).filter(Boolean)
    .map((x) => {
      let prob
      const m = x.split('@')
      const [start, end] = m[0].split('-')
      if (m[1] != null) prob = parseFloat(m[1])
      const w = { start: (start || '').trim(), end: (end || '').trim() }
      if (prob != null) w.probability = prob
      return w
    }).filter((w) => w.start && w.end)
  const cfg = {
    enabled: !!oc.value.enabled,
    cooldown_minutes: Math.max(0, oc.value.cooldownMin || 30),
    schedule: oc.value.scheduleOn ? { enabled: true, time: oc.value.scheduleTime, jitter_minutes: Math.max(0, oc.value.scheduleJitter || 0) } : {},
    screen: {
      enabled: !!oc.value.screenOn,
      change_ratio: Math.max(0, Math.min(1, oc.value.screenRatio ?? 0.5)),
      probability: Math.max(0, Math.min(1, oc.value.screenProb ?? 0.5)),
      check_interval_seconds: Math.max(1, oc.value.screenInterval || 30),
      cooldown_minutes: Math.max(0, oc.value.screenCooldown || 30),
    },
    idle: {
      enabled: !!oc.value.idleOn,
      idle_minutes: Math.max(0, oc.value.idleMinutes || 30),
      probability: Math.max(0, Math.min(1, oc.value.idleProb ?? 0.5)),
      check_interval_seconds: Math.max(1, oc.value.idleInterval || 60),
    },
    time_windows_enabled: !!oc.value.windowsOn,
    time_windows: timeWindows,
  }
  try {
    const r = await fetch('/api/stream/session/' + encodeURIComponent(props.sessionId) + '/outreach-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outreach: cfg }),
    })
    const d = await r.json()
    if (d.success) toast.show('主动搭话设置已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = false }
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
    if (d.success) toast.show('定时任务已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = false }
}

onMounted(loadAll)
</script>

<template>
  <div class="ss-overlay" @click.self="emit('close')">
    <div class="ss-panel">
      <div class="ss-head">
        <div style="display:flex;align-items:center;gap:8px;min-width:0">
          <Icon name="settings" :size="16" />
          <span style="font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">会话设置{{ title ? '：' + title : '' }}</span>
        </div>
        <button class="btn btn-sm" @click="emit('close')"><Icon name="x" :size="14" /> 关闭</button>
      </div>

      <div class="seg" style="margin:10px 0">
        <button :class="{ on: tab === 'outreach' }" @click="tab = 'outreach'">主动搭话</button>
        <button :class="{ on: tab === 'tasks' }" @click="tab = 'tasks'">定时任务 ({{ tasks.length }})</button>
      </div>

      <div v-if="loading" style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>

      <!-- 主动搭话 -->
      <div v-else-if="tab === 'outreach'" style="max-height:calc(100vh - 220px);overflow-y:auto">
        <div class="ss-row" style="justify-content:space-between">
          <span class="ss-lbl">开启主动搭话</span>
          <label class="toggle-switch"><input type="checkbox" v-model="oc.enabled" /><span class="toggle-slider"></span></label>
        </div>
        <div class="ss-row">
          <span class="ss-lbl">综合冷却</span>
          <input class="input" type="number" v-model.number="oc.cooldownMin" style="width:64px;text-align:right" /> <span class="ss-unit">min</span>
        </div>
        <div class="ss-row">
          <span class="ss-lbl">定点发送</span>
          <label class="toggle-switch"><input type="checkbox" v-model="oc.scheduleOn" /><span class="toggle-slider"></span></label>
          <input class="input" v-model="oc.scheduleTime" style="width:70px" placeholder="14:00" :disabled="!oc.scheduleOn" />
          <span class="ss-unit" :class="{ off: !oc.scheduleOn }">±</span>
          <input class="input" type="number" v-model.number="oc.scheduleJitter" style="width:52px;text-align:right" :disabled="!oc.scheduleOn" /> <span class="ss-unit" :class="{ off: !oc.scheduleOn }">min</span>
        </div>
        <div class="ss-row">
          <span class="ss-lbl">屏幕触发</span>
          <label class="toggle-switch"><input type="checkbox" v-model="oc.screenOn" /><span class="toggle-slider"></span></label>
          <input class="input" type="number" v-model.number="oc.screenRatio" style="width:44px;text-align:right" title="变化幅度" :disabled="!oc.screenOn" />
          <input class="input" type="number" v-model.number="oc.screenProb" style="width:44px;text-align:right" title="概率" :disabled="!oc.screenOn" />
          <input class="input" type="number" v-model.number="oc.screenInterval" style="width:44px;text-align:right" title="判定间隔(s)" :disabled="!oc.screenOn" />
          <input class="input" type="number" v-model.number="oc.screenCooldown" style="width:44px;text-align:right" title="冷却(min)" :disabled="!oc.screenOn" />
        </div>
        <div class="ss-row">
          <span class="ss-lbl">空闲触发</span>
          <label class="toggle-switch"><input type="checkbox" v-model="oc.idleOn" /><span class="toggle-slider"></span></label>
          <input class="input" type="number" v-model.number="oc.idleMinutes" style="width:44px;text-align:right" title="空闲(min)" :disabled="!oc.idleOn" />
          <input class="input" type="number" v-model.number="oc.idleProb" style="width:44px;text-align:right" title="概率" :disabled="!oc.idleOn" />
          <input class="input" type="number" v-model.number="oc.idleInterval" style="width:44px;text-align:right" title="判定间隔(s)" :disabled="!oc.idleOn" />
        </div>
        <div class="ss-row">
          <span class="ss-lbl">时段触发</span>
          <label class="toggle-switch"><input type="checkbox" v-model="oc.windowsOn" /><span class="toggle-slider"></span></label>
          <input class="input" v-model="oc.timeWindowsText" style="flex:1;min-width:160px" placeholder="09:00-12:00@0.5,14:00-18:00@0.8" :disabled="!oc.windowsOn" />
        </div>
        <div style="text-align:right;margin-top:12px">
          <button class="btn btn-sm btn-primary" :disabled="saving" @click="saveOutreach">{{ saving ? '保存中...' : '保存主动搭话' }}</button>
        </div>
      </div>

      <!-- 定时任务 -->
      <div v-else style="max-height:calc(100vh - 220px);overflow-y:auto">
        <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
          <button class="btn btn-sm btn-primary" @click="addTask"><Icon name="plus" :size="13" /> 添加任务</button>
        </div>
        <div v-if="tasks.length">
          <div v-for="(task, i) in tasks" :key="task.id" style="padding:10px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
              <select v-model="task.type" class="input" style="width:96px" @change="onTypeChange(task)">
                <option v-for="t in TASK_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
              </select>
              <template v-if="task.type === 'daily'">
                <input v-model="task.time" class="input" style="width:72px" placeholder="HH:MM" @change="task.schedule = task.time" />
              </template>
              <template v-else-if="task.type === 'interval'">
                <input v-model.number="task.every_minutes" type="number" min="1" class="input" style="width:60px" @change="task.schedule = { kind: 'interval', every_minutes: task.every_minutes }" />
                <span class="ss-unit">分钟</span>
              </template>
              <template v-else-if="task.type === 'once'">
                <input v-model="task.at" class="input" style="width:72px" placeholder="HH:MM" @change="task.schedule = { kind: 'once', at: task.at }" />
              </template>
              <template v-else>
                <input v-model="task.expr" class="input" style="width:110px" placeholder="分 时 日 月 周" @change="task.schedule = { kind: 'cron', expr: task.expr }" />
              </template>
              <label class="toggle-switch" title="启用"><input type="checkbox" v-model="task.enabled" /><span class="toggle-slider"></span></label>
              <select v-model="task.agent_type" class="input" style="width:130px" title="角色人格">
                <option value="">总指挥（默认）</option>
                <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
              </select>
              <button class="btn btn-sm danger" @click="removeTask(i)"><Icon name="trash" :size="12" /></button>
              <span v-if="statusBadge(task.last_status)" :style="{ fontSize: '11px', padding: '2px 8px', borderRadius: '10px', background: statusBadge(task.last_status).color + '22', color: statusBadge(task.last_status).color }">
                {{ statusBadge(task.last_status).label }}{{ task.last_run ? ' · ' + task.last_run : '' }}
              </span>
            </div>
            <textarea v-model="task.prompt" rows="2" class="input" style="width:100%;margin-top:6px;font-size:12px" placeholder="可选提示词（留空用默认提醒语）"></textarea>
          </div>
        </div>
        <div v-else style="text-align:center;padding:20px;color:var(--text-muted)">暂无定时任务，点击「添加任务」创建</div>
        <div style="text-align:right;margin-top:12px">
          <button class="btn btn-sm btn-primary" :disabled="saving" @click="saveTasks">{{ saving ? '保存中...' : '保存定时任务' }}</button>
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
  background: var(--bg, #161b22); border: 1px solid var(--border); border-radius: 12px;
  padding: 14px 18px; display: flex; flex-direction: column;
}
.ss-head { display: flex; justify-content: space-between; align-items: center; }
.ss-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.ss-lbl { width: 72px; font-size: 13px; flex-shrink: 0; }
.ss-unit { font-size: 12px; color: var(--text-muted); }
.ss-unit.off { opacity: 0.4; }
</style>
