<script setup>
import { ref, onMounted, computed } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const skills = ref([])
const agents = ref([])
const loading = ref(true)
const saving = ref('')
const selected = ref('')
const showForm = ref(false)
const editing = ref(null)
const form = ref({ id: '', name: '', description: '', keywords: '', trigger: '', tool_rules: '' })
const roleSel = ref('')
const roleSkills = ref([])

const filtered = computed(() => skills.value)

function skillOf(id) {
  return skills.value.find((s) => s.id === id)
}
function triggerText(s) {
  const t = s.trigger || {}
  const inc = t.include || []
  return inc.length ? inc.slice(0, 3).join(', ') + (inc.length > 3 ? '…' : '') : '—'
}

async function loadData() {
  loading.value = true
  try {
    const [sr, ar] = await Promise.all([
      fetch('/management/skills', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
      fetch('/management/orchestration', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
    ])
    skills.value = sr?.data?.skills || []
    agents.value = ar?.data?.agents || []
    if (roleSel.value) await loadRoleSkills()
  } catch {} finally { loading.value = false }
}

async function loadRoleSkills() {
  if (!roleSel.value) { roleSkills.value = []; return }
  try {
    const r = await fetch('/management/config/role-skills/' + encodeURIComponent(roleSel.value), { headers: { Accept: 'application/json' } })
    const d = await r.json()
    roleSkills.value = d?.data?.skills || []
  } catch { roleSkills.value = [] }
}

async function saveRoleSkills() {
  if (!roleSel.value) return
  try {
    const r = await fetch('/management/config/role-skills/' + encodeURIComponent(roleSel.value), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skills: roleSkills.value }),
    })
    const d = await r.json()
    if (d.success) toast.show('角色技能白名单已保存', 'success')
    else toast.show('保存失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('保存失败', 'error') }
}

function openNew() {
  editing.value = null
  form.value = { id: '', name: '', description: '', keywords: '', trigger: '', tool_rules: '' }
  showForm.value = true
}

function openEdit(id) {
  const s = skillOf(id)
  editing.value = id
  form.value = {
    id: s.id,
    name: s.name,
    description: s.description,
    keywords: (s.keywords || []).join(', '),
    trigger: JSON.stringify(s.trigger || {}),
    tool_rules: JSON.stringify(s.tool_rules || {}),
  }
  showForm.value = true
}

function parseList(str) {
  return String(str || '').split(',').map((x) => x.trim()).filter(Boolean)
}

async function submit() {
  const body = {
    name: form.value.name,
    description: form.value.description,
    keywords: parseList(form.value.keywords),
  }
  let trg = null, tr = null
  try { trg = form.value.trigger ? JSON.parse(form.value.trigger) : null } catch { toast.show('触发规则 JSON 无效', 'error'); return }
  try { tr = form.value.tool_rules ? JSON.parse(form.value.tool_rules) : null } catch { toast.show('工具权限 JSON 无效', 'error'); return }
  body.trigger = trg
  body.tool_rules = tr
  saving.value = editing.value || 'new'
  try {
    const r = await fetch('/management/skills' + (editing.value ? '/' + encodeURIComponent(editing.value) : ''), {
      method: editing.value ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(editing.value ? body : { id: form.value.id, ...body }),
    })
    const d = await r.json()
    if (d.success) { toast.show(editing.value ? '技能已更新' : '技能已创建', 'success'); showForm.value = false; await loadData() }
    else toast.show('失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('失败', 'error') }
  finally { saving.value = '' }
}

async function toggleEnabled(s) {
  const next = !s.enabled
  try {
    const r = await fetch('/management/skills/' + encodeURIComponent(s.id) + '/enabled', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    const d = await r.json()
    if (d.success) s.enabled = next
    else toast.show('失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('失败', 'error') }
}

async function removeSkill(s) {
  if (!confirm('确定删除技能「' + s.name + '」？')) return
  try {
    const r = await fetch('/management/skills/' + encodeURIComponent(s.id), { method: 'DELETE' })
    const d = await r.json()
    if (d.success) { toast.show('技能已删除', 'success'); await loadData() }
    else toast.show('删除失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('删除失败', 'error') }
}

async function reloadSkills() {
  try {
    const r = await fetch('/management/skills/reload', { method: 'POST' })
    const d = await r.json()
    if (d.success) { toast.show('已重载 ' + d.data.count + ' 个技能', 'success'); await loadData() }
  } catch (e) { toast.show('重载失败', 'error') }
}

onMounted(loadData)
</script>

<template>
  <div>
    <div class="page-header">
      <h2>技能管理</h2>
      <div style="display:flex;gap:6px">
        <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
        <button class="btn btn-sm" @click="reloadSkills"><Icon name="refresh" :size="14" /> 重载</button>
        <button class="btn btn-sm btn-primary" @click="openNew"><Icon name="plus" :size="14" /> 新建技能</button>
      </div>
    </div>
    <div class="page-body" v-show="!loading">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <!-- 技能列表 -->
        <div class="card">
          <div class="card-header">技能列表（{{ skills.length }}）</div>
          <div v-if="skills.length" style="max-height:560px;overflow-y:auto">
            <div
              v-for="s in filtered"
              :key="s.id"
              style="padding:10px 12px;border-bottom:1px solid var(--border);cursor:pointer"
              :style="{ background: selected === s.id ? 'rgba(56,139,253,0.08)' : '' }"
              @click="selected = s.id"
            >
              <div style="display:flex;justify-content:space-between;align-items:center;gap:6px">
                <div style="display:flex;align-items:center;gap:8px;min-width:0">
                  <span style="font-family:monospace;color:var(--accent);font-size:13px">{{ s.id }}</span>
                  <span v-if="s.metadata?.type === 'builtin'" class="badge badge-blue" style="font-size:10px">内置</span>
                  <span v-if="!s.enabled" class="badge" style="font-size:10px;background:rgba(139,148,158,0.15);color:#8b949e">已禁用</span>
                </div>
                <label class="toggle-switch" @click.stop title="启用/禁用">
                  <input type="checkbox" :checked="s.enabled" @change="toggleEnabled(s)" />
                  <span class="toggle-slider"></span>
                </label>
              </div>
              <div style="font-size:12px;color:var(--text-muted);margin-top:2px">{{ s.name }}</div>
              <div style="font-size:11px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ s.description || '' }}</div>
            </div>
          </div>
          <div v-else style="text-align:center;padding:24px;color:var(--text-muted)">暂无技能</div>
        </div>

        <!-- 详情/编辑 -->
        <div>
          <div class="card" v-if="showForm">
            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
              <span>{{ editing ? '编辑技能：' + editing : '新建技能' }}</span>
              <button class="btn btn-sm" @click="showForm = false"><Icon name="x" :size="13" /> 关闭</button>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px">
              <input v-model="form.id" class="input" style="font-size:13px" placeholder="id（小写/数字/下划线，仅新建）" :disabled="!!editing" />
              <input v-model="form.name" class="input" style="font-size:13px" placeholder="名称（如：代码审查专家）" />
            </div>
            <textarea v-model="form.description" rows="8" class="input" style="width:100%;margin-top:8px;font-size:13px" placeholder="技能说明书正文（模型阅读后知道怎么做）"></textarea>
            <input v-model="form.keywords" class="input" style="width:100%;margin-top:8px;font-size:12px" placeholder="关键词（逗号分隔，用于自动匹配）" />
            <input v-model="form.trigger" class="input" style="width:100%;margin-top:8px;font-size:12px;font-family:monospace" placeholder='触发规则 JSON：{"include":["审查"],"exclude":["架构"],"min_score":1}' />
            <input v-model="form.tool_rules" class="input" style="width:100%;margin-top:8px;font-size:12px;font-family:monospace" placeholder='工具权限 JSON：{"allow_tools":["read_file"],"restrict_to":true}' />
            <div style="text-align:right;margin-top:10px">
              <button class="btn btn-sm btn-primary" :disabled="saving === (editing || 'new')" @click="submit">
                {{ saving === (editing || 'new') ? '保存中...' : '保存' }}
              </button>
            </div>
          </div>

          <div class="card" v-else-if="selected && skillOf(selected)">
            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
              <span>{{ skillOf(selected).name }}（{{ selected }}）</span>
              <div style="display:flex;gap:6px">
                <button class="btn btn-sm" @click="openEdit(selected)"><Icon name="pencil" :size="12" /> 编辑</button>
                <button class="btn btn-sm danger" @click="removeSkill(skillOf(selected))"><Icon name="trash" :size="12" /> 删除</button>
              </div>
            </div>
            <div style="margin-top:8px">
              <div style="font-size:12px;color:var(--text-muted);margin-bottom:4px">
                来源：{{ skillOf(selected).source }} · 触发：{{ triggerText(skillOf(selected)) }} · 启用：{{ skillOf(selected).enabled ? '是' : '否' }}
              </div>
              <pre style="white-space:pre-wrap;font-size:12px;max-height:420px;overflow-y:auto;background:var(--bg-2, rgba(255,255,255,0.02));padding:10px;border:1px solid var(--border);border-radius:6px">{{ skillOf(selected).description }}</pre>
            </div>
          </div>
          <div class="card" v-else style="text-align:center;padding:40px;color:var(--text-muted)">选择左侧技能查看详情，或点击「新建技能」</div>

          <!-- per-agent 技能可见性 -->
          <div class="card" style="margin-top:12px">
            <div class="card-header" style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
              <span>角色技能白名单（角色可见的技能）</span>
              <div style="display:flex;gap:6px">
                <select v-model="roleSel" class="input" style="width:160px;font-size:12px" @change="loadRoleSkills">
                  <option value="">选择角色</option>
                  <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
                </select>
                <button class="btn btn-sm btn-primary" @click="saveRoleSkills">保存白名单</button>
              </div>
            </div>
            <div v-if="roleSel" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:6px;margin-top:10px;max-height:200px;overflow-y:auto">
              <label v-for="s in skills" :key="s.id" style="display:flex;align-items:center;gap:6px;font-size:12px">
                <input type="checkbox" :checked="roleSkills.includes(s.id) || roleSkills.includes('*')" @change="roleSkills.includes('*') ? null : (roleSkills.includes(s.id) ? roleSkills.splice(roleSkills.indexOf(s.id), 1) : roleSkills.push(s.id))" :disabled="roleSkills.includes('*')" />
                <span style="font-family:monospace">{{ s.id }}</span>
              </label>
              <label style="display:flex;align-items:center;gap:6px;font-size:12px">
                <input type="checkbox" :checked="roleSkills.includes('*')" @change="roleSkills = roleSkills.includes('*') ? [] : ['*']" />
                <span style="font-weight:600">全部 (*)</span>
              </label>
            </div>
            <div v-else style="text-align:center;padding:16px;color:var(--text-muted);font-size:12px">选择角色后配置其可见技能（空 = 全部可见）</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
