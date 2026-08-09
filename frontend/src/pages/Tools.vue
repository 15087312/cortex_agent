<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const props = defineProps({ compact: { type: Boolean, default: false } })
const tools = ref([])
const events = ref([])
const bySource = ref(0)
const query = ref('')
const selected = ref(null)
const toolInfo = ref(null)
const toolResult = ref(null)
const infoLoading = ref(false)

// 简单参数表单（primitive），复杂参数用 JSON
const formFields = ref([])
const formValues = ref({})
const showJson = ref(false)

async function loadData() {
  try {
    const [tr, er] = await Promise.all([endpoints.tools().catch(() => null), endpoints.toolEvents(20).catch(() => null)])
    const rawTools = tr?.data?.tools || {}
    tools.value = Array.isArray(rawTools)
      ? rawTools.map(t => (typeof t === 'string' ? { name: t } : t))
      : Object.keys(rawTools).map(n => ({ name: n, description: (rawTools[n] && typeof rawTools[n] === 'object' && rawTools[n].description) || '' }))
    bySource.value = tr?.data?.by_source ? Object.keys(tr.data.by_source).length : 0
    events.value = er?.data?.events || []
  } catch {}
}

const filteredTools = computed(() => {
  const q = query.value.toLowerCase()
  if (!q) return tools.value
  return tools.value.filter(t => (t.name || '').toLowerCase().includes(q))
})

async function handleSelect(name) {
  selected.value = name
  toolInfo.value = null
  toolResult.value = null
  formFields.value = []
  formValues.value = {}
  showJson.value = false
  infoLoading.value = true
  try {
    const r = await endpoints.toolInfo(name)
    toolInfo.value = r.data || r
    // 尽力解析参数定义：spec.params / params / parameters（对象或数组）
    const spec = toolInfo.value?.spec || toolInfo.value?.tool || {}
    const rawParams = spec.params || spec.parameters || toolInfo.value?.params || {}
    formFields.value = buildFields(rawParams)
  } catch {
    toolInfo.value = null
  } finally {
    infoLoading.value = false
  }
}

function buildFields(rawParams) {
  if (!rawParams || typeof rawParams !== 'object') return []
  const fields = []
  const entries = Array.isArray(rawParams) ? rawParams.map((p, i) => [String(i), p]) : Object.entries(rawParams)
  for (const [key, def] of entries) {
    const d = (def && typeof def === 'object') ? def : { description: String(def) }
    const type = d.type || d.kind || 'string'
    const ft = ['integer', 'number'].includes(type) ? 'number' : ['boolean'].includes(type) ? 'boolean' : 'string'
    fields.push({ key, type: ft, required: !!d.required, description: d.description || '' })
  }
  return fields
}

function buildParamsFromForm() {
  const params = {}
  for (const f of formFields.value) {
    const v = formValues.value[f.key]
    if (v === '' || v == null) continue
    if (f.type === 'number') params[f.key] = Number(v)
    else if (f.type === 'boolean') params[f.key] = v
    else params[f.key] = String(v)
  }
  return params
}

async function handleCall() {
  let params = {}
  try {
    params = showJson.value ? JSON.parse(jsonText.value) : buildParamsFromForm()
  } catch { toast.show('参数格式错误', 'error'); return }
  try { const r = await endpoints.callTool(selected.value, params); toolResult.value = JSON.stringify(r.data, null, 2) } catch (e) { toolResult.value = '错误: ' + (e.body?.error?.message || e.status) }
}

const jsonText = ref('{}')

