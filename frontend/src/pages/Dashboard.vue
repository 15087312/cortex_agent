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
    const [d, health, libsR, sess, perc] = await Promise.all([
      endpoints.dashboard().catch(() => null),
      endpoints.health().catch(() => null),
      endpoints.memoryLibs().catch(() => null),
      endpoints.sessions().catch(() => null),
      endpoints.perceptionStatus().catch(() => null),
    ])
    dash.value = d?.data || null
    apiStatus.value = health?.data?.status === 'healthy' ? '健康' : health?.data?.status || '-'
    libs.value = libsR?.data?.libs || []
    sessions.value = sess?.data || []
    perception.value = perc?.data || null
  } catch {} finally { loading.value = false }
}

let timer = null
onMounted(async () => { await loadData(); timer = setInterval(loadData, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
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
        <div class="health-card" @click="router.push('/sessions')" title="点击查看会话">
          <div class="health-ring">
            <svg viewBox="0 0 72 72"><circle class="ring-bg" cx="36" cy="36" r="30"/><circle class="ring-fill" cx="36" cy="36" r="30" :style="{ strokeDasharray: 188.5, strokeDashoffset: 188.5 }"/></svg>
            <div class="ring-label">{{ totalSessions }}</div>
          </div>
          <div class="health-name">会话</div>
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
    </div>
    <div class="page-body" v-else style="text-align:center;padding:60px;color:var(--text-muted)">加载中...</div>
  </div>
</template>
