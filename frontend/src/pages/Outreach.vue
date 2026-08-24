<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import { useI18n } from 'vue-i18n'
import Icon from '@/components/Icon.vue'

const { t } = useI18n()
const toast = useToastStore()
const props = defineProps({ compact: { type: Boolean, default: false } })
const sessions = ref([])
const logs = ref([])
const totalLogs = ref(0)
const loading = ref(true)

const enabledCount = computed(() => sessions.value.filter((s) => s.enabled).length)
const reasonLabels = { schedule: 'outreach.reasonSchedule', screen: 'outreach.reasonScreen', idle: 'outreach.reasonIdle', time_window: 'outreach.reasonTimeWindow' }

async function loadAll() {
  loading.value = true
  try {
    const [sr, lr] = await Promise.all([
      endpoints.sessions().catch(() => null),
      endpoints.proactiveLogs(50).catch(() => null),
    ])
    sessions.value = (sr?.data || []).map((s) => {
      const prev = sessions.value.find((x) => x.session_id === s.session_id)
      const oc = (s.metadata && s.metadata.outreach) || {}
      const scr = oc.screen || {}
      const idle = oc.idle || {}
      const sched = oc.schedule || {}
      return {
        session_id: s.session_id,
        title: s.title || s.session_id.slice(0, 12),
        enabled: !!oc.enabled,
        cooldownMin: oc.cooldown_minutes ?? 30,
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
        windowsOn: !!oc.time_windows_enabled,
        timeWindowsText: (oc.time_windows || []).map((w) =>
          `${w.start}-${w.end}` + (w.probability != null ? `@${w.probability}` : '')).join(','),
        _open: prev ? prev._open : false,
      }
    })
    logs.value = lr?.data?.logs || []
    totalLogs.value = lr?.data?.total || 0
  } catch {} finally { loading.value = false }
}

async function saveConfig(s) {
  const timeWindows = s.timeWindowsText.split(',').map((t) => t.trim()).filter(Boolean)
    .map((t) => {
      let prob
      const m = t.split('@')
      const [start, end] = m[0].split('-')
      if (m[1] != null) prob = parseFloat(m[1])
      const w = { start: (start || '').trim(), end: (end || '').trim() }
      if (prob != null) w.probability = prob
      return w
    }).filter((w) => w.start && w.end)
  const cfg = {
    enabled: !!s.enabled,
    cooldown_minutes: Math.max(0, s.cooldownMin || 30),
    schedule: s.scheduleOn ? { enabled: true, time: s.scheduleTime, jitter_minutes: Math.max(0, s.scheduleJitter || 0) } : {},
    screen: {
      enabled: !!s.screenOn,
      change_ratio: Math.max(0, Math.min(1, s.screenRatio ?? 0.5)),
      probability: Math.max(0, Math.min(1, s.screenProb ?? 0.5)),
      check_interval_seconds: Math.max(1, s.screenInterval || 30),
      cooldown_minutes: Math.max(0, s.screenCooldown || 30),
    },
    idle: {
      enabled: !!s.idleOn,
      idle_minutes: Math.max(0, s.idleMinutes || 30),
      probability: Math.max(0, Math.min(1, s.idleProb ?? 0.5)),
      check_interval_seconds: Math.max(1, s.idleInterval || 60),
    },
    time_windows_enabled: !!s.windowsOn,
    time_windows: timeWindows,
  }
  try {
    await endpoints.setOutreachConfig(s.session_id, cfg)
    toast.show(t('common.saved'), 'success')
  } catch (e) {
    toast.show(t('common.saveFailed') + ': ' + (e.body?.error?.message || e.status), 'error')
  }
}

