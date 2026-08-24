<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'

const router = useRouter()
const { t } = useI18n()
const loading = ref(true)
const apiStatus = ref('-')
const dash = ref(null)
const libs = ref([])
const sessions = ref([])
const perception = ref(null)
const proactiveLogs = ref([])
const enabledOutreach = ref(0)

const modules = computed(() =>
  Object.entries(dash.value?.modules || {}).map(([n, s]) => ({ name: n, status: s }))
)
const healthyCount = computed(() => modules.value.filter((m) => m.status === 'healthy').length)
const moduleOk = computed(() => modules.value.length ? `${healthyCount.value}/${modules.value.length}` : '-')
const totalEvents = computed(() => libs.value.reduce((s, l) => s + (l.event_count || 0), 0))
const totalSessions = computed(() => sessions.value.length)
const activeApp = computed(() => perception.value?.world_state?.active_app || '-')
const activeWindow = computed(() => perception.value?.world_state?.active_window || '-')
const perceptionRunning = computed(() => perception.value?.status === 'running')

// 借鉴 DeterminFlow 看板：活跃会话 / 高频调用路径统计
const recentSessions = computed(() =>
  [...sessions.value].sort((a, b) => (b.last_active || '').localeCompare(a.last_active || '')).slice(0, 8)
)
const totalApi = computed(() => apiReqStats.value?.total || 0)
const topPaths = computed(() => {
  const counts = {}
  ;(apiReq.value.items || []).forEach((r) => { counts[r.path] = (counts[r.path] || 0) + 1 })
  const arr = Object.entries(counts).map(([path, n]) => ({ path, n })).sort((a, b) => b.n - a.n).slice(0, 8)
  const max = Math.max(1, ...arr.map((x) => x.n))
  return { arr, max }
})

function statusLabel(s) { return s === 'healthy' ? t('dashboard.healthy') : s === 'degraded' ? t('dashboard.degraded') : s }
function statusBadge(s) { return s === 'healthy' ? 'badge-green' : s === 'degraded' ? 'badge-yellow' : 'badge-red' }

async function loadData() {
  try {
    const [d, health, libsR, sess, perc, plog] = await Promise.all([
      endpoints.dashboard().catch(() => null),
      endpoints.health().catch(() => null),
      endpoints.memoryLibs().catch(() => null),
      endpoints.sessions().catch(() => null),
      endpoints.perceptionStatus().catch(() => null),
      endpoints.proactiveLogs(5).catch(() => null),
    ])
    dash.value = d?.data || null
    apiStatus.value = health?.data?.status === 'healthy' ? t('dashboard.healthy') : health?.data?.status || '-'
    libs.value = libsR?.data?.libs || []
    sessions.value = sess?.data || []
    perception.value = perc?.data || null
    proactiveLogs.value = plog?.data?.logs || []
    enabledOutreach.value = (sess?.data || []).filter((s) => (s.metadata || {}).outreach?.enabled).length
  } catch {} finally { loading.value = false }
  loadApiRequests(); loadApiStats()
}

