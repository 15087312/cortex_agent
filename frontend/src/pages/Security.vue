<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const state = ref({})
const logs = ref([])
const labels = { 'L0': '基础校验', 'L1': '内容审核', 'L2': '输出审查', 'L3': '工具安全', 'L4': '执行保护' }

onMounted(loadData)
async function loadData() { try { const [sr, ar] = await Promise.all([endpoints.securityStatus().catch(() => null), endpoints.securityAudit(50).catch(() => null)]); state.value = sr?.data?.state || {}; logs.value = ar?.data?.logs || [] } catch {} }
async function handleToggle(lv, en) { try { await endpoints.setSecuritySwitch(lv, en); toast.show(`${lv}已${en?'开启':'关闭'}`, 'success'); loadData() } catch { toast.show('切换失败', 'error') } }
function passed(l) { return l.passed || l.result === true || l.result === '通过' }
function actionOf(l) { return l.action || l.type || l.event_type || '' }
function contentOf(l) { return (l.content || l.message || l.input || l.content_preview || '').slice(0, 80) }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>安全审计</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="card"><div class="card-header">安全开关</div>
        <div v-if="Object.keys(state).length === 0"><div class="empty-state" style="padding:40px"><span class="empty-icon"><Icon name="circle" :size="20" /></span><p class="empty-text">安全策略加载后将显示于此</p></div></div>
        <div v-else style="display:flex;flex-wrap:wrap;gap:12px">
            <div v-for="(en, lv) in state" :key="lv" style="display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--bg-tertiary);border-radius:var(--radius-md)">
              <span style="font-size:13px;font-weight:600;min-width:72px">{{ labels[lv] || lv }}</span>
              <label class="toggle-switch">
                <input type="checkbox" :checked="en" @change="handleToggle(lv, !en)" />
                <span class="toggle-slider"></span>
              </label>
            </div>
          </div>
      </div>
      <div class="card" style="margin-top:12px"><div class="card-header">审计日志</div>
        <table class="data-table" v-if="logs.length > 0"><thead><tr><th>时间</th><th>操作</th><th>内容</th><th>结果</th></tr></thead><tbody><tr v-for="l in logs" :key="l.id || l.timestamp"><td>{{ formatTime(l.timestamp || l.time) }}</td><td>{{ actionOf(l) }}</td><td><span class="mem-content-ellipsis">{{ contentOf(l) }}</span></td><td><span class="badge" :class="passed(l)?'badge-green':'badge-red'">{{ passed(l)?'通过':'拦截' }}</span></td></tr></tbody></table>
        <div v-else class="empty-state" style="padding:40px"><span class="empty-icon"><Icon name="list" :size="20" /></span><p class="empty-text">审计日志将在此显示</p></div>
      </div>
    </div>
  </div>
</template>
