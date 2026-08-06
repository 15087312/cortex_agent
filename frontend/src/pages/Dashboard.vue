<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { endpoints } from '@/api.js'
import Icon from '@/components/Icon.vue'

const router = useRouter()
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
const currentLib = computed(() => libs.value.find((l) => l.current))
const totalSessions = computed(() => sessions.value.length)
const activeApp = computed(() => perception.value?.world_state?.active_app || '-')
const activeWindow = computed(() => perception.value?.world_state?.active_window || '-')
const perceptionRunning = computed(() => perception.value?.status === 'running')

function statusLabel(s) { return s === 'healthy' ? '正常' : s === 'degraded' ? '降级' : s }
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
    apiStatus.value = health?.data?.status === 'healthy' ? '健康' : health?.data?.status || '-'
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
    const r = await fetch('/management/api-requests?' + q.toString(), { headers: { Accept: 'application/json' } })
    const d = await r.json()
    apiReq.value = d?.data || { items: [], total: 0 }
  } catch (e) {}
}
async function loadApiStats() {
  try {
    const f = apiReqFilter.value
    const q = f.since_hours ? '?since_hours=' + f.since_hours : ''
    const r = await fetch('/management/api-requests/stats' + q, { headers: { Accept: 'application/json' } })
    const d = await r.json()
    apiReqStats.value = d?.data || null
  } catch (e) {}
}
function applyApiFilter() { apiReqPage.value = 0; loadApiRequests(); loadApiStats() }
function apiReqNext() { apiReqPage.value += 1; loadApiRequests() }
function apiReqPrev() { if (apiReqPage.value > 0) { apiReqPage.value -= 1; loadApiRequests() } }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>仪表盘</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body" v-if="!loading">
      <!-- 顶部统计卡 -->
      <div class="health-grid">
        <div class="health-card" @click="router.push('/chat')" title="点击进入对话">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: apiStatus === '健康' ? 0 : 94 }"/></svg>
            <div class="ring-label"><Icon name="message" :size="18" /></div>
          </div>
          <div class="health-name">API {{ apiStatus }}</div>
        </div>
        <div class="health-card">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 * (1 - healthyCount / Math.max(modules.length || 1, 1)) }"/></svg>
            <div class="ring-label">{{ moduleOk }}</div>
          </div>
          <div class="health-name">模块健康</div>
        </div>
        <div class="health-card" @click="router.push('/memory')" title="点击查看记忆">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 }"/></svg>
            <div class="ring-label"><Icon name="database" :size="18" /></div>
          </div>
          <div class="health-name">记忆 {{ totalEvents }} 条</div>
        </div>
        <div class="health-card" @click="router.push('/chat')" title="点击进入对话">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 }"/></svg>
            <div class="ring-label">{{ totalSessions }}</div>
          </div>
          <div class="health-name">会话</div>
        </div>
      </div>

      <!-- 主动搭话状态 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
          <span>主动搭话</span>
          <button class="btn btn-sm" @click="router.push('/outreach')">管理规则</button>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
          <div>
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px">已开启会话</div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <span v-for="s in sessions.filter(x => (x.metadata||{}).outreach?.enabled).slice(0,5)" :key="s.session_id" class="badge badge-green" style="max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.title || s.session_id.slice(0,8) }}</span>
              <span v-if="!enabledOutreach" style="color:var(--text-muted);font-size:13px">未开启任何会话</span>
            </div>
          </div>
          <div>
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px">最近触发</div>
            <div v-if="proactiveLogs.length">
              <div v-for="l in proactiveLogs.slice(0,3)" :key="l.created_at" style="display:flex;gap:6px;align-items:baseline;font-size:13px;margin-bottom:4px">
                <span class="badge badge-blue" style="font-size:10px">{{ ({schedule:'定点',screen:'屏幕',idle:'空闲',time_window:'时段'})[l.reason] || l.reason }}</span>
                <span style="color:var(--text-muted);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ l.content }}</span>
                <span style="color:var(--text-muted);font-size:11px">{{ (l.created_at||'').slice(5,16) }}</span>
              </div>
            </div>
            <div v-else style="color:var(--text-muted);font-size:13px">暂无触发记录</div>
          </div>
        </div>
      </div>

      <!-- 当前环境 + 记忆库 + 最近会话 -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">
        <div class="card">
          <div class="card-header">当前环境</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">感知系统</div></div>
            <div class="setting-ctl"><span class="badge" :class="perceptionRunning ? 'badge-green' : 'badge-red'">{{ perceptionRunning ? '运行中' : '已停止' }}</span></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">当前应用</div></div>
            <div class="setting-ctl" style="justify-content:flex-end"><span style="color:var(--text-muted)">{{ activeApp }}</span></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">当前窗口</div></div>
            <div class="setting-ctl" style="justify-content:flex-end"><span style="color:var(--text-muted);max-width:260px;text-align:right;word-break:break-all">{{ activeWindow }}</span></div>
          </div>
        </div>
        <div class="card">
          <div class="card-header">记忆库 ({{ libs.length }})</div>
          <div v-if="libs.length">
            <div v-for="l in libs" :key="l.name" class="setting-row">
              <div class="lbl"><div class="t">{{ l.name }} <span v-if="l.current" class="badge badge-blue">当前</span></div></div>
              <div class="setting-ctl"><span style="color:var(--text-muted)">{{ l.event_count ?? 0 }} 条</span></div>
            </div>
          </div>
          <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无记忆库</div>
        </div>
      </div>

      <!-- 模块状态 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">模块状态 ({{ modules.length }})</div>
        <table class="data-table" v-if="modules.length > 0">
          <thead><tr><th>模块</th><th>状态</th></tr></thead>
          <tbody><tr v-for="m in modules" :key="m.name"><td><strong>{{ m.name }}</strong></td><td><span class="badge" :class="statusBadge(m.status)">{{ statusLabel(m.status) }}</span></td></tr></tbody>
        </table>
        <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">暂无模块数据</div>
      </div>

      <!-- 最近会话 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">最近会话</div>
        <div v-if="sessions.length">
          <div v-for="s in sessions.slice(0, 6)" :key="s.session_id" class="setting-row" @click="router.push('/chat?session=' + s.session_id)" style="cursor:pointer">
            <div class="lbl"><div class="t">{{ s.title || s.session_id.slice(0, 12) }}</div></div>
            <div class="setting-ctl"><span style="color:var(--text-muted)">{{ s.message_count ?? 0 }} 条 · {{ (s.last_active || '').slice(5, 16) }}</span></div>
          </div>
        </div>
        <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无会话</div>
      </div>

      <!-- API 请求日志（持久化 + 筛选 + 分页 + 统计） -->
      <div class="card" style="margin-top:12px">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <span>API 请求日志 ({{ apiReq.total }})</span>
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select v-model="apiReqFilter.method" @change="applyApiFilter" class="input" style="width:90px">
              <option value="">全部方法</option><option value="POST">POST</option><option value="GET">GET</option>
            </select>
            <select v-model="apiReqFilter.status" @change="applyApiFilter" class="input" style="width:100px">
              <option value="">全部状态</option><option value="2">2xx</option><option value="3">3xx</option><option value="4">4xx</option><option value="5">5xx</option>
            </select>
            <select v-model="apiReqFilter.since_hours" @change="applyApiFilter" class="input" style="width:100px">
              <option :value="0">全部时间</option><option :value="1">最近1小时</option><option :value="24">最近24小时</option><option :value="168">最近7天</option>
            </select>
            <input v-model="apiReqFilter.path" @keydown.enter="applyApiFilter" placeholder="路径筛选" class="input" style="width:140px" />
            <button class="btn btn-sm" @click="applyApiFilter"><Icon name="search" :size="13" /> 筛选</button>
            <button class="btn btn-sm" @click="applyApiFilter"><Icon name="refresh" :size="13" /> 刷新</button>
          </div>
        </div>

        <div v-if="apiReqStats" style="display:flex;gap:12px;flex-wrap:wrap;padding:8px 0;font-size:12px;color:var(--text-secondary)">
          <span>总计 <b>{{ apiReqStats.total }}</b></span>
          <span>平均耗时 <b>{{ apiReqStats.avg_ms }}ms</b></span>
          <span v-for="(v, k) in apiReqStats.by_method" :key="'m' + k">{{ k }}: <b>{{ v }}</b></span>
          <span v-for="(v, k) in apiReqStats.by_status" :key="'s' + k">{{ k }}xx: <b>{{ v }}</b></span>
        </div>

        <table class="data-table" v-if="apiReq.items.length">
          <thead><tr><th>时间</th><th>方法</th><th>路径</th><th>状态</th><th>耗时</th></tr></thead>
          <tbody>
            <tr v-for="(r, i) in apiReq.items" :key="apiReqPage * API_PAGE + i">
              <td style="color:var(--text-muted);white-space:nowrap">{{ r.time }}</td>
              <td><span class="badge" :class="r.method === 'POST' ? 'badge-blue' : 'badge-gray'">{{ r.method }}</span></td>
              <td style="word-break:break-all;font-size:12px">{{ r.path }}</td>
              <td><span class="badge" :class="r.status < 400 ? 'badge-green' : 'badge-red'">{{ r.status }}</span></td>
              <td style="color:var(--text-muted);white-space:nowrap">{{ r.ms != null ? r.ms + 'ms' : '-' }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无请求记录（发送 API 请求后会显示在这里）</div>

        <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:8px;align-items:center">
          <span style="font-size:12px;color:var(--text-muted)">共 {{ apiReq.total }} 条</span>
          <button class="btn btn-sm" :disabled="apiReqPage <= 0" @click="apiReqPrev">上一页</button>
          <button class="btn btn-sm" :disabled="(apiReqPage + 1) * API_PAGE >= apiReq.total" @click="apiReqNext">下一页</button>
        </div>
      </div>
    </div>
    <div class="page-body" v-else style="text-align:center;padding:60px;color:var(--text-muted)">加载中...</div>
  </div>
</template>
