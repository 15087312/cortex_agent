<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm } from '@/composables/useDialog.js'
import Icon from '@/components/Icon.vue'
import SkillsView from '@/pages/Skills.vue'
import GraphView from '@/pages/Graph.vue'
import ToolsView from '@/pages/Tools.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const toast = useToastStore()
const confirm = useConfirm()
const agents = ref([])
const tools = ref([])
const drafts = ref({})
const overrides = ref({})
const toolsCfg = ref({})
const modelParams = ref({})
const activeTab = ref('agents')
const saving = ref('')
const loading = ref(true)
const expanded = ref({})
const preview = ref({ role: '', text: '', loading: false })
const promptPreview = ref({ open: false, agentName: '', text: '', loading: false })

const TIER_LABEL = { large: 'orchestration.tiers.large', supervisor: 'orchestration.tiers.supervisor', expert: 'orchestration.tiers.expert' }

// AI 工具管理
const aiTools = ref([])
const showAiForm = ref(false)
const editingAi = ref(null)
const aiForm = ref({ tool_name: '', description: '', code: '', params: '' })
// per-role 工具权限集中配置（工具管理 tab）
const roleToolSel = ref('')
const roleToolCfg = ref({ whitelist: [], blacklist: [] })
const srcModal = ref({ open: false, name: '', source: '', editable: false })

const grouped = computed(() => {
  const g = { large: [], supervisor: [], expert: [] }
  agents.value.forEach((a) => { (g[a.tier] || (g[a.tier] = [])).push(a) })
  return g
})

