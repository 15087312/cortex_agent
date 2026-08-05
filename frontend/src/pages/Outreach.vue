<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const sessions = ref([])
const logs = ref([])
const totalLogs = ref(0)
const loading = ref(true)

const enabledCount = computed(() => sessions.value.filter((s) => s.enabled).length)
const reasonLabels = { schedule: '定点发送', screen: '屏幕变化', idle: '空闲', time_window: '时段' }

async function loadAll() {
  loading.value = true
  try {
    const [sr, lr] = await Promise.all([
      endpoints.sessions().catch(() => null),
      endpoints.proactiveLogs(50).catch(() => null),
    ])
    sessions.value = (sr?.data || []).map((s) => {
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
        _open: false,
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
    toast.show('已保存', 'success')
  } catch (e) {
    toast.show('保存失败: ' + (e.body?.error?.message || e.status), 'error')
  }
}

let timer = null
onMounted(async () => { await loadAll(); timer = setInterval(loadAll, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-header">
      <h2>主动搭话</h2>
      <button class="btn btn-sm" @click="loadAll"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body" v-if="!loading">
      <!-- 概览卡 -->
      <div class="stat-grid" style="grid-template-columns:repeat(3,1fr)">
        <div class="stat-card"><div class="stat-icon" style="background:rgba(88,166,255,.15);color:#58a6ff"><Icon name="heart" :size="18" /></div><div class="stat-value">{{ enabledCount }}/5</div><div class="stat-label">已开启会话</div></div>
        <div class="stat-card"><div class="stat-icon" style="background:rgba(63,185,80,.15);color:#3fb950"><Icon name="message" :size="18" /></div><div class="stat-value">{{ totalLogs }}</div><div class="stat-label">累计搭话</div></div>
        <div class="stat-card"><div class="stat-icon" style="background:rgba(210,153,34,.15);color:#d29922"><Icon name="clock" :size="18" /></div><div class="stat-value">{{ logs.length }}</div><div class="stat-label">最近记录</div></div>
      </div>

      <!-- 会话规则配置 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">会话规则（最多开启 5 个会话）</div>
        <div v-if="sessions.length === 0" class="empty-state" style="padding:32px"><p class="empty-text">暂无会话</p></div>
        <div v-for="s in sessions" :key="s.session_id" class="outreach-session">
          <div class="outreach-head" @click="s._open = !s._open">
            <div style="display:flex;align-items:center;gap:8px;flex:1;min-width:0">
              <Icon :name="s._open ? 'down' : 'right'" :size="14" style="color:var(--text-muted)" />
              <b style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.title }}</b>
              <span v-if="s.enabled" class="badge badge-green">已开启</span>
              <span v-else class="badge badge-gray">关闭</span>
            </div>
            <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.enabled" @change="saveConfig(s)" /><span class="toggle-slider"></span></label>
          </div>
          <div v-if="s._open" class="outreach-body">
            <div class="outreach-row">
              <span class="outreach-lbl">综合冷却</span>
              <input class="input" type="number" v-model.number="s.cooldownMin" style="width:64px;text-align:right" /> <span class="outreach-unit">min</span>
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">定点发送</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.scheduleOn" /><span class="toggle-slider"></span></label>
              <span class="outreach-unit" :class="{ off: !s.scheduleOn }">时间</span>
              <input class="input" v-model="s.scheduleTime" style="width:80px" placeholder="14:00" :disabled="!s.scheduleOn" />
              <span class="outreach-unit" :class="{ off: !s.scheduleOn }">±</span>
              <input class="input" type="number" v-model.number="s.scheduleJitter" style="width:56px;text-align:right" title="误差(分钟)" :disabled="!s.scheduleOn" /> <span class="outreach-unit" :class="{ off: !s.scheduleOn }">min</span>
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">屏幕触发</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.screenOn" /><span class="toggle-slider"></span></label>
              <input class="input" type="number" v-model.number="s.screenRatio" style="width:52px;text-align:right" title="变化幅度" :disabled="!s.screenOn" /> 变化
              <input class="input" type="number" v-model.number="s.screenProb" style="width:52px;text-align:right" title="概率 0-1" :disabled="!s.screenOn" /> 概率
              <input class="input" type="number" v-model.number="s.screenInterval" style="width:52px;text-align:right" title="判定间隔(s)" :disabled="!s.screenOn" /> s
              <input class="input" type="number" v-model.number="s.screenCooldown" style="width:52px;text-align:right" title="冷却(min)" :disabled="!s.screenOn" /> min
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">空闲触发</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.idleOn" /><span class="toggle-slider"></span></label>
              <input class="input" type="number" v-model.number="s.idleMinutes" style="width:52px;text-align:right" title="空闲(min)" :disabled="!s.idleOn" /> min
              <input class="input" type="number" v-model.number="s.idleProb" style="width:52px;text-align:right" title="概率 0-1" :disabled="!s.idleOn" /> 概率
              <input class="input" type="number" v-model.number="s.idleInterval" style="width:52px;text-align:right" title="判定间隔(s)" :disabled="!s.idleOn" /> s
            </div>
            <div class="outreach-row">
              <span class="outreach-lbl">时段触发</span>
              <label class="toggle-switch" @click.stop><input type="checkbox" v-model="s.windowsOn" /><span class="toggle-slider"></span></label>
              <input class="input" v-model="s.timeWindowsText" style="flex:1;min-width:180px" placeholder="09:00-12:00@0.5,14:00-18:00@0.8" :disabled="!s.windowsOn" />
            </div>
            <div style="display:flex;justify-content:flex-end;margin-top:10px">
              <button class="btn btn-sm btn-primary" @click="saveConfig(s)"><Icon name="check" :size="14" /> 保存</button>
            </div>
          </div>
        </div>
      </div>

      <!-- 触发记录 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">触发记录</div>
        <div v-if="logs.length === 0" class="empty-state" style="padding:32px"><p class="empty-text">暂无主动搭话记录（开启会话并满足规则后触发）</p></div>
        <div v-else class="activity-timeline">
          <div v-for="l in logs" :key="l.session_id + l.created_at" class="activity-item">
            <span class="activity-time">{{ formatTime(l.created_at) }}</span>
            <span class="badge" :class="l.reason === 'screen' ? 'badge-yellow' : 'badge-blue'">{{ reasonLabels[l.reason] || l.reason }}</span>
            <span style="color:var(--text-muted);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ l.content }}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="page-body" v-else style="text-align:center;padding:60px;color:var(--text-muted)">加载中...</div>
  </div>
</template>
