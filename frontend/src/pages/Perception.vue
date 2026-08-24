<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useI18n } from 'vue-i18n'
import Icon from '@/components/Icon.vue'

const { t } = useI18n()
const toast = useToastStore()
const status = ref({})
let _interval = null

const running = computed(() => status.value.status === 'running')
const ws = computed(() => status.value.world_state || {})
const detectors = computed(() => {
  const p = status.value.pipeline || {}
  return Object.entries(p).map(([step, st]) => ({ step, ok: st === 'ok' || st === true || st === 'healthy' }))
})

onMounted(async () => { await refresh(); _interval = setInterval(refresh, 10000) })
onUnmounted(() => { if (_interval) clearInterval(_interval) })
async function refresh() { try { const r = await endpoints.perceptionStatus(); status.value = r.data || {} } catch {} }
async function start() { try { await endpoints.startPerception(); toast.show(t('perception.started'), 'success'); refresh() } catch { toast.show(t('perception.startFailed'), 'error') } }
async function stop() { try { await endpoints.stopPerception(); toast.show(t('perception.stopped'), 'success'); refresh() } catch { toast.show(t('perception.stopFailed'), 'error') } }
</script>

<template>
  <div>
    <div class="page-header"><h2>{{ $t('perception.title') }}</h2></div>
    <div class="page-body">
      <!-- 运行状态 + 控制 -->
      <div class="stat-grid stat-grid-4">
        <div class="stat-card" :class="{ 'perception-running': running }"><div class="stat-icon"><Icon :name="running ? 'circle' : 'stop'" :size="18" /></div><div class="stat-value">{{ running ? $t('perception.running') : $t('perception.idle') }}</div><div class="stat-label">{{ $t('common.status') }}</div></div>
        <div class="stat-card"><div class="stat-icon"><Icon name="monitor" :size="18" /></div><div class="stat-value">{{ status.platform || $t('perception.detecting') }}</div><div class="stat-label">{{ $t('perception.platform') }}</div></div>
        <div class="stat-card"><div class="stat-icon"><Icon name="mic" :size="18" /></div><div class="stat-value">{{ status.voice_available ? $t('common.available') : $t('common.unavailable') }}</div><div class="stat-label">{{ $t('perception.voice') }}</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-yellow"><Icon name="clock" :size="18" /></div><div class="stat-value">{{ ws.recent_events_count ?? 0 }}</div><div class="stat-label">{{ $t('perception.recentEvents') }}</div></div>
      </div>

      <div class="perception-toolbar">
        <button class="btn btn-primary btn-sm" @click="start"><Icon name="play" :size="14" /> {{ $t('perception.start') }}</button>
        <button class="btn btn-sm" @click="stop"><Icon name="stop" :size="14" /> {{ $t('perception.stop') }}</button>
      </div>

      <!-- 当前环境（AI 在看什么） -->
      <div class="card dash-mt">
        <div class="card-header">{{ $t('perception.currentEnv') }}</div>
        <div class="setting-row"><div class="lbl"><div class="t">{{ $t('perception.currentApp') }}</div></div><div class="setting-ctl"><b>{{ ws.active_app || '—' }}</b></div></div>
        <div class="setting-row"><div class="lbl"><div class="t">{{ $t('perception.currentWindow') }}</div></div><div class="setting-ctl"><span class="perception-window">{{ ws.active_window || '—' }}</span></div></div>
        <div class="setting-row" v-if="ws.screen_text"><div class="lbl"><div class="t">{{ $t('perception.screenContent') }}</div></div><div class="setting-ctl"><span class="perception-screen">{{ ws.screen_text }}</span></div></div>
        <div class="setting-row"><div class="lbl"><div class="t">{{ $t('perception.stats') }}</div></div><div class="setting-ctl"><span class="perception-stats">{{ $t('perception.ocr') }} {{ ws.recent_ocr_count ?? 0 }} · {{ $t('perception.uiElements') }} {{ ws.ui_elements_count ?? 0 }}</span></div></div>
      </div>

      <div class="card dash-mt">
        <div class="card-header">{{ $t('perception.detectors') }} ({{ detectors.length }})</div>
        <div v-if="detectors.length === 0" class="empty-state perception-empty"><p class="empty-text">{{ $t('perception.pipelineEmpty') }}</p></div>
        <div class="detector-grid">
          <div v-for="d in detectors" :key="d.step" class="pipeline-card" :class="{ ok: d.ok }">
            <div class="step-dot" :class="{ done: d.ok }"></div>
            <span>{{ d.step }}</span>
            <span class="badge" :class="d.ok ? 'badge-green' : 'badge-red'">{{ d.ok ? $t('perception.normal') : $t('perception.abnormal') }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-grid-4 { grid-template-columns: repeat(4, 1fr); }
.stat-icon-yellow { color: var(--warning); }
.perception-toolbar { display: flex; gap: 8px; margin-top: 12px; }
.dash-mt { margin-top: 12px; }
.perception-window { color: var(--text-muted); word-break: break-all; text-align: right; }
.perception-screen { color: var(--text-muted); font-size: 12px; white-space: pre-wrap; word-break: break-all; text-align: right; max-width: 70%; }
.perception-stats { color: var(--text-muted); }
.perception-empty { padding: 24px; }
.detector-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 10px; }
</style>
