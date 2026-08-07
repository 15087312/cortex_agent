<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
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

const TIER_LABEL = { large: '总指挥', supervisor: '主管', expert: '专家' }

// AI 工具管理
const aiTools = ref([])
const showAiForm = ref(false)
const editingAi = ref(null)
const aiForm = ref({ tool_name: '', description: '', code: '', params: '' })
const toolFilter = ref('')
const toolGroupsOpen = ref({ builtin: true, plugin: true, dynamic: true })

const grouped = computed(() => {
  const g = { large: [], supervisor: [], expert: [] }
  agents.value.forEach((a) => { (g[a.tier] || (g[a.tier] = [])).push(a) })
  return g
})

const toolGroups = computed(() => {
  const g = { builtin: [], plugin: [], dynamic: [], mcp: [] }
  const f = (toolFilter.value || '').toLowerCase()
  tools.value.forEach((t) => {
    if (f && !(t.name + ' ' + (t.description || '')).toLowerCase().includes(f)) return
    const k = g[t.source] !== undefined ? t.source : 'dynamic'
    ;(g[k] || (g[k] = [])).push(t)
  })
  return g
})

async function loadData() {
  loading.value = true
  try {
    const [o, t, ai] = await Promise.all([
      fetch('/management/orchestration', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
      endpoints.tools().catch(() => null),
      fetch('/tools/ai', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
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
      modelParams.value[a.role] = { temperature: a.model_params?.temperature ?? '', max_tokens: a.model_params?.max_tokens ?? '' }
    })
  } catch {} finally { loading.value = false }
}

async function savePersona(agent) {
  saving.value = agent.role
  try {
    const r = await fetch('/config/persona/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: drafts.value[agent.role] || '' }),
    })
    const d = await r.json()
    if (d.success) toast.show(agent.name + ' 人设已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = '' }
}

async function saveOverride(agent) {
  saving.value = 'ov_' + agent.role
  try {
    const r = await fetch('/config/persona/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: drafts.value[agent.role] || '', system_override: overrides.value[agent.role] || '' }),
    })
    const d = await r.json()
    if (d.success) toast.show(agent.name + ' 完整提示词已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = '' }
}

async function saveRoleTools(agent) {
  saving.value = 'tools_' + agent.role
  try {
    const r = await fetch('/config/tools/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tools: toolsCfg.value[agent.role] }),
    })
    const d = await r.json()
    if (d.success) toast.show(agent.name + ' 工具权限已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = '' }
}

async function saveModelParams(agent) {
  saving.value = 'mp_' + agent.role
  const p = modelParams.value[agent.role]
  const body = {}
  if (p.temperature !== '') body.temperature = Number(p.temperature)
  if (p.max_tokens !== '') body.max_tokens = Number(p.max_tokens)
  try {
    const r = await fetch('/config/model-params/' + encodeURIComponent(agent.role), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ params: body }),
    })
    const d = await r.json()
    if (d.success) toast.show(agent.name + ' 模型参数已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
  finally { saving.value = '' }
}

async function previewPrompt(agent) {
  expanded.value[agent.role] = true
  preview.value = { role: agent.role, text: '', loading: true }
  try {
    const r = await fetch('/management/orchestration/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: agent.role, tier: agent.tier }),
    })
    const d = await r.json()
    preview.value.text = d?.data?.prompt || ''
  } catch {} finally { preview.value.loading = false }
}

async function toggleTool(tool) {
  const next = !tool.enabled
  try {
    const r = await fetch('/tools/enabled/' + encodeURIComponent(tool.name), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    const d = await r.json()
    if (d.success) tool.enabled = next
    else toast.show('操作失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('操作失败', 'error') }
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
    aiForm.value = { tool_name: tool, description: aiTools.value[tool]?.description || '', code: '', params: '' }
  } else {
    editingAi.value = null
    aiForm.value = { tool_name: '', description: '', code: '', params: '' }
  }
  showAiForm.value = true
}

async function submitAiForm() {
  const body = { tool_name: aiForm.value.tool_name, description: aiForm.value.description, code: aiForm.value.code, params: aiForm.value.params }
  try {
    const r = await fetch('/tools/ai' + (editingAi.value ? '/' + encodeURIComponent(editingAi.value) : ''), {
      method: editingAi.value ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await r.json()
    if (d.success) {
      toast.show(editingAi.value ? '工具已更新' : '工具已创建', 'success')
      showAiForm.value = false
      await loadData()
    } else toast.show('失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('失败', 'error') }
}

async function deleteAiTool(name) {
  if (!confirm('确定删除 AI 工具 ' + name + '？')) return
  try {
    const r = await fetch('/tools/ai/' + encodeURIComponent(name), { method: 'DELETE' })
    const d = await r.json()
    if (d.success) { toast.show('工具已删除', 'success'); await loadData() }
    else toast.show('失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('失败', 'error') }
}

onMounted(loadData)
</script>

<template>
  <div>
    <div class="page-header">
      <h2>编排</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="seg" style="margin-bottom:12px">
        <button :class="{ on: activeTab === 'agents' }" @click="activeTab = 'agents'">Agent 定义</button>
        <button :class="{ on: activeTab === 'tools' }" @click="activeTab = 'tools'">工具管理 ({{ tools.length }})</button>
        <button :class="{ on: activeTab === 'ai' }" @click="activeTab = 'ai'">AI 工具 ({{ Object.keys(aiTools).length }})</button>
      </div>

      <!-- Agent 定义 -->
      <div v-if="activeTab === 'agents'" v-show="!loading">
        <div v-for="tier in ['large', 'supervisor', 'expert']" :key="tier">
          <div class="card" style="margin-bottom:12px">
            <div class="card-header">{{ TIER_LABEL[tier] }}（{{ grouped[tier]?.length || 0 }}）</div>
            <div v-if="grouped[tier]?.length">
              <div v-for="agent in grouped[tier]" :key="agent.role" style="padding:12px 0;border-bottom:1px solid var(--border)">
                <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
                  <div style="display:flex;align-items:center;gap:8px">
                    <strong>{{ agent.name }}</strong>
                    <span class="badge badge-gray" style="font-family:monospace;font-size:10px">{{ agent.role }}</span>
                    <span class="badge badge-blue" style="font-size:10px">{{ agent.model_id || '默认模型' }}</span>
                    <span v-if="agent.custom_persona" class="badge badge-green" style="font-size:10px">自定义人设</span>
                    <span v-if="agent.system_override" class="badge badge-green" style="font-size:10px">完整覆盖</span>
                    <span v-if="agent.role_tools?.whitelist?.length || agent.role_tools?.blacklist?.length" class="badge badge-green" style="font-size:10px">工具权限</span>
                    <span v-if="Object.keys(agent.model_params || {}).length" class="badge badge-green" style="font-size:10px">模型参数</span>
                  </div>
                  <div style="display:flex;gap:6px">
                    <button class="btn btn-sm" @click="previewPrompt(agent)"><Icon name="eye" :size="13" /> 预览提示词</button>
                    <button class="btn btn-sm" @click="expanded[agent.role] = !expanded[agent.role]">
                      {{ expanded[agent.role] ? '收起' : '更多设置' }}
                    </button>
                  </div>
                </div>

                <!-- 人设编辑 -->
                <div style="display:flex;gap:8px;margin-top:8px;align-items:flex-start">
                  <textarea
                    v-model="drafts[agent.role]"
                    rows="2"
                    class="input"
                    style="flex:1;min-height:60px;font-size:13px"
                    placeholder="自定义人设（留空则用默认人格）"
                  ></textarea>
                  <div style="display:flex;flex-direction:column;gap:6px">
                    <button class="btn btn-sm btn-primary" :disabled="saving === agent.role" @click="savePersona(agent)">
                      {{ saving === agent.role ? '保存中...' : '保存' }}
                    </button>
                    <button v-if="agent.custom_persona" class="btn btn-sm" @click="clearCustom(agent)">恢复默认</button>
                  </div>
                </div>
                <div v-if="agent.speaking_style || agent.expertise" style="margin-top:6px;font-size:12px;color:var(--text-muted)">
                  <span v-if="agent.speaking_style">风格：{{ agent.speaking_style }}</span>
                  <span v-if="agent.expertise" style="margin-left:12px">专长：{{ agent.expertise }}</span>
                </div>

                <!-- 更多设置 -->
                <div v-if="expanded[agent.role]" style="margin-top:10px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--bg-2, rgba(255,255,255,0.02))">
                  <!-- 完整系统提示词覆盖 -->
                  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
                    <span style="font-size:13px;font-weight:600">完整系统提示词覆盖（非空则跳过所有组装，直接使用）</span>
                    <div style="display:flex;gap:6px">
                      <button class="btn btn-sm btn-primary" :disabled="saving === 'ov_' + agent.role" @click="saveOverride(agent)">保存覆盖</button>
                      <button v-if="agent.system_override" class="btn btn-sm" @click="clearOverride(agent)">清除</button>
                    </div>
                  </div>
                  <textarea v-model="overrides[agent.role]" rows="4" class="input" style="width:100%;font-size:12px;font-family:monospace" placeholder="粘贴完整 system prompt（可含所有段）"></textarea>

                  <!-- 模型参数 -->
                  <div style="display:flex;justify-content:space-between;align-items:center;margin:12px 0 6px">
                    <span style="font-size:13px;font-weight:600">模型参数</span>
                    <button class="btn btn-sm btn-primary" :disabled="saving === 'mp_' + agent.role" @click="saveModelParams(agent)">保存参数</button>
                  </div>
                  <div style="display:flex;gap:16px;align-items:center">
                    <label style="font-size:12px">温度 temperature
                      <input v-model="modelParams[agent.role].temperature" type="number" step="0.1" min="0" max="2" class="input" style="width:90px;margin-left:6px" />
                    </label>
                    <label style="font-size:12px">最大 tokens
                      <input v-model="modelParams[agent.role].max_tokens" type="number" step="256" min="0" class="input" style="width:110px;margin-left:6px" />
                    </label>
                  </div>

                  <!-- 工具权限 -->
                  <div style="display:flex;justify-content:space-between;align-items:center;margin:12px 0 6px">
                    <span style="font-size:13px;font-weight:600">工具权限（白名单：非空则整体替换默认；黑名单：剔除）</span>
                    <button class="btn btn-sm btn-primary" :disabled="saving === 'tools_' + agent.role" @click="saveRoleTools(agent)">保存权限</button>
                  </div>
                  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                    <div>
                      <div style="font-size:12px;margin-bottom:4px;display:flex;justify-content:space-between;align-items:center">
                        <span>白名单（{{ toolsCfg[agent.role]?.whitelist?.length || 0 }}）</span>
                        <button class="btn btn-sm" @click="toggleAll(agent, 'whitelist')">{{ hasAll(agent, 'whitelist') ? '取消全部' : '全部 (*)' }}</button>
                      </div>
                      <div style="max-height:180px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
                        <label v-for="tool in tools" :key="'w' + tool.name" style="display:flex;align-items:center;gap:6px;font-size:12px;padding:2px 0">
                          <input type="checkbox" :checked="toolsCfg[agent.role]?.whitelist?.includes(tool.name)" @change="toggleToolList(agent, 'whitelist', tool.name)" />
                          <span style="font-family:monospace">{{ tool.name }}</span>
                        </label>
                      </div>
                    </div>
                    <div>
                      <div style="font-size:12px;margin-bottom:4px">黑名单（{{ toolsCfg[agent.role]?.blacklist?.length || 0 }}）</div>
                      <div style="max-height:180px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:6px">
                        <label v-for="tool in tools" :key="'b' + tool.name" style="display:flex;align-items:center;gap:6px;font-size:12px;padding:2px 0">
                          <input type="checkbox" :checked="toolsCfg[agent.role]?.blacklist?.includes(tool.name)" @change="toggleToolList(agent, 'blacklist', tool.name)" />
                          <span style="font-family:monospace">{{ tool.name }}</span>
                        </label>
                      </div>
                    </div>
                  </div>

                  <!-- 预览 -->
                  <div v-if="preview.role === agent.role && preview.text" style="margin-top:12px">
                    <div style="font-size:13px;font-weight:600;margin-bottom:6px">System Prompt 预览（已应用人设/覆盖）</div>
                    <pre style="white-space:pre-wrap;font-size:12px;max-height:280px;overflow-y:auto;background:var(--bg-2, rgba(255,255,255,0.02));padding:10px;border:1px solid var(--border);border-radius:6px">{{ preview.text }}</pre>
                  </div>
                </div>
              </div>
            </div>
            <div v-else style="text-align:center;padding:20px;color:var(--text-muted)">暂无该层 Agent</div>
          </div>
        </div>
      </div>

      <!-- 工具管理 -->
      <div v-else-if="activeTab === 'tools'" v-show="!loading">
        <div class="card">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
            <span>已注册工具 ({{ tools.length }}) —— 开关即时生效</span>
            <input v-model="toolFilter" class="input" style="width:220px;font-size:12px" placeholder="搜索工具..." />
          </div>
          <div v-for="source in ['builtin', 'plugin', 'dynamic', 'mcp']" :key="source">
            <div v-if="toolGroups[source]?.length">
              <div
                style="display:flex;align-items:center;gap:6px;margin:10px 0 6px;cursor:pointer;font-size:13px;font-weight:600"
                @click="toolGroupsOpen[source] = !toolGroupsOpen[source]"
              >
                <Icon :name="toolGroupsOpen[source] ? 'down' : 'right'" :size="12" />
                {{ source }} ({{ toolGroups[source].length }})
              </div>
              <div v-if="toolGroupsOpen[source]" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:8px">
                <div v-for="tool in toolGroups[source]" :key="tool.name" style="padding:8px 10px;border:1px solid var(--border);border-radius:8px;font-size:12px">
                  <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
                    <span style="font-family:monospace;color:var(--accent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ tool.name }}</span>
                    <label class="toggle-switch" :title="tool.enabled ? '点击禁用' : '点击启用'">
                      <input type="checkbox" :checked="tool.enabled" @change="toggleTool(tool)" />
                      <span class="toggle-slider"></span>
                    </label>
                  </div>
                  <div style="color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ tool.description || '' }}</div>
                  <div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap">
                    <span class="badge" :style="{ fontSize: '10px', background: (tool.risk_level === 'HIGH' || tool.risk_level === 'CRITICAL') ? 'rgba(248,81,73,0.15)' : 'rgba(139,148,158,0.15)', color: (tool.risk_level === 'HIGH' || tool.risk_level === 'CRITICAL') ? '#f85149' : '#8b949e' }">{{ tool.risk_level }}</span>
                    <span v-if="tool.core" class="badge badge-blue" style="font-size:10px">core</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="!tools.length" style="text-align:center;padding:24px;color:var(--text-muted)">暂无工具</div>
        </div>
      </div>

      <!-- AI 工具管理 -->
      <div v-else-if="activeTab === 'ai'" v-show="!loading">
        <div class="card">
          <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
            <span>AI 自创工具（{{ Object.keys(aiTools).length }}）—— 模型可提交代码动态注册</span>
            <button class="btn btn-sm btn-primary" @click="openAiForm(null)"><Icon name="plus" :size="13" /> 新建工具</button>
          </div>
          <div v-if="showAiForm" style="padding:12px 0;border-bottom:1px solid var(--border)">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <input v-model="aiForm.tool_name" class="input" style="font-size:13px" placeholder="工具名（函数名）" :disabled="!!editingAi" />
              <input v-model="aiForm.description" class="input" style="font-size:13px" placeholder="描述" />
            </div>
            <textarea v-model="aiForm.code" rows="6" class="input" style="width:100%;margin-top:8px;font-size:12px;font-family:monospace" placeholder="Python 函数代码（禁止 import，用内置函数）"></textarea>
            <input v-model="aiForm.params" class="input" style="width:100%;margin-top:8px;font-size:12px;font-family:monospace" placeholder='可选参数 JSON：{"query":{"type":"string","required":true}}' />
            <div style="text-align:right;margin-top:8px;display:flex;gap:6px;justify-content:flex-end">
              <button class="btn btn-sm" @click="showAiForm = false">取消</button>
              <button class="btn btn-sm btn-primary" @click="submitAiForm">{{ editingAi ? '保存修改' : '创建工具' }}</button>
            </div>
          </div>
          <div v-if="Object.keys(aiTools).length">
            <div v-for="(info, name) in aiTools" :key="name" style="padding:10px 0;border-bottom:1px solid var(--border)">
              <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
                <div>
                  <span style="font-family:monospace;color:var(--accent)">{{ name }}</span>
                  <span style="color:var(--text-muted);font-size:12px;margin-left:8px">{{ info.description || '' }}</span>
                </div>
                <div style="display:flex;gap:6px">
                  <button class="btn btn-sm" @click="openAiForm(name)"><Icon name="pencil" :size="12" /> 编辑</button>
                  <button class="btn btn-sm danger" @click="deleteAiTool(name)"><Icon name="trash" :size="12" /> 删除</button>
                </div>
              </div>
            </div>
          </div>
          <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无 AI 自创工具</div>
        </div>
      </div>
      <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>
    </div>
  </div>
</template>
