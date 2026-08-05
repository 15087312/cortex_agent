<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

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
async function start() { try { await endpoints.startPerception(); toast.show('已启动', 'success'); refresh() } catch { toast.show('启动失败', 'error') } }
async function stop() { try { await endpoints.stopPerception(); toast.show('已停止', 'success'); refresh() } catch { toast.show('停止失败', 'error') } }
</script>

<template>
  <div>
    <div class="page-header"><h2>感知系统</h2></div>
    <div class="page-body">
      <!-- 运行状态 + 控制 -->
      <div class="stat-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat-card" :class="{ 'perception-running': running }"><div class="stat-icon"><Icon :name="running ? 'circle' : 'stop'" :size="18" /></div><div class="stat-value">{{ running ? '运行中' : '待启动' }}</div><div class="stat-label">状态</div></div>
        <div class="stat-card"><div class="stat-icon"><Icon name="monitor" :size="18" /></div><div class="stat-value">{{ status.platform || '检测中...' }}</div><div class="stat-label">平台</div></div>
        <div class="stat-card"><div class="stat-icon"><Icon name="mic" :size="18" /></div><div class="stat-value">{{ status.voice_available ? '可用' : '不可用' }}</div><div class="stat-label">语音</div></div>
        <div class="stat-card"><div class="stat-icon" style="color:#d29922"><Icon name="clock" :size="18" /></div><div class="stat-value">{{ ws.recent_events_count ?? 0 }}</div><div class="stat-label">最近事件</div></div>
      </div>

      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn btn-primary btn-sm" @click="start"><Icon name="play" :size="14" /> 启动</button>
        <button class="btn btn-sm" @click="stop"><Icon name="stop" :size="14" /> 停止</button>
      </div>

      <!-- 当前环境（AI 在看什么） -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">当前环境</div>
        <div class="setting-row"><div class="lbl"><div class="t">当前应用</div></div><div class="setting-ctl"><b>{{ ws.active_app || '—' }}</b></div></div>
        <div class="setting-row"><div class="lbl"><div class="t">当前窗口</div></div><div class="setting-ctl"><span style="color:var(--text-muted);word-break:break-all;text-align:right">{{ ws.active_window || '—' }}</span></div></div>
        <div class="setting-row" v-if="ws.screen_text"><div class="lbl"><div class="t">屏幕内容</div></div><div class="setting-ctl"><span style="color:var(--text-muted);font-size:12px;white-space:pre-wrap;word-break:break-all;text-align:right;max-width:70%">{{ ws.screen_text }}</span></div></div>
        <div class="setting-row"><div class="lbl"><div class="t">感知统计</div></div><div class="setting-ctl"><span style="color:var(--text-muted)">OCR {{ ws.recent_ocr_count ?? 0 }} · UI 元素 {{ ws.ui_elements_count ?? 0 }}</span></div></div>
      </div>

      <!-- 检测器流水线 -->
      <div class="card" style="margin-top:12px">
        <div class="card-header">检测器 ({{ detectors.length }})</div>
        <div v-if="detectors.length === 0" class="empty-state" style="padding:24px"><p class="empty-text">流水线信息将在此显示</p></div>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px">
          <div v-for="d in detectors" :key="d.step" class="pipeline-card" :class="{ ok: d.ok }">
            <div class="step-dot" :class="{ done: d.ok }"></div>
            <span>{{ d.step }}</span>
            <span class="badge" :class="d.ok ? 'badge-green' : 'badge-red'">{{ d.ok ? '正常' : '异常' }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
