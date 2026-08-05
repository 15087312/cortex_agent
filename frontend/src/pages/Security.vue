<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const state = ref({})
const logs = ref([])
const labels = { 'L0': '基础校验', 'L1': '内容审核', 'L2': '输出审查', 'L3': '工具安全', 'L4': '执行保护' }
const levelOrder = ['L0', 'L1', 'L2', 'L3', 'L4']

const enabledCount = computed(() => levelOrder.filter((l) => state.value[l]).length)
const totalLogs = computed(() => logs.value.length)
const blocked = computed(() => logs.value.filter((l) => !passed(l)).length)

async function loadData() {
  try {
    const [sr, ar] = await Promise.all([endpoints.securityStatus().catch(() => null), endpoints.securityAudit(50).catch(() => null)])
    state.value = sr?.data?.state || {}
    logs.value = ar?.data?.logs || []
  } catch {}
}
async function handleToggle(lv, en) {
  try { await endpoints.setSecuritySwitch(lv, en); toast.show(`${labels[lv] || lv}已${en ? '开启' : '关闭'}`, 'success'); loadData() }
  catch { toast.show('切换失败', 'error') }
}
function passed(l) { return l.passed || l.result === true || l.result === '通过' }
function actionOf(l) { return l.action || l.type || l.event_type || '' }
function contentOf(l) { return (l.content || l.message || l.input || l.content_preview || '').slice(0, 80) }

let timer = null
onMounted(() => { loadData(); timer = setInterval(loadData, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-header">
      <h2>安全审计</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid" style="grid-template-columns:repeat(3,1fr)">
        <div class="stat-card"><div class="stat-icon" style="background:rgba(63,185,80,.15);color:#3fb950"><Icon name="shield" :size="18" /></div><div class="stat-value">{{ enabledCount }}/5</div><div class="stat-label">已开启防护</div></div>
        <div class="stat-card"><div class="stat-icon" style="background:rgba(88,166,255,.15);color:#58a6ff"><Icon name="list" :size="18" /></div><div class="stat-value">{{ totalLogs }}</div><div class="stat-label">审计记录</div></div>
        <div class="stat-card"><div class="stat-icon" style="background:rgba(248,81,73,.15);color:#f85149"><Icon name="alert" :size="18" /></div><div class="stat-value">{{ blocked }}</div><div class="stat-label">拦截</div></div>
      </div>

      <div class="card" style="margin-top:12px">
        <div class="card-header">安全防护开关</div>
        <div v-if="Object.keys(state).length === 0" class="empty-state" style="padding:32px"><p class="empty-text">安全策略加载后将显示于此</p></div>
        <div v-else style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px">
          <div v-for="lv in levelOrder" :key="lv" class="pipeline-card" :class="{ ok: !!state[lv] }">
            <span style="font-weight:600">{{ labels[lv] || lv }}</span>
            <span style="flex:1"></span>
            <label class="toggle-switch"><input type="checkbox" :checked="!!state[lv]" @change="handleToggle(lv, !state[lv])" /><span class="toggle-slider"></span></label>
          </div>
        </div>
      </div>

      <div class="card" style="margin-top:12px">
        <div class="card-header">审计日志</div>
        <table class="data-table" v-if="logs.length > 0">
          <thead><tr><th>时间</th><th>操作</th><th>内容</th><th>结果</th></tr></thead>
          <tbody><tr v-for="l in logs" :key="l.id || l.timestamp">
            <td>{{ formatTime(l.timestamp || l.time) }}</td>
            <td>{{ actionOf(l) }}</td>
            <td><span class="mem-content-ellipsis">{{ contentOf(l) }}</span></td>
            <td><span class="badge" :class="passed(l) ? 'badge-green' : 'badge-red'">{{ passed(l) ? '通过' : '拦截' }}</span></td>
          </tr></tbody>
        </table>
        <div v-else class="empty-state" style="padding:32px"><p class="empty-text">审计日志将在此显示</p></div>
      </div>
    </div>
  </div>
</template>