async function loadData() {
  loading.value = true
  try {
    const [o, t, ai] = await Promise.all([
      fetch('/api/management/orchestration', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
      endpoints.tools().catch(() => null),
      fetch('/api/tools/ai', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
    ])
    agents.value = o?.data?.agents || []
    const toolsData = t?.data
    const toolsObj = toolsData?.tools || {}
    tools.value = Array.isArray(toolsObj)
      ? toolsObj
      : Object.keys(toolsObj).map((n) => ({ name: n, ...(typeof toolsObj[n] === 'object' ? toolsObj[n] : {}) }))
    aiTools.value = ai?.data?.tools || {}
    agents.value.forEach((a) => {
      drafts.value[a.role] = a.custom_persona || ''
      overrides.value[a.role] = a.system_override || ''
      toolsCfg.value[a.role] = { whitelist: [...(a.role_tools?.whitelist || [])], blacklist: [...(a.role_tools?.blacklist || [])] }
      modelParams.value[a.role] = { temperature: a.model_params?.temperature ?? '', max_tokens: a.model_params?.max_tokens ?? '', reasoning_effort: a.model_params?.reasoning_effort ?? '' }
    })
  } catch {} finally { loading.value = false }
}

async function savePersona(agent) {
  saving.value = agent.role
  try {
    const r = await fetch('/api/config/persona/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: drafts.value[agent.role] || '' }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('orchestration.personaSaved', { name: agent.name }), 'success')
    else toast.show(t('orchestration.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.saveFailed'), 'error') }
  finally { saving.value = '' }
}

async function saveOverride(agent) {
  saving.value = 'ov_' + agent.role
  try {
    const r = await fetch('/api/config/persona/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: drafts.value[agent.role] || '', system_override: overrides.value[agent.role] || '' }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('orchestration.overrideSaved', { name: agent.name }), 'success')
    else toast.show(t('orchestration.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.saveFailed'), 'error') }
  finally { saving.value = '' }
}

async function saveRoleTools(agent) {
  saving.value = 'tools_' + agent.role
  try {
    const r = await fetch('/api/config/tools/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tools: toolsCfg.value[agent.role] }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('orchestration.toolsSaved', { name: agent.name }), 'success')
    else toast.show(t('orchestration.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.saveFailed'), 'error') }
  finally { saving.value = '' }
}

async function saveModelParams(agent) {
  saving.value = 'mp_' + agent.role
  const p = modelParams.value[agent.role]
  const body = {}
  if (p.temperature !== '') body.temperature = Number(p.temperature)
  if (p.max_tokens !== '') body.max_tokens = Number(p.max_tokens)
  if (p.reasoning_effort !== '') body.reasoning_effort = p.reasoning_effort
  try {
    const r = await fetch('/api/config/model-params/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ params: body }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('orchestration.paramsSaved', { name: agent.name }), 'success')
    else toast.show(t('orchestration.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.saveFailed'), 'error') }
  finally { saving.value = '' }
}

async function previewPrompt(agent) {
  promptPreview.value = { open: true, agentName: agent.name, text: '', loading: true }
  try {
    const r = await fetch('/api/management/orchestration/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: agent.role, tier: agent.tier }),
    })
    const d = await r.json()
    promptPreview.value.text = d?.data?.prompt || ''
  } catch { promptPreview.value.text = t('orchestration.fetchFailed') } finally { promptPreview.value.loading = false }
}

async function toggleTool(tool) {
  const next = !tool.enabled
  try {
    const r = await fetch('/api/tools/enabled/' + encodeURIComponent(tool.name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    const d = await r.json()
    if (d.success) tool.enabled = next
    else toast.show(t('orchestration.opFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.opFailed'), 'error') }
}

async function toggleAgentActive(agent) {
  const next = !agent.active
  try {
    const r = await fetch('/api/management/orchestration/agents/' + encodeURIComponent(agent.role) + '/active', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: next }),
    })
    const d = await r.json()
    if (d.success) {
      // 重新加载数据以同步同层其他 agent 状态（总指挥层互斥）
      await loadData()
    } else toast.show(t('orchestration.opFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.opFailed'), 'error') }
}

// ── per-role 工具权限集中配置 ──
function onRoleSel() {
  const agent = agents.value.find((a) => a.role === roleToolSel.value)
  roleToolCfg.value = {
    whitelist: [...(agent?.role_tools?.whitelist || [])],
    blacklist: [...(agent?.role_tools?.blacklist || [])],
  }
}
async function saveRoleToolsSel() {
  if (!roleToolSel.value) return
  saving.value = 'role_tools'
  try {
    const r = await fetch('/api/config/tools/' + encodeURIComponent(roleToolSel.value), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tools: roleToolCfg.value }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('orchestration.roleToolsSaved'), 'success')
    else toast.show(t('orchestration.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.saveFailed'), 'error') }
  finally { saving.value = '' }
}
function toggleRoleTool(key, name) {
  const list = roleToolCfg.value[key]
  const i = list.indexOf(name)
  if (i >= 0) list.splice(i, 1)
  else list.push(name)
}
function toggleRoleAll(key) {
  const list = roleToolCfg.value[key]
  const i = list.indexOf('*')
  if (i >= 0) list.splice(i, 1)
  else list.push('*')
}

// ── 工具源码查看/编辑 ──
async function viewSource(tool) {
  const name = typeof tool === 'string' ? tool : tool?.name
  if (!name) return
  srcModal.value = { open: true, name, source: t('common.loading'), editable: false }
  try {
    const r = await fetch('/api/tools/source/' + encodeURIComponent(name), { headers: { Accept: 'application/json' } })
    const d = await r.json()
    srcModal.value.source = d?.data?.source || t('orchestration.noSource')
    srcModal.value.editable = !!d?.data?.editable
  } catch { srcModal.value.source = t('orchestration.sourceFailed') }
}
function editAiFromSource() {
  const name = srcModal.value.name
  const info = aiTools.value[name] || {}
  editingAi.value = name
  aiForm.value = { tool_name: name, description: info.description || '', code: info.code || '', params: '' }
  showAiForm.value = true
}

function clearCustom(agent) {
  drafts.value[agent.role] = ''
  savePersona(agent)
}

function clearOverride(agent) {
  overrides.value[agent.role] = ''
  saveOverride(agent)
}

function toggleToolList(agent, key, name) {
  const list = toolsCfg.value[agent.role][key]
  const i = list.indexOf(name)
  if (i >= 0) list.splice(i, 1)
  else list.push(name)
}

function hasAll(agent, key) {
  const list = toolsCfg.value[agent.role][key]
  return list.includes('*')
}

function toggleAll(agent, key) {
  const list = toolsCfg.value[agent.role][key]
  const idx = list.indexOf('*')
  if (idx >= 0) list.splice(idx, 1)
  else list.push('*')
}

// ── AI 工具管理 ──

function openAiForm(tool) {
  if (tool) {
    editingAi.value = tool
    aiForm.value = { tool_name: tool, description: aiTools.value[tool]?.description || '', code: aiTools.value[tool]?.code || '', params: '' }
  } else {
    editingAi.value = null
    aiForm.value = { tool_name: '', description: '', code: '', params: '' }
  }
  showAiForm.value = true
}

async function submitAiForm() {
  const body = { tool_name: aiForm.value.tool_name, description: aiForm.value.description, code: aiForm.value.code, params: aiForm.value.params }
  try {
    const r = await fetch('/api/tools/ai' + (editingAi.value ? '/' + encodeURIComponent(editingAi.value) : ''), {
      method: editingAi.value ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await r.json()
    if (d.success) {
      toast.show(editingAi.value ? t('orchestration.toolUpdated') : t('orchestration.toolCreated'), 'success')
      showAiForm.value = false
      await loadData()
    } else toast.show(t('orchestration.failed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.failed'), 'error') }
}

async function deleteAiTool(name) {
  if (!(await confirm(t('orchestration.deleteAiToolConfirm', { name })))) return
  try {
    const r = await fetch('/api/tools/ai/' + encodeURIComponent(name), { method: 'DELETE' })
    const d = await r.json()
    if (d.success) { toast.show(t('orchestration.toolDeleted'), 'success'); await loadData() }
    else toast.show(t('orchestration.failed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.failed'), 'error') }
}

// ── 自定义 Agent 管理 ──
const showAgentForm = ref(false)
const agentForm = ref({ role: '', name: '', tier: 'expert', personality: '', speaking_style: '', expertise: '', model_id: '' })

function openAgentForm(tier) {
  agentForm.value = { role: '', name: '', tier: tier || 'expert', personality: '', speaking_style: '', expertise: '', model_id: '' }
  showAgentForm.value = true
}

async function submitAgentForm() {
  const f = agentForm.value
  if (!f.role || !f.name) { toast.show(t('orchestration.roleNameRequired'), 'error'); return }
  try {
    const r = await fetch('/api/management/orchestration/agents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(f),
    })
    const d = await r.json()
    if (d.success) {
      toast.show(t('orchestration.agentCreated', { name: f.name }), 'success')
      showAgentForm.value = false
      await loadData()
    } else toast.show(t('orchestration.createFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.createFailed'), 'error') }
}

async function deleteAgent(agent) {
  if (!(await confirm(t('orchestration.deleteAgentConfirm', { name: agent.name })))) return
  try {
    const r = await fetch('/api/management/orchestration/agents/' + encodeURIComponent(agent.role), { method: 'DELETE' })
    const d = await r.json()
    if (d.success) {
      toast.show(t('orchestration.agentDeleted', { name: agent.name }), 'success')
      await loadData()
    } else toast.show(t('orchestration.deleteFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('orchestration.deleteFailed'), 'error') }
}

// ── 人设预设管理 ──
const personaPresets = ref([])
const showPresetSave = ref(false)
const presetName = ref('')
const selectedPreset = ref('')

async function loadPresets() {
  try {
    const r = await fetch('/api/management/persona-presets', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    personaPresets.value = d?.data?.presets || []
  } catch {}
}

async function saveCurrentAsPreset() {
  if (!presetName.value.trim()) { toast.show(t('orchestration.presetNameRequired'), 'error'); return }
  try {
    const personas = {}
    agents.value.forEach((a) => {
      const p = drafts.value[a.role]
      if (p) personas[a.role] = p
    })
    const r = await fetch('/api/management/persona-presets', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: presetName.value.trim(), personas }),
    })
    const d = await r.json()
    if (d.success) {
      toast.show(t('orchestration.presetSaved', { name: presetName.value }), 'success')
      showPresetSave.value = false
      presetName.value = ''
      await loadPresets()
    } else toast.show(t('orchestration.saveFailed'), 'error')
  } catch (e) { toast.show(t('orchestration.saveFailed'), 'error') }
}

async function applyPreset(presetId) {
  try {
    const r = await fetch('/api/management/persona-presets/' + encodeURIComponent(presetId) + '/apply', { method: 'PUT' })
    const d = await r.json()
    if (d.success) {
      toast.show(t('orchestration.presetApplied'), 'success')
      await loadData()
    } else toast.show(t('orchestration.applyFailed'), 'error')
  } catch (e) { toast.show(t('orchestration.applyFailed'), 'error') }
}

async function deletePreset(presetId) {
  if (!(await confirm(t('orchestration.deletePresetConfirm')))) return
  try {
    const r = await fetch('/api/management/persona-presets/' + encodeURIComponent(presetId), { method: 'DELETE' })
    const d = await r.json()
    if (d.success) { toast.show(t('orchestration.presetDeleted'), 'success'); await loadPresets() }
    else toast.show(t('orchestration.deleteFailed'), 'error')
  } catch (e) { toast.show(t('orchestration.deleteFailed'), 'error') }
}

onMounted(() => { loadData(); loadPresets() })
</script>

<template>
  <div>
    <div class="page-header">
      <h2>{{ $t('orchestration.title') }}</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body">
      <div class="seg mt-3">
        <button :class="{ on: activeTab === 'agents' }" @click="activeTab = 'agents'">{{ $t('orchestration.tabs.agents') }}</button>
        <button :class="{ on: activeTab === 'skills' }" @click="activeTab = 'skills'">{{ $t('orchestration.tabs.skills') }}</button>
        <button :class="{ on: activeTab === 'graph' }" @click="activeTab = 'graph'">{{ $t('orchestration.tabs.graph') }}</button>
        <button :class="{ on: activeTab === 'permission' }" @click="activeTab = 'permission'">{{ $t('orchestration.tabs.permission') }}</button>
        <button :class="{ on: activeTab === 'tools' }" @click="activeTab = 'tools'">{{ $t('orchestration.tabs.tools') }} ({{ tools.length }})</button>
      </div>

      <!-- 技能管理（合并自技能页） -->
      <div v-if="activeTab === 'skills'">
        <SkillsView :compact="true" />
      </div>

      <!-- 编排图（合并自会话图谱页） -->
      <div v-if="activeTab === 'graph'">
        <GraphView :compact="true" />
      </div>

      <!-- Agent 定义 -->
      <div v-if="activeTab === 'agents'" v-show="!loading">
        <!-- 人设预设工具栏 -->
        <div class="flex-between mt-3 mb-3">
          <div class="flex gap-2">
            <button class="btn btn-sm" @click="showPresetSave = true"><Icon name="save" :size="13" /> {{ $t('orchestration.savePreset') }}</button>
          </div>
          <div v-if="personaPresets.length" class="flex gap-2 items-center">
            <span class="text-xs text-muted">{{ $t('orchestration.loadPreset') }}：</span>
            <select class="input w-160 text-xs" v-model="selectedPreset">
              <option value="">{{ $t('orchestration.selectPreset') }}...</option>
              <option v-for="p in personaPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
            <button class="btn btn-sm btn-primary" :disabled="!selectedPreset" @click="applyPreset(selectedPreset)">{{ $t('orchestration.apply') }}</button>
            <button class="btn btn-sm danger" :disabled="!selectedPreset" @click="deletePreset(selectedPreset)">{{ $t('common.delete') }}</button>
          </div>
        </div>
        <div v-for="tier in ['large', 'supervisor', 'expert']" :key="tier">
          <div class="card mt-3">
            <div class="card-header flex-between">
              <span>{{ $t(TIER_LABEL[tier]) }}（{{ grouped[tier]?.length || 0 }}）</span>
              <button class="btn btn-sm" @click="openAgentForm(tier)"><Icon name="plus" :size="13" /> {{ $t('orchestration.add') }}</button>
            </div>
            <div v-if="grouped[tier]?.length">
              <div v-for="agent in grouped[tier]" :key="agent.role" class="agent-row">
                <div class="flex-between flex-wrap">
                  <div class="flex-center">
                    <label v-if="tier === 'large'" class="toggle-label mr-2">
                      <input type="radio" :name="'tier-' + tier" :checked="agent.active !== false" @change="toggleAgentActive(agent)" />
                      <span class="toggle-switch"></span>
                    </label>
                    <label v-else class="toggle-label mr-2">
                      <input type="checkbox" :checked="agent.active !== false" @change="toggleAgentActive(agent)" />
                      <span class="toggle-switch"></span>
                    </label>
                    <strong>{{ agent.name }}</strong>
                    <span class="badge badge-gray badge-mono">{{ agent.role }}</span>
                    <span class="badge badge-blue badge-sm">{{ agent.model_id || $t('orchestration.defaultModel') }}</span>
                    <span v-if="agent.custom_persona" class="badge badge-green badge-sm">{{ $t('orchestration.customPersona') }}</span>
                    <span v-if="agent.system_override" class="badge badge-green badge-sm">{{ $t('orchestration.fullOverride') }}</span>
                    <span v-if="agent.role_tools?.whitelist?.length || agent.role_tools?.blacklist?.length" class="badge badge-green badge-sm">{{ $t('orchestration.toolPerms') }}</span>
                    <span v-if="Object.keys(agent.model_params || {}).length" class="badge badge-green badge-sm">{{ $t('orchestration.modelParams') }}</span>
                    <span v-if="agent.is_custom" class="badge badge-purple badge-sm">{{ $t('orchestration.custom') }}</span>
                  </div>
                  <div class="flex gap-2">
                    <button class="btn btn-sm" @click="previewPrompt(agent)"><Icon name="eye" :size="13" /> {{ $t('orchestration.previewPrompt') }}</button>
                    <button v-if="agent.is_custom" class="btn btn-sm danger" @click="deleteAgent(agent)"><Icon name="trash" :size="13" /></button>
                    <button class="btn btn-sm" @click="expanded[agent.role] = !expanded[agent.role]">
                      {{ expanded[agent.role] ? $t('orchestration.collapse') : $t('orchestration.moreSettings') }}
                    </button>
                  </div>
                </div>

                <!-- 人设编辑 -->
                <div class="flex gap-3 mt-2 flex-start">
                  <textarea
                    v-model="drafts[agent.role]"
                    rows="2"
                    class="input persona-textarea"
                    :placeholder="$t('orchestration.personaPlaceholder')"
                  ></textarea>
                  <div class="flex-col gap-2">
                    <button class="btn btn-sm btn-primary" :disabled="saving === agent.role" @click="savePersona(agent)">
                      {{ saving === agent.role ? $t('orchestration.saving') : $t('common.save') }}
                    </button>
                    <button v-if="agent.custom_persona" class="btn btn-sm" @click="clearCustom(agent)">{{ $t('orchestration.restoreDefault') }}</button>
                  </div>
                </div>
                <div v-if="agent.speaking_style || agent.expertise" class="muted-text mt-2">
                  <span v-if="agent.speaking_style">{{ $t('orchestration.style') }}：{{ agent.speaking_style }}</span>
                  <span v-if="agent.expertise" class="ml-3">{{ $t('orchestration.expertise') }}：{{ agent.expertise }}</span>
                </div>

                <!-- 更多设置 -->
                <div v-if="expanded[agent.role]" class="settings-panel">
                  <!-- 完整系统提示词覆盖 -->
                  <div class="flex-between mb-2">
                    <span class="label">{{ $t('orchestration.systemOverride') }}</span>
                    <div class="flex gap-2">
                      <button class="btn btn-sm btn-primary" :disabled="saving === 'ov_' + agent.role" @click="saveOverride(agent)">{{ $t('orchestration.saveOverride') }}</button>
                      <button v-if="agent.system_override" class="btn btn-sm" @click="clearOverride(agent)">{{ $t('orchestration.clear') }}</button>
                    </div>
                  </div>
                  <textarea v-model="overrides[agent.role]" rows="4" class="input textarea-mono" :placeholder="$t('orchestration.overridePlaceholder')"></textarea>

                  <!-- 模型参数 -->
                  <div class="section-heading">
                    <span class="label">{{ $t('orchestration.modelParams') }}</span>
                    <button class="btn btn-sm btn-primary" :disabled="saving === 'mp_' + agent.role" @click="saveModelParams(agent)">{{ $t('orchestration.saveParams') }}</button>
                  </div>
                  <div class="flex gap-4 items-center">
                    <label class="text-xs">{{ $t('orchestration.temperature') }}
                      <input v-model="modelParams[agent.role].temperature" type="number" step="0.1" min="0" max="2" class="input w-90 ml-6" />
                    </label>
                    <label class="text-xs">{{ $t('orchestration.maxTokens') }}
                      <input v-model="modelParams[agent.role].max_tokens" type="number" step="256" min="0" class="input w-110 ml-6" />
                    </label>
                    <label class="text-xs">{{ $t('orchestration.reasoningEffort') }}
                      <select v-model="modelParams[agent.role].reasoning_effort" class="input w-90 ml-6">
                        <option value="">{{ $t('orchestration.default') }}</option>
                        <option value="low">low</option>
                        <option value="medium">medium</option>
                        <option value="high">high</option>
                      </select>
                    </label>
                  </div>

                  <!-- 工具权限 -->
                  <div class="section-heading">
                    <span class="label">{{ $t('orchestration.toolPermTitle') }}</span>
                    <button class="btn btn-sm btn-primary" :disabled="saving === 'tools_' + agent.role" @click="saveRoleTools(agent)">{{ $t('orchestration.savePerms') }}</button>
                  </div>
                  <div class="grid-2 gap-3">
                    <div>
                      <div class="list-label flex-between">
                        <span>{{ $t('orchestration.whitelist', { count: toolsCfg[agent.role]?.whitelist?.length || 0 }) }}</span>
                        <button class="btn btn-sm" @click="toggleAll(agent, 'whitelist')">{{ hasAll(agent, 'whitelist') ? $t('orchestration.unselectAll') : $t('orchestration.allWildcard') }}</button>
                      </div>
                      <div class="scroll-list">
                        <label v-for="tool in tools" :key="'w' + tool.name" class="tool-check">
                          <input type="checkbox" :checked="toolsCfg[agent.role]?.whitelist?.includes(tool.name)" @change="toggleToolList(agent, 'whitelist', tool.name)" />
                          <span class="mono">{{ tool.name }}</span>
                        </label>
                      </div>
                    </div>
                    <div>
                      <div class="list-label">{{ $t('orchestration.blacklist', { count: toolsCfg[agent.role]?.blacklist?.length || 0 }) }}</div>
                      <div class="scroll-list">
                        <label v-for="tool in tools" :key="'b' + tool.name" class="tool-check">
                          <input type="checkbox" :checked="toolsCfg[agent.role]?.blacklist?.includes(tool.name)" @change="toggleToolList(agent, 'blacklist', tool.name)" />
                          <span class="mono">{{ tool.name }}</span>
                        </label>
                      </div>
                    </div>
                  </div>

                </div>
              </div>
            </div>
            <div v-else class="empty-state">{{ $t('orchestration.noAgentTier') }}</div>
          </div>
        </div>
      </div>

      <!-- 权限管理：每角色工具权限（替换原工具管理 tab） -->
      <div v-if="activeTab === 'permission'" v-show="!loading">
        <div class="card">
          <div class="card-header flex-between flex-wrap">
            <span>{{ $t('orchestration.roleToolTitle') }}</span>
            <div class="flex gap-2">
              <select v-model="roleToolSel" class="input w-200 text-xs" @change="onRoleSel">
                <option value="">{{ $t('orchestration.selectRole') }}</option>
                <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
              </select>
              <button class="btn btn-sm btn-primary" :disabled="saving === 'role_tools'" @click="saveRoleToolsSel">{{ $t('orchestration.savePerms') }}</button>
            </div>
          </div>
          <div v-if="roleToolSel" class="grid-2 gap-3 mt-3">
            <div>
              <div class="list-label flex-between">
                <span>{{ $t('orchestration.whitelistReplace', { count: roleToolCfg.whitelist.length }) }}</span>
                <button class="btn btn-sm" @click="toggleRoleAll('whitelist')">{{ roleToolCfg.whitelist.includes('*') ? $t('orchestration.unselectAll') : $t('orchestration.allWildcard') }}</button>
              </div>
              <div class="scroll-list scroll-list-lg">
                <label v-for="tool in tools" :key="'rw' + tool.name" class="tool-check">
                  <input type="checkbox" :checked="roleToolCfg.whitelist.includes(tool.name)" @change="toggleRoleTool('whitelist', tool.name)" />
                  <span class="mono">{{ tool.name }}</span>
                </label>
              </div>
            </div>
            <div>
              <div class="list-label">{{ $t('orchestration.blacklistRemove', { count: roleToolCfg.blacklist.length }) }}</div>
              <div class="scroll-list scroll-list-lg">
                <label v-for="tool in tools" :key="'rb' + tool.name" class="tool-check">
                  <input type="checkbox" :checked="roleToolCfg.blacklist.includes(tool.name)" @change="toggleRoleTool('blacklist', tool.name)" />
                  <span class="mono">{{ tool.name }}</span>
                </label>
              </div>
            </div>
          </div>
          <div v-else class="empty-state text-xs">{{ $t('orchestration.selectRoleHint') }}</div>
        </div>
      </div>

      <!-- 工具管理（合并独立工具管理页 + AI 工具 + 脚本） -->
      <div v-if="activeTab === 'tools'" v-show="!loading">
        <ToolsView :compact="true" />

        <!-- AI 工具管理 -->
        <div class="card mt-3">
          <div class="card-header flex-between">
            <span>{{ $t('orchestration.aiToolsTitle', { count: Object.keys(aiTools).length }) }}</span>
            <button class="btn btn-sm btn-primary" @click="openAiForm(null)"><Icon name="plus" :size="13" /> {{ $t('orchestration.newTool') }}</button>
          </div>
          <div v-if="showAiForm" class="form-section">
            <div class="grid-2 gap-2">
              <input v-model="aiForm.tool_name" class="input text-sm" :placeholder="$t('orchestration.toolNamePlaceholder')" :disabled="!!editingAi" />
              <input v-model="aiForm.description" class="input text-sm" :placeholder="$t('orchestration.description')" />
            </div>
            <textarea v-model="aiForm.code" rows="6" class="input textarea-mono mt-2" :placeholder="$t('orchestration.codePlaceholder')"></textarea>
            <input v-model="aiForm.params" class="input textarea-mono mt-2" :placeholder="$t('orchestration.paramsPlaceholder')" />
            <div class="form-actions">
              <button class="btn btn-sm" @click="showAiForm = false">{{ $t('common.cancel') }}</button>
              <button class="btn btn-sm btn-primary" @click="submitAiForm">{{ editingAi ? $t('orchestration.saveChanges') : $t('orchestration.createTool') }}</button>
            </div>
          </div>
          <div v-if="Object.keys(aiTools).length">
            <div v-for="(info, name) in aiTools" :key="name" class="ai-tool-row">
              <div class="flex-between">
                <div class="min-w-0">
                  <span class="mono accent-text">{{ name }}</span>
                  <span class="muted-text text-xs ml-2">{{ info.description || '' }}</span>
                </div>
                <div class="flex gap-2">
                  <button class="btn btn-sm" @click="viewSource(name)"><Icon name="eye" :size="12" /> {{ $t('orchestration.script') }}</button>
                  <button class="btn btn-sm" @click="openAiForm(name)"><Icon name="pencil" :size="12" /> {{ $t('common.edit') }}</button>
                  <button class="btn btn-sm danger" @click="deleteAiTool(name)"><Icon name="trash" :size="12" /> {{ $t('common.delete') }}</button>
                </div>
              </div>
            </div>
          </div>
          <div v-else class="empty-state">{{ $t('orchestration.noAiTools') }}</div>
        </div>

        <!-- 工具源码弹窗 -->
        <div v-if="srcModal.open" class="src-overlay" @click.self="srcModal.open = false">
          <div class="src-panel">
            <div class="src-head">
              <span class="font-semibold">{{ $t('orchestration.toolScript', { name: srcModal.name, mode: srcModal.editable ? $t('orchestration.editable') : $t('orchestration.readonly') }) }}</span>
              <div class="flex gap-2">
                <button v-if="srcModal.editable" class="btn btn-sm btn-primary" @click="editAiFromSource">{{ $t('orchestration.editThisTool') }}</button>
                <button class="btn btn-sm" @click="srcModal.open = false"><Icon name="x" :size="14" /> {{ $t('common.close') }}</button>
              </div>
            </div>
            <pre class="src-text">{{ srcModal.source }}</pre>
          </div>
        </div>
      </div>

      <div v-else class="empty-state loading-state">{{ $t('common.loading') }}</div>
    </div>

    <!-- 新增 Agent 弹窗 -->
    <div v-if="showAgentForm" class="modal-overlay" @click.self="showAgentForm = false">
      <div class="modal" style="width:480px">
        <div class="modal-header">
          <span>{{ $t('orchestration.add') }} {{ $t(TIER_LABEL[agentForm.tier] || 'orchestration.agent') }}</span>
          <button class="btn btn-sm" @click="showAgentForm = false"><Icon name="x" :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.roleId') }} *</label>
            <input v-model="agentForm.role" class="input mt-1" :placeholder="$t('orchestration.roleIdPlaceholder')" />
            <div class="text-xs text-muted mt-1">{{ $t('orchestration.roleIdHint') }}</div>
          </div>
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.agentName') }} *</label>
            <input v-model="agentForm.name" class="input mt-1" :placeholder="$t('orchestration.agentNamePlaceholder')" />
          </div>
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.tier') }}</label>
            <div class="seg mt-1">
              <button :class="{ on: agentForm.tier === 'large' }" @click="agentForm.tier = 'large'">{{ $t('orchestration.tiers.large') }}</button>
              <button :class="{ on: agentForm.tier === 'supervisor' }" @click="agentForm.tier = 'supervisor'">{{ $t('orchestration.tiers.supervisor') }}</button>
              <button :class="{ on: agentForm.tier === 'expert' }" @click="agentForm.tier = 'expert'">{{ $t('orchestration.tiers.expert') }}</button>
            </div>
          </div>
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.personality') }}</label>
            <textarea v-model="agentForm.personality" rows="3" class="input mt-1" :placeholder="$t('orchestration.personalityPlaceholder')"></textarea>
          </div>
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.speakingStyle') }}</label>
            <input v-model="agentForm.speaking_style" class="input mt-1" :placeholder="$t('orchestration.speakingStylePlaceholder')" />
          </div>
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.expertiseLabel') }}</label>
            <input v-model="agentForm.expertise" class="input mt-1" :placeholder="$t('orchestration.expertisePlaceholder')" />
          </div>
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.modelId') }}</label>
            <input v-model="agentForm.model_id" class="input mt-1" :placeholder="$t('orchestration.modelIdPlaceholder')" />
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-sm" @click="showAgentForm = false">{{ $t('common.cancel') }}</button>
          <button class="btn btn-sm btn-primary" @click="submitAgentForm">{{ $t('orchestration.create') }}</button>
        </div>
      </div>
    </div>

    <!-- 保存人设预设弹窗 -->
    <div v-if="showPresetSave" class="modal-overlay" @click.self="showPresetSave = false">
      <div class="modal" style="width:380px">
        <div class="modal-header">
          <span>{{ $t('orchestration.savePresetTitle') }}</span>
          <button class="btn btn-sm" @click="showPresetSave = false"><Icon name="x" :size="14" /></button>
        </div>
        <div class="modal-body">
          <div class="mb-2">
            <label class="text-xs font-semibold">{{ $t('orchestration.presetName') }}</label>
            <input v-model="presetName" class="input mt-1" :placeholder="$t('orchestration.presetNamePlaceholder')" @keydown.enter="saveCurrentAsPreset" />
            <div class="text-xs text-muted mt-1">{{ $t('orchestration.presetNameHint') }}</div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-sm" @click="showPresetSave = false">{{ $t('common.cancel') }}</button>
          <button class="btn btn-sm btn-primary" @click="saveCurrentAsPreset">{{ $t('common.save') }}</button>
        </div>
      </div>
    </div>

    <!-- 预览提示词弹窗 -->
    <div v-if="promptPreview.open" class="modal-overlay" @click.self="promptPreview.open = false">
      <div class="modal" style="width:640px">
        <div class="modal-header">
          <span>{{ promptPreview.agentName }} — {{ $t('orchestration.promptPreviewTitle') }}</span>
          <button class="btn btn-sm" @click="promptPreview.open = false"><Icon name="x" :size="14" /></button>
        </div>
        <div class="modal-body">
          <div v-if="promptPreview.loading" class="empty-state">{{ $t('common.loading') }}</div>
          <pre v-else class="code-preview">{{ promptPreview.text }}</pre>
        </div>
        <div class="modal-footer">
          <button class="btn btn-sm" @click="promptPreview.open = false">{{ $t('common.close') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 12px; }
.mb-2 { margin-bottom: 6px; }
.ml-2 { margin-left: 8px; }
.ml-3 { margin-left: 12px; }
.gap-2 { gap: 6px; }
.gap-3 { gap: 8px; }
.gap-4 { gap: 16px; }
.flex { display: flex; }
.flex-col { display: flex; flex-direction: column; }
.flex-between { display: flex; justify-content: space-between; align-items: center; }
.flex-center { display: flex; align-items: center; gap: 8px; }
.flex-start { align-items: flex-start; }
.flex-wrap { flex-wrap: wrap; }
.items-center { align-items: center; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; }
.text-xs { font-size: 12px; }
.text-sm { font-size: 13px; }
.label { font-size: 13px; font-weight: 600; }
.font-semibold { font-weight: 600; }
.mono { font-family: monospace; }
.badge-mono { font-family: monospace; font-size: 10px; }
.badge-sm { font-size: 10px; }
.muted-text { font-size: 12px; color: var(--text-muted); }
.accent-text { color: var(--accent); }
.min-w-0 { min-width: 0; }
.agent-row { padding: 12px 0; border-bottom: 1px solid var(--border); }
.persona-textarea { flex: 1; min-height: 60px; font-size: 13px; }
.settings-panel { margin-top: 10px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-2, rgba(255,255,255,0.02)); }
.section-heading { display: flex; justify-content: space-between; align-items: center; margin: 12px 0 6px; }
.section-subheading { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.list-label { font-size: 12px; margin-bottom: 4px; }
.scroll-list { max-height: 180px; overflow-y: auto; border: 1px solid var(--border); border-radius: 6px; padding: 6px; }
.scroll-list-lg { max-height: 200px; }
.tool-check { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 2px 0; }
.textarea-mono { width: 100%; font-size: 12px; font-family: monospace; }
.code-preview { white-space: pre-wrap; font-size: 12px; max-height: 280px; overflow-y: auto; background: var(--bg-2, rgba(255,255,255,0.02)); padding: 10px; border: 1px solid var(--border); border-radius: 6px; }
.empty-state { text-align: center; padding: 20px; color: var(--text-muted); }
.loading-state { padding: 40px; }
.form-section { padding: 12px 0; border-bottom: 1px solid var(--border); }
.form-actions { text-align: right; margin-top: 8px; display: flex; gap: 6px; justify-content: flex-end; }
.ai-tool-row { padding: 10px 0; border-bottom: 1px solid var(--border); }
.tool-card { padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 12px; cursor: pointer; }
.tool-card:hover { border-color: var(--accent); }
.badge-purple { background: rgba(139, 92, 246, 0.12); color: var(--purple); }
.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; display: flex; align-items: center; justify-content: center; }
.modal { background: var(--bg-secondary, #fff); border: 1px solid var(--border); border-radius: 12px; display: flex; flex-direction: column; max-height: 88vh; }
.modal-header { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; border-bottom: 1px solid var(--border); font-weight: 600; }
.modal-body { padding: 18px; overflow-y: auto; flex: 1; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; padding: 12px 18px; border-top: 1px solid var(--border); }
.src-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 1000; display: flex; align-items: center; justify-content: center; }
.src-panel { width: 640px; max-width: 92vw; max-height: 88vh; overflow: hidden; background: var(--bg,#161b22); border: 1px solid var(--border); border-radius: 12px; padding: 14px 18px; display: flex; flex-direction: column; }
.src-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.src-text { white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.6; overflow-y: auto; max-height: calc(88vh - 80px); margin: 0; padding: 12px; background: var(--bg-secondary, rgba(255,255,255,0.02)); border: 1px solid var(--border); border-radius: 8px; }
.toggle-label { display: inline-flex; align-items: center; cursor: pointer; position: relative; }
.toggle-label input { position: absolute; opacity: 0; width: 0; height: 0; }
.toggle-switch { width: 32px; height: 18px; background: var(--border); border-radius: 9px; transition: background 0.2s; position: relative; }
.toggle-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 14px; height: 14px; background: #fff; border-radius: 50%; transition: transform 0.2s; }
.toggle-label input:checked + .toggle-switch { background: var(--accent); }
.toggle-label input:checked + .toggle-switch::after { transform: translateX(14px); }
.mr-2 { margin-right: 8px; }
</style>