let timer = null
onMounted(async () => { await loadData(); timer = setInterval(loadData, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

// ── API 请求日志（持久化，可筛选/分页/统计）──
const apiReq = ref({ items: [], total: 0 })
const apiReqStats = ref(null)
const apiReqFilter = ref({ method: '', status: '', path: '', since_hours: 0 })
const apiReqPage = ref(0)
const API_PAGE = 50
async function loadApiRequests() {
  try {
    const f = apiReqFilter.value
    const q = new URLSearchParams()
    if (f.method) q.set('method', f.method)
    if (f.status) q.set('status', f.status)
    if (f.path) q.set('path', f.path)
    if (f.since_hours) q.set('since_hours', f.since_hours)
    q.set('limit', API_PAGE)
    q.set('offset', apiReqPage.value * API_PAGE)
    const r = await fetch('/api/management/api-requests?' + q.toString(), { headers: { Accept: 'application/json' } })
    const d = await r.json()
    apiReq.value = d?.data || { items: [], total: 0 }
  } catch (e) {}
}
async function loadApiStats() {
  try {
    const f = apiReqFilter.value
    const q = f.since_hours ? '?since_hours=' + f.since_hours : ''
    const r = await fetch('/api/management/api-requests/stats' + q, { headers: { Accept: 'application/json' } })
    const d = await r.json()
    apiReqStats.value = d?.data || null
  } catch (e) {}
}
function applyApiFilter() { apiReqPage.value = 0; loadApiRequests(); loadApiStats() }
function apiReqNext() { apiReqPage.value += 1; loadApiRequests() }
function apiReqPrev() { if (apiReqPage.value > 0) { apiReqPage.value -= 1; loadApiRequests() } }

// ── API 请求详情（参数 + 返回值）──
const apiDetail = ref(null)
function openApiDetail(r) {
  apiDetail.value = r
  // 详情面板渲染在 50 行日志表格之后——不滚动用户根本看不到（"点了没反应"的根因）
  nextTick(() => {
    const el = document.querySelector('.dash-detail')
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}
function closeApiDetail() { apiDetail.value = null }
function formatBody(s) {
  if (!s) return t('dashboard.noRecord')
  const text = String(s)
  try { return JSON.stringify(JSON.parse(text), null, 2) }
  catch { return text }
}
function copyApiBody(s) {
  const text = s || ''
  navigator.clipboard?.writeText(text).catch(() => {})
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>{{ $t('dashboard.title') }}</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body" v-if="!loading">
      <!-- 顶部统计卡 -->
      <div class="health-grid">
        <div class="health-card" @click="router.push('/chat')" :title="$t('dashboard.enterChat')">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: apiStatus === $t('dashboard.healthy') ? 0 : 94 }"/></svg>
            <div class="ring-label"><Icon name="message" :size="18" /></div>
          </div>
          <div class="health-name">API {{ apiStatus }}</div>
        </div>
        <div class="health-card">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 * (1 - healthyCount / Math.max(modules.length || 1, 1)) }"/></svg>
            <div class="ring-label">{{ moduleOk }}</div>
          </div>
          <div class="health-name">{{ $t('dashboard.moduleHealth') }}</div>
        </div>
        <div class="health-card" @click="router.push('/memory')" :title="$t('dashboard.viewMemory')">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 }"/></svg>
            <div class="ring-label"><Icon name="database" :size="18" /></div>
          </div>
          <div class="health-name">{{ $t('dashboard.memoryCount', { count: totalEvents }) }}</div>
        </div>
        <div class="health-card" @click="router.push('/chat')" :title="$t('dashboard.enterChat')">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 }"/></svg>
            <div class="ring-label">{{ totalSessions }}</div>
          </div>
          <div class="health-name">{{ $t('dashboard.sessions') }}</div>
        </div>
        <div class="health-card" :title="$t('dashboard.apiTotalTitle')">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: totalApi ? 0 : 188.5, stroke: '#8b5cf6' }"/></svg>
            <div class="ring-label">{{ totalApi }}</div>
          </div>
          <div class="health-name">{{ $t('dashboard.apiRequests') }}</div>
        </div>
        <div class="health-card" :title="$t('dashboard.enabledOutreachTitle')">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: enabledOutreach ? 0 : 188.5, stroke: '#22C55E' }"/></svg>
            <div class="ring-label">{{ enabledOutreach }}</div>
          </div>
          <div class="health-name">{{ $t('dashboard.outreach') }}</div>
        </div>
      </div>

      <!-- 当前环境 + 记忆库 -->
      <div class="dash-grid-2">
        <div class="card">
          <div class="card-header">{{ $t('dashboard.currentEnv') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('dashboard.perception') }}</div></div>
            <div class="setting-ctl"><span class="badge" :class="perceptionRunning ? 'badge-green' : 'badge-red'">{{ perceptionRunning ? $t('dashboard.running') : $t('dashboard.stopped') }}</span></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('dashboard.currentApp') }}</div></div>
            <div class="setting-ctl dash-ctl-end"><span class="dash-muted">{{ activeApp }}</span></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('dashboard.currentWindow') }}</div></div>
            <div class="setting-ctl dash-ctl-end"><span class="dash-window-text">{{ activeWindow }}</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-header">{{ $t('dashboard.memoryLibs', { count: libs.length }) }}</div>
          <div v-if="libs.length">
            <div v-for="l in libs" :key="l.name" class="setting-row">
              <div class="lbl"><div class="t">{{ l.name }} <span v-if="l.current" class="badge badge-blue">{{ $t('dashboard.current') }}</span></div></div>
              <div class="setting-ctl"><span class="dash-muted">{{ l.event_count ?? 0 }} {{ $t('dashboard.countSuffix') }}</span></div>
            </div>
          </div>
          <div v-else class="dash-empty">{{ $t('dashboard.noMemoryLibs') }}</div>
        </div>
      </div>

      <!-- 模块状态 -->
      <div class="card dash-mt">
        <div class="card-header">{{ $t('dashboard.moduleStatus', { count: modules.length }) }}</div>
        <table class="data-table" v-if="modules.length > 0">
          <thead><tr><th>{{ $t('dashboard.module') }}</th><th>{{ $t('common.status') }}</th></tr></thead>
          <tbody><tr v-for="m in modules" :key="m.name"><td><strong>{{ m.name }}</strong></td><td><span class="badge" :class="statusBadge(m.status)">{{ statusLabel(m.status) }}</span></td></tr></tbody>
        </table>
        <div v-else class="dash-empty-lg">{{ $t('dashboard.noModuleData') }}</div>
      </div>

      <!-- 最近会话（借鉴 DeterminFlow 会话表格） -->
      <div class="card dash-mt">
        <div class="card-header">{{ $t('dashboard.recentSessions', { count: sessions.length }) }}</div>
        <div class="overflow-x-auto" v-if="recentSessions.length">
          <table class="data-table">
            <thead>
              <tr>
                <th class="text-left">ID</th>
                <th class="text-left">{{ $t('common.type') }}</th>
                <th class="text-left">{{ $t('dashboard.sessionTitle') }}</th>
                <th class="text-right">{{ $t('dashboard.msgCount') }}</th>
                <th class="text-right">{{ $t('dashboard.updatedAt') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in recentSessions" :key="s.session_id" @click="router.push('/chat?session=' + s.session_id)" class="dash-session-row">
                <td class="dash-session-id">{{ s.session_id.slice(0, 18) }}</td>
                <td><span class="badge" :class="s.session_id === 'pet_main' ? 'badge-blue' : 'badge-gray'">{{ s.session_id === 'pet_main' ? 'PET' : s.session_id === 'chat' ? 'MAIN' : 'SUB' }}</span></td>
                <td class="dash-session-title">{{ s.title || s.session_id.slice(0, 12) }}</td>
                <td class="text-right dash-session-meta">{{ s.message_count ?? 0 }}</td>
                <td class="text-right dash-session-time">{{ (s.last_active || '').slice(5, 16) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="dash-empty">{{ $t('dashboard.noSessions') }}</div>
      </div>

      <!-- 调用频率 + 主动搭话时间线（借鉴 DeterminFlow 双栏） -->
      <div class="dash-grid-2">
        <div class="card">
          <div class="card-header">{{ $t('dashboard.apiFreq') }}</div>
          <div v-if="topPaths.arr.length">
            <div v-for="t in topPaths.arr" :key="t.path" class="dash-freq-item">
              <div class="dash-freq-header">
                <span class="dash-freq-path">{{ t.path }}</span>
                <span>{{ t.n }}</span>
              </div>
              <div class="dash-freq-bar">
                <div class="dash-freq-fill" :style="{ width: (t.n / topPaths.max) * 100 + '%' }"></div>
              </div>
            </div>
          </div>
          <div v-else class="dash-empty">{{ $t('dashboard.noCallRecords') }}</div>
        </div>
        <div class="card">
          <div class="card-header dash-card-header">
            <span>{{ $t('dashboard.outreachTimeline') }}</span>
            <button class="btn btn-sm" @click="router.push('/outreach')">{{ $t('dashboard.manageRules') }}</button>
          </div>
          <div v-if="proactiveLogs.length">
            <div v-for="(l, i) in proactiveLogs.slice(0, 8)" :key="l.created_at" class="dash-timeline-item">
              <span class="dash-timeline-dot"></span>
              <span v-if="i < proactiveLogs.slice(0, 8).length - 1" class="dash-timeline-line"></span>
              <div class="dash-timeline-content">
                <div class="dash-timeline-header">
                  <span class="badge badge-blue dash-timeline-badge">{{ ({schedule: $t('dashboard.reasonSchedule'), screen: $t('dashboard.reasonScreen'), idle: $t('dashboard.reasonIdle'), time_window: $t('dashboard.reasonTimeWindow')})[l.reason] || l.reason }}</span>
                  <span class="dash-timeline-time">{{ (l.created_at||'').slice(5, 16) }}</span>
                </div>
                <div class="dash-timeline-text">{{ l.content }}</div>
              </div>
            </div>
          </div>
          <div v-else class="dash-empty">{{ $t('dashboard.noTriggerRecords') }}</div>
        </div>
      </div>

      <!-- API 请求日志（持久化 + 筛选 + 分页 + 统计） -->
      <div class="card dash-mt">
        <div class="card-header dash-card-header-wrap">
          <span>{{ $t('dashboard.apiLog', { count: apiReq.total }) }}</span>
          <div class="dash-filter-bar">
            <select v-model="apiReqFilter.method" @change="applyApiFilter" class="input w-90">
              <option value="">{{ $t('dashboard.allMethods') }}</option><option value="POST">POST</option><option value="GET">GET</option>
            </select>
            <select v-model="apiReqFilter.status" @change="applyApiFilter" class="input w-100">
              <option value="">{{ $t('dashboard.allStatuses') }}</option><option value="2">2xx</option><option value="3">3xx</option><option value="4">4xx</option><option value="5">5xx</option>
            </select>
            <select v-model="apiReqFilter.since_hours" @change="applyApiFilter" class="input w-100">
              <option :value="0">{{ $t('dashboard.allTime') }}</option><option :value="1">{{ $t('dashboard.lastHour') }}</option><option :value="24">{{ $t('dashboard.last24h') }}</option><option :value="168">{{ $t('dashboard.last7d') }}</option>
            </select>
            <input v-model="apiReqFilter.path" @keydown.enter="applyApiFilter" :placeholder="$t('dashboard.pathFilter')" class="input w-140" />
            <button class="btn btn-sm" @click="applyApiFilter"><Icon name="search" :size="13" /> {{ $t('dashboard.filter') }}</button>
            <button class="btn btn-sm" @click="applyApiFilter"><Icon name="refresh" :size="13" /> {{ $t('common.refresh') }}</button>
          </div>
        </div>

        <div v-if="apiReqStats" class="dash-api-stats">
          <span>{{ $t('dashboard.total') }} <b>{{ apiReqStats.total }}</b></span>
          <span>{{ $t('dashboard.avgMs') }} <b>{{ apiReqStats.avg_ms }}ms</b></span>
          <span v-for="(v, k) in apiReqStats.by_method" :key="'m' + k">{{ k }}: <b>{{ v }}</b></span>
          <span v-for="(v, k) in apiReqStats.by_status" :key="'s' + k">{{ k }}xx: <b>{{ v }}</b></span>
        </div>

        <table class="data-table" v-if="apiReq.items.length">
          <thead><tr><th>{{ $t('common.time') }}</th><th>{{ $t('dashboard.method') }}</th><th>{{ $t('dashboard.path') }}</th><th>{{ $t('common.status') }}</th><th>{{ $t('dashboard.costMs') }}</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in apiReq.items" :key="apiReqPage * API_PAGE + i">
              <td class="dash-api-time">{{ r.time }}</td>
              <td><span class="badge" :class="r.method === 'POST' ? 'badge-blue' : 'badge-gray'">{{ r.method }}</span></td>
              <td class="dash-api-path">{{ r.path }}</td>
              <td><span class="badge" :class="r.status < 400 ? 'badge-green' : 'badge-red'">{{ r.status }}</span></td>
              <td class="dash-api-ms">{{ r.ms != null ? r.ms + 'ms' : '-' }}</td>
              <td class="dash-api-action">
                <button class="btn btn-sm" @click="openApiDetail(r)" :class="{ 'btn-primary': apiDetail === r }"><Icon name="search" :size="12" /> {{ $t('common.details') }}</button>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="dash-api-empty">{{ $t('dashboard.noRequestRecords') }}</div>

        <!-- 请求详情：参数 + 返回值 -->
        <div v-if="apiDetail" class="dash-detail">
          <div class="dash-detail-header">
            <div class="dash-detail-info">
              <span class="badge" :class="apiDetail.method === 'POST' ? 'badge-blue' : 'badge-gray'">{{ apiDetail.method }}</span>
              <span class="dash-detail-path">{{ apiDetail.path }}</span>
              <span class="badge" :class="apiDetail.status < 400 ? 'badge-green' : 'badge-red'">{{ apiDetail.status }}</span>
              <span class="dash-detail-meta">{{ apiDetail.time }} · {{ apiDetail.ms != null ? apiDetail.ms + 'ms' : '-' }}</span>
            </div>
            <div class="dash-detail-actions">
              <button class="btn btn-sm" @click="copyApiBody(apiDetail.request_body)"><Icon name="copy" :size="12" /> {{ $t('dashboard.copyRequest') }}</button>
              <button class="btn btn-sm" @click="copyApiBody(apiDetail.response_body)"><Icon name="copy" :size="12" /> {{ $t('dashboard.copyResponse') }}</button>
              <button class="btn btn-sm" @click="closeApiDetail()"><Icon name="x" :size="12" /> {{ $t('common.close') }}</button>
            </div>
          </div>
          <div class="dash-detail-body">
            <div class="dash-detail-col">
              <div class="dash-detail-label">{{ $t('dashboard.requestParams') }}</div>
              <pre class="diag-pre dash-detail-pre">{{ formatBody(apiDetail.request_body) }}</pre>
            </div>
            <div class="dash-detail-col">
              <div class="dash-detail-label">{{ $t('dashboard.responseValue') }}</div>
              <pre class="diag-pre dash-detail-pre">{{ formatBody(apiDetail.response_body) }}</pre>
            </div>
          </div>
        </div>

        <div class="dash-footer">
          <span class="dash-footer-info">{{ $t('dashboard.totalCount', { count: apiReq.total }) }}</span>
          <button class="btn btn-sm" :disabled="apiReqPage <= 0" @click="apiReqPrev">{{ $t('dashboard.prevPage') }}</button>
          <button class="btn btn-sm" :disabled="(apiReqPage + 1) * API_PAGE >= apiReq.total" @click="apiReqNext">{{ $t('dashboard.nextPage') }}</button>
        </div>
      </div>
    </div>
    <div class="page-body dash-loading" v-else>{{ $t('app.loading') }}</div>
  </div>
</template>

<style scoped>
.dash-grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.dash-grid-2 > .card { margin-top: 0; }
.dash-mt { margin-top: 12px; }
.dash-ctl-end { justify-content: flex-end; }
.dash-muted { color: var(--text-muted); }
.dash-empty { text-align: center; padding: 24px; color: var(--text-muted); }
.dash-empty-lg { text-align: center; padding: 40px; color: var(--text-muted); }
.dash-loading { text-align: center; padding: 60px; color: var(--text-muted); }
.dash-session-row { cursor: pointer; }
.dash-session-id { font-family: var(--font-mono); font-size: 12px; color: var(--accent); }
.dash-session-title { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
.dash-session-meta { color: var(--text-muted); font-size: 12px; }
.dash-session-time { color: var(--text-muted); font-size: 12px; white-space: nowrap; }
.dash-window-text { color: var(--text-muted); max-width: 260px; text-align: right; word-break: break-all; }
.dash-api-path { word-break: break-all; font-size: 12px; }
.dash-api-time { color: var(--text-muted); white-space: nowrap; }
.dash-api-ms { color: var(--text-muted); white-space: nowrap; }
.dash-api-action { text-align: right; white-space: nowrap; }
.dash-api-empty { text-align: center; padding: 24px; color: var(--text-muted); }
.dash-api-stats { display: flex; gap: 12px; flex-wrap: wrap; padding: 8px 0; font-size: 12px; color: var(--text-secondary); }
.dash-detail { margin-top: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); overflow: hidden; background: var(--bg-secondary); }
.dash-detail-header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 14px; background: var(--bg-tertiary); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.dash-detail-info { display: flex; align-items: center; gap: 10px; font-size: 13px; flex-wrap: wrap; }
.dash-detail-path { word-break: break-all; }
.dash-detail-meta { color: var(--text-muted); font-size: 12px; }
.dash-detail-actions { display: flex; gap: 8px; }
.dash-detail-body { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 14px; }
.dash-detail-col { min-width: 0; }
.dash-detail-label { font-size: 12px; font-weight: 600; color: var(--text-secondary); margin-bottom: 6px; }
.dash-detail-pre { max-height: 360px; margin: 0; }
.dash-footer { display: flex; justify-content: flex-end; gap: 8px; padding-top: 8px; align-items: center; }
.dash-footer-info { font-size: 12px; color: var(--text-muted); }
.dash-freq-item { margin-bottom: 8px; }
.dash-freq-header { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-muted); margin-bottom: 3px; }
.dash-freq-path { font-family: var(--font-mono); max-width: 70%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dash-freq-bar { height: 6px; border-radius: 3px; background: var(--bg-secondary); overflow: hidden; }
.dash-freq-fill { height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--purple), #ff5f8f); }
.dash-timeline-item { display: flex; gap: 10px; position: relative; padding-left: 18px; margin-bottom: 12px; }
.dash-timeline-dot { position: absolute; left: 0; top: 5px; width: 8px; height: 8px; border-radius: 50%; background: var(--success); box-shadow: 0 0 0 3px rgba(34, 197, 94, .15); }
.dash-timeline-line { position: absolute; left: 3.5px; top: 14px; bottom: -6px; width: 1px; background: var(--border); }
.dash-timeline-content { flex: 1; min-width: 0; }
.dash-timeline-header { display: flex; gap: 6px; align-items: center; margin-bottom: 2px; }
.dash-timeline-time { color: var(--text-muted); font-size: 11px; }
.dash-timeline-text { font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dash-filter-bar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.dash-card-header { display: flex; justify-content: space-between; align-items: center; }
.dash-card-header-wrap { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
.dash-timeline-badge { font-size: 10px; }
</style>
