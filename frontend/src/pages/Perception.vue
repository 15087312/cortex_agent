<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const status = ref({})
let _interval = null

onMounted(async () => { await refresh(); _interval = setInterval(refresh, 10000) })
onUnmounted(() => { if (_interval) clearInterval(_interval) })
async function refresh() { try { const r = await endpoints.perceptionStatus(); status.value = r.data || {} } catch {} }
async function start() { try { await endpoints.startPerception(); toast.show('已启动', 'success'); refresh() } catch { toast.show('启动失败', 'error') } }
async function stop() { try { await endpoints.stopPerception(); toast.show('已停止', 'success'); refresh() } catch { toast.show('停止失败', 'error') } }
function pipeBadgeClass(st) { return st === 'ok' || st === true || st === 'healthy' ? 'badge-green' : 'badge-red' }
function pipeStatusLabel(st) { return st === 'ok' || st === true || st === 'healthy' ? '正常' : '异常' }
</script>

<template>
  <div>
    <div class="page-header">      <h2>感知系统</h2></div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card" :class="{ 'perception-running': status.status === 'running' }"><div class="stat-icon"><Icon :name="status.status === 'running' ? 'circle' : 'stop'" :size="18" /></div><div class="stat-value">{{ status.status === 'running' ? '运行中' : '待启动' }}</div><div class="stat-label">状态</div></div>
        <div class="stat-card"><div class="stat-icon"><Icon name="monitor" :size="18" /></div><div class="stat-value">{{ status.platform || '检测中...' }}</div><div class="stat-label">平台</div></div>
        <div class="stat-card"><div class="stat-icon"><Icon name="mic" :size="18" /></div><div class="stat-value">{{ status.voice_available ? '可用' : '不可用' }}</div><div class="stat-label">语音</div></div>
      </div>
      <div class="card" style="margin-top:12px"><div class="card-header">控制</div><div style="display:flex;gap:8px"><button class="btn btn-primary btn-sm" @click="start"><Icon name="play" :size="14" /> 启动</button><button class="btn btn-sm" @click="stop"><Icon name="stop" :size="14" /> 停止</button></div></div>
      <div v-if="status.pipeline && Object.keys(status.pipeline).length > 0" class="card" style="margin-top:12px"><div class="card-header">流水线</div>
        <div class="pipeline-list">
          <div class="pipeline-row" v-for="(st, step) in status.pipeline" :key="step">
            <span class="step-dot" :class="{ done: pipeBadgeClass(st) === 'badge-green' }"></span>
            <span class="step-name">{{ step }}</span>
            <span class="step-status"><span class="badge" :class="pipeBadgeClass(st)">{{ pipeStatusLabel(st) }}</span></span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
