<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const agents = ref([])
const tools = ref([])
const drafts = ref({})
const activeTab = ref('agents')
const saving = ref('')
const loading = ref(true)

const TIER_ORDER = { large: 0, supervisor: 1, expert: 2 }
const TIER_LABEL = { large: '总指挥', supervisor: '主管', expert: '专家' }

const grouped = computed(() => {
  const g = { large: [], supervisor: [], expert: [] }
  agents.value.forEach((a) => { (g[a.tier] || (g[a.tier] = [])).push(a) })
  return g
})

async function loadData() {
  try {
    const [o, t] = await Promise.all([
      fetch('/management/orchestration', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
      endpoints.tools().catch(() => null),
    ])
    agents.value = o?.data?.agents || []
    const toolsData = t?.data
    tools.value = Array.isArray(toolsData) ? toolsData : (Array.isArray(toolsData?.tools) ? toolsData.tools : [])
    agents.value.forEach((a) => { drafts.value[a.role] = a.custom_persona || '' })
  } catch {} finally { loading.value = false }
}

async function savePersona(agent) {
  saving.value = agent.role
  try {
    const r = await fetch('/config/personas/' + encodeURIComponent(agent.role), {
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

function clearCustom(agent) {
  drafts.value[agent.role] = ''
  savePersona(agent)
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
      <!-- 子标签 -->
      <div class="seg" style="margin-bottom:12px">
        <button :class="{ on: activeTab === 'agents' }" @click="activeTab = 'agents'">Agent 定义</button>
        <button :class="{ on: activeTab === 'tools' }" @click="activeTab = 'tools'">工具列表 ({{ tools.length }})</button>
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
                    <span v-if="agent.custom_persona" class="badge badge-green" style="font-size:10px">自定义</span>
                  </div>
                </div>
                <div style="display:flex;gap:8px;margin-top:8px;align-items:flex-start">
                  <textarea
                    v-model="drafts[agent.role]"
                    rows="3"
                    class="input"
                    style="flex:1;min-height:70px;font-size:13px"
                    placeholder="自定义人设（留空则用默认）"
                  ></textarea>
                  <div style="display:flex;flex-direction:column;gap:6px">
                    <button class="btn btn-sm btn-primary" :disabled="saving === agent.role" @click="savePersona(agent)">
                      {{ saving === agent.role ? '保存中...' : '保存' }}
                    </button>
                    <button v-if="agent.custom_persona" class="btn btn-sm" @click="clearCustom(agent)">恢复默认</button>
                  </div>
                </div>
                <div v-if="agent.speaking_style || agent.expertise" style="margin-top:6px;font-size:12px;color:var(--text-muted)">
                  <div v-if="agent.speaking_style">风格：{{ agent.speaking_style }}</div>
                  <div v-if="agent.expertise">专长：{{ agent.expertise }}</div>
                </div>
              </div>
            </div>
            <div v-else style="text-align:center;padding:20px;color:var(--text-muted)">暂无该层 Agent</div>
          </div>
        </div>
      </div>

      <!-- 工具列表 -->
      <div v-else-if="activeTab === 'tools'" v-show="!loading">
        <div class="card">
          <div class="card-header">已注册工具 ({{ tools.length }})</div>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;max-height:500px;overflow-y:auto">
            <div v-for="tool in tools" :key="tool.name" style="padding:8px 10px;border:1px solid var(--border);border-radius:8px;font-size:12px">
              <div style="font-family:monospace;color:var(--accent);margin-bottom:2px">{{ tool.name }}</div>
              <div style="color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ tool.description || '' }}</div>
            </div>
          </div>
          <div v-if="!tools.length" style="text-align:center;padding:24px;color:var(--text-muted)">暂无工具</div>
        </div>
      </div>
      <div v-else style="text-align:center;padding:40px;color:var(--text-muted)">加载中...</div>
    </div>
  </div>
</template>
