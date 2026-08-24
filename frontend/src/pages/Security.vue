<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import { useI18n } from 'vue-i18n'
import Icon from '@/components/Icon.vue'

const { t } = useI18n()
const toast = useToastStore()
const state = ref({})
const logs = ref([])
const labels = { L0: 'level0', L1: 'level1', L2: 'level2', L3: 'level3', L4: 'level4' }
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
  try { await endpoints.setSecuritySwitch(lv, en); toast.show(t('security.toggleState', { level: t('security.' + (labels[lv] || lv)), state: t(en ? 'common.enabled' : 'common.disabled') }), 'success'); loadData() }
  catch { toast.show(t('security.switchFailed'), 'error') }
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
      <h2>{{ $t('security.title') }}</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body">
      <div class="stat-grid stat-grid-3">
        <div class="stat-card"><div class="stat-icon stat-icon-green"><Icon name="shield" :size="18" /></div><div class="stat-value">{{ enabledCount }}/5</div><div class="stat-label">{{ $t('security.enabledProtection') }}</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-blue"><Icon name="list" :size="18" /></div><div class="stat-value">{{ totalLogs }}</div><div class="stat-label">{{ $t('security.auditRecords') }}</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-red"><Icon name="alert" :size="18" /></div><div class="stat-value">{{ blocked }}</div><div class="stat-label">{{ $t('security.blocked') }}</div></div>
      </div>

      <div class="card dash-mt">
        <div class="card-header">{{ $t('security.protectionSwitches') }}</div>
        <div v-if="Object.keys(state).length === 0" class="empty-state security-empty"><p class="empty-text">{{ $t('security.emptyState') }}</p></div>
        <div v-else class="security-grid">
          <div v-for="lv in levelOrder" :key="lv" class="pipeline-card" :class="{ ok: !!state[lv] }">
            <span class="security-label">{{ $t('security.' + (labels[lv] || lv)) }}</span>
            <span class="security-spacer"></span>
            <label class="toggle-switch"><input type="checkbox" :checked="!!state[lv]" @change="handleToggle(lv, !state[lv])" /><span class="toggle-slider"></span></label>
          </div>
        </div>
      </div>

      <div class="card dash-mt">
        <div class="card-header">{{ $t('security.auditLogs') }}</div>
        <table class="data-table" v-if="logs.length > 0">
          <thead><tr><th>{{ $t('common.time') }}</th><th>{{ $t('common.action') }}</th><th>{{ $t('security.content') }}</th><th>{{ $t('security.result') }}</th></tr></thead>
          <tbody><tr v-for="l in logs" :key="l.id || l.timestamp">
            <td>{{ formatTime(l.timestamp || l.time) }}</td>
            <td>{{ actionOf(l) }}</td>
            <td><span class="mem-content-ellipsis">{{ contentOf(l) }}</span></td>
            <td><span class="badge" :class="passed(l) ? 'badge-green' : 'badge-red'">{{ passed(l) ? $t('security.passed') : $t('security.blocked') }}</span></td>
          </tr></tbody>
        </table>
        <div v-else class="empty-state security-empty"><p class="empty-text">{{ $t('security.auditLogsEmpty') }}</p></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-icon-green { background: rgba(63,185,80,.15); color: var(--success); }
.stat-icon-blue { background: rgba(88,166,255,.15); color: #58a6ff; }
.stat-icon-red { background: rgba(248,81,73,.15); color: #f85149; }
.dash-mt { margin-top: 12px; }
.security-empty { padding: 32px; }
.security-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 10px; }
.security-label { font-weight: 600; }
.security-spacer { flex: 1; }
.stat-grid-3 { grid-template-columns: repeat(3, 1fr); }
</style>