let timer = null
onMounted(() => { loadData(); timer = setInterval(loadData, 30000) })
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-header" v-if="!compact">
      <h2>工具管理</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid" style="grid-template-columns:repeat(3,1fr)">
        <div class="stat-card"><div class="stat-icon stat-icon-blue"><Icon name="wrench" :size="18" /></div><div class="stat-value">{{ tools.length }}</div><div class="stat-label">总工具</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-purple"><Icon name="layers" :size="18" /></div><div class="stat-value">{{ bySource }}</div><div class="stat-label">来源分类</div></div>
        <div class="stat-card"><div class="stat-icon stat-icon-green"><Icon name="activity" :size="18" /></div><div class="stat-value">{{ events.length }}</div><div class="stat-label">最近调用</div></div>
      </div>

      <div class="dash-grid">
        <div class="card card-scroll">
          <div class="card-header">工具列表 ({{ tools.length }})</div>
          <div class="tool-search"><input class="input" v-model="query" placeholder="搜索工具..." style="width:100%" /></div>
          <div v-if="filteredTools.length === 0" class="empty-state tool-empty"><p class="empty-text">工具注册后自动出现于此</p></div>
          <div v-else v-for="t in filteredTools" :key="t.name" class="tool-item" :class="{ selected: selected === t.name }" @click="handleSelect(t.name)">
            <div class="tool-name">{{ t.name }}</div>
            <div v-if="t.description" class="tool-desc">{{ t.description }}</div>
          </div>
        </div>
        <div class="card card-scroll">
          <div class="card-header">工具详情</div>
          <div v-if="!selected" class="empty-state tool-empty"><p class="empty-text">选择一个工具查看详情并调用</p></div>
          <div v-else class="tool-detail">
            <div v-if="infoLoading" class="tool-loading">加载中...</div>
            <template v-else>
              <div class="detail-row"><span class="detail-label">描述</span>{{ toolInfo?.description || toolInfo?.name || '-' }}</div>
              <div class="detail-row"><span class="detail-label">来源</span>{{ toolInfo?.source || 'builtin' }}</div>

              <div class="tool-call-section">
                <strong>调用工具</strong>
                <button v-if="formFields.length" class="btn btn-sm" @click="showJson = !showJson">{{ showJson ? '表单模式' : 'JSON 模式' }}</button>
              </div>

              <!-- 参数表单（简单参数） -->
              <div v-if="formFields.length && !showJson" class="tool-form-mt">
                <div v-for="f in formFields" :key="f.key" class="tool-param-row">
                  <span class="detail-label">{{ f.key }}<template v-if="f.required"> *</template></span>
                  <input v-if="f.type === 'number'" class="input" type="number" v-model="formValues[f.key]" style="width:100%;max-width:240px" />
                  <label v-else-if="f.type === 'boolean'" class="toggle-switch tool-toggle"><input type="checkbox" v-model="formValues[f.key]" /><span class="toggle-slider"></span></label>
                  <input v-else class="input" v-model="formValues[f.key]" style="width:100%;max-width:240px" />
                </div>
              </div>
              <!-- JSON 参数（复杂/无 schema） -->
              <textarea v-else class="input tool-json-textarea" v-model="jsonText" placeholder='{"param": "value"}'></textarea>

              <div class="tool-exec-btn"><button class="btn btn-sm" @click="handleCall"><Icon name="play" :size="14" /> 执行</button></div>
              <pre v-if="toolResult" class="json-output">{{ toolResult }}</pre>
            </template>
          </div>
        </div>
      </div>

      <div class="card dash-mt"><div class="card-header">调用历史 ({{ events.length }})</div>
        <table class="data-table" v-if="events.length > 0"><thead><tr><th>工具</th><th>时间</th></tr></thead><tbody><tr v-for="e in events" :key="e.id || e.timestamp"><td>{{ e.tool_name || e.name || '' }}</td><td>{{ formatTime(e.timestamp || e.time) }}</td></tr></tbody></table>
        <div v-else class="empty-state tool-empty"><p class="empty-text">工具调用记录将显示在此</p></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stat-icon-blue { background: rgba(88,166,255,.15); color: #58a6ff; }
.stat-icon-purple { background: rgba(163,113,247,.15); color: #a371f7; }
.stat-icon-green { background: rgba(63,185,80,.15); color: var(--success); }
.dash-grid { display: grid; grid-template-columns: 1fr 1.2fr; gap: 12px; margin-top: 12px; }
.card-scroll { max-height: 400px; overflow-y: auto; }
.tool-search { padding: 8px 12px; }
.tool-desc { font-size: 12px; color: var(--text-muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-name { font-weight: 600; }
.tool-loading { padding: 12px; color: var(--text-muted); }
.tool-call-section { margin-top: 12px; display: flex; align-items: center; gap: 8px; }
.tool-form-mt { margin-top: 8px; }
.tool-toggle { margin: 0; }
.tool-json-textarea { width: 100%; min-height: 70px; font-family: var(--font-mono); margin-top: 8px; }
.tool-exec-btn { margin-top: 8px; }
.tool-empty { padding: 32px; }
.dash-mt { margin-top: 12px; }
</style>