let timer = null
onMounted(async () => { await loadAll(); timer = setInterval(loadAll, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-header" v-if="!compact">
      <h2>{{ $t('outreach.title') }}</h2>
      <button class="btn btn-sm" @click="loadAll"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body" v-if="!loading">
      <!-- 概览卡 -->
      <div class="stat-grid stat-grid-3">
        <div class="stat-card"><div class="stat-icon stat-icon-blue"><Icon name="heart" :size="18" /></div><div class="stat-value">{{ enabledCount }}/5</div><div class="stat-label">{{ $t('outreach.enabledSessions') }}</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-green"><Icon name="message" :size="18" /></div><div class="stat-value">{{ totalLogs }}</div><div class="stat-label">{{ $t('outreach.totalOutreach') }}</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-yellow"><Icon name="clock" :size="18" /></div><div class="stat-value">{{ logs.length }}</div><div class="stat-label">{{ $t('outreach.recentLogs') }}</div></div>
      </div>

      <!-- 会话规则配置 -->
      <div class="card dash-mt">
        <div class="card-header">{{ $t('outreach.sessionsHint') }}</div>
        <div class="outreach-hint">{{ $t('outreach.hintPrefix') }}<b>{{ $t('outreach.hintAction') }}</b>{{ $t('outreach.hintSuffix') }}</div>
        <div v-if="sessions.length === 0" class="empty-state outreach-empty"><p class="empty-text">{{ $t('outreach.noSessions') }}</p></div>
        <div v-for="s in sessions" :key="s.session_id" class="outreach-session">
            <div class="outreach-head" @click="s._open = !s._open">
            <div class="outreach-session-head">
              <Icon :name="s._open ? 'down' : 'right'" :size="14" class="outreach-icon-muted" />
              <b class="outreach-session-title">{{ s.title }}</b>
              <span v-if="s.enabled" class="badge badge-green">{{ $t('common.enabled') }}</span>
              <span v-else class="badge badge-gray">{{ $t('common.disabled') }}</span>
            </div>
            <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.enabled" @change="saveConfig(s)" /><span class="toggle-slider"></span></label>
          </div>
          <div v-if="s._open" class="outreach-body">
            <div class="outreach-row outreach-row-align">
              <span class="outreach-lbl">{{ $t('outreach.cooldown') }}</span>
              <input class="input w-64" type="number" v-model.number="s.cooldownMin" :title="$t('outreach.cooldownTooltip')" /> <span class="outreach-unit">min</span>
              <span class="outreach-hint-text">{{ $t('outreach.cooldownHint') }}</span>
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">{{ $t('outreach.schedule') }}</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.scheduleOn" /><span class="toggle-slider"></span></label>
              <span class="outreach-unit" :class="{ off: !s.scheduleOn }">{{ $t('common.time') }}</span>
              <input class="input w-80" v-model="s.scheduleTime" placeholder="14:00" :disabled="!s.scheduleOn" :title="$t('outreach.timeTooltip')" />
              <span class="outreach-unit" :class="{ off: !s.scheduleOn }">{{ $t('outreach.jitter') }}</span>
              <input class="input w-56" type="number" v-model.number="s.scheduleJitter" :title="$t('outreach.jitterTooltip')" :disabled="!s.scheduleOn" /> <span class="outreach-unit" :class="{ off: !s.scheduleOn }">min</span>
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">{{ $t('outreach.screen') }}</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.screenOn" /><span class="toggle-slider"></span></label>
              <input class="input w-52" type="number" v-model.number="s.screenRatio" :title="$t('outreach.screenRatioTooltip')" :disabled="!s.screenOn" /> <span class="outreach-unit" :class="{ off: !s.screenOn }">{{ $t('outreach.screenRatio') }}</span>
              <input class="input w-52" type="number" v-model.number="s.screenProb" :title="$t('outreach.screenProbTooltip')" :disabled="!s.screenOn" /> <span class="outreach-unit" :class="{ off: !s.screenOn }">{{ $t('outreach.probability') }}</span>
              <input class="input w-52" type="number" v-model.number="s.screenInterval" :title="$t('outreach.screenIntervalTooltip')" :disabled="!s.screenOn" /> <span class="outreach-unit" :class="{ off: !s.screenOn }">{{ $t('outreach.intervalS') }}</span>
              <input class="input w-52" type="number" v-model.number="s.screenCooldown" :title="$t('outreach.screenCooldownTooltip')" :disabled="!s.screenOn" /> <span class="outreach-unit" :class="{ off: !s.screenOn }">{{ $t('outreach.cooldownMin') }}</span>
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">{{ $t('outreach.idle') }}</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.idleOn" /><span class="toggle-slider"></span></label>
              <input class="input w-52" type="number" v-model.number="s.idleMinutes" :title="$t('outreach.idleMinutesTooltip')" :disabled="!s.idleOn" /> <span class="outreach-unit" :class="{ off: !s.idleOn }">{{ $t('outreach.idleMin') }}</span>
              <input class="input w-52" type="number" v-model.number="s.idleProb" :title="$t('outreach.idleProbTooltip')" :disabled="!s.idleOn" /> <span class="outreach-unit" :class="{ off: !s.idleOn }">{{ $t('outreach.probability') }}</span>
              <input class="input w-52" type="number" v-model.number="s.idleInterval" :title="$t('outreach.idleIntervalTooltip')" :disabled="!s.idleOn" /> <span class="outreach-unit" :class="{ off: !s.idleOn }">{{ $t('outreach.intervalS') }}</span>
            </div>
            <div class="outreach-row outreach-row-wrap">
              <span class="outreach-lbl">{{ $t('outreach.timeWindow') }}</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.windowsOn" /><span class="toggle-slider"></span></label>
              <input class="input w-flex-200" v-model="s.timeWindowsText" placeholder="09:00-12:00@0.5,14:00-18:00@0.8" :disabled="!s.windowsOn" :title="$t('outreach.windowsTooltipPre') + '开始-结束@概率' + $t('outreach.windowsTooltipPost')" />
            </div>
            <div class="outreach-hint-mt">{{ $t('outreach.windowsFormatLabel') }}<code>开始-结束@概率</code>{{ $t('outreach.windowsFormatMid') }}<code>09:00-12:00@0.5,14:00-18:00@0.8</code>{{ $t('outreach.windowsFormatTail') }}</div>
            <div class="outreach-save">
              <button class="btn btn-sm btn-primary" @click="saveConfig(s)"><Icon name="check" :size="14" /> {{ $t('common.save') }}</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 触发记录 -->
      <div class="card dash-mt">
        <div class="card-header">{{ $t('outreach.logsTitle') }}</div>
        <div v-if="logs.length === 0" class="empty-state outreach-empty"><p class="empty-text">{{ $t('outreach.logsEmpty') }}</p></div>
        <div v-else class="activity-timeline">
          <div v-for="l in logs" :key="l.session_id + l.created_at" class="activity-item">
            <span class="activity-time">{{ formatTime(l.created_at) }}</span>
            <span class="badge" :class="l.reason === 'screen' ? 'badge-yellow' : 'badge-blue'">{{ l.reason === 'schedule' || l.reason === 'screen' || l.reason === 'idle' || l.reason === 'time_window' ? $t(reasonLabels[l.reason]) : l.reason }}</span>
            <span class="outreach-log-content">{{ l.content }}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="page-body" v-else>{{ $t('common.loading') }}</div>
  </div>
</template>

<style scoped>
.stat-icon-blue { background: rgba(88,166,255,.15); color: #58a6ff; }
.stat-icon-green { background: rgba(63,185,80,.15); color: var(--success); }
.stat-icon-yellow { background: rgba(210,153,34,.15); color: var(--warning); }
.dash-mt { margin-top: 12px; }
.outreach-hint { font-size: 12px; color: var(--text-muted); padding: 2px 0 6px; }
.outreach-empty { padding: 32px; }
.outreach-session-head { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
.outreach-session-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.outreach-icon-muted { color: var(--text-muted); }
.outreach-row-align { align-items: center; }
.outreach-hint-text { font-size: 12px; color: var(--text-muted); }
.outreach-row-wrap { flex-wrap: wrap; }
.outreach-hint-mt { font-size: 12px; color: var(--text-muted); margin-top: 4px; }
.outreach-save { display: flex; justify-content: flex-end; margin-top: 10px; }
.outreach-log-content { color: var(--text-muted); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.outreach-loading { text-align: center; padding: 60px; color: var(--text-muted); }
.stat-grid-3 { grid-template-columns: repeat(3, 1fr); }
</style>
