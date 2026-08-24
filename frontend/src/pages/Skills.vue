<script setup>
import { ref, onMounted, computed } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm } from '@/composables/useDialog.js'
import { useI18n } from 'vue-i18n'
import Icon from '@/components/Icon.vue'

const { t } = useI18n()
const toast = useToastStore()
const confirm = useConfirm()
const props = defineProps({ compact: { type: Boolean, default: false } })
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
const forcedSkill = ref('')      // 当前全局强制技能 id
const forcedSel = ref('')        // 下拉框选中值
const forcedSaving = ref(false)

const filtered = computed(() => skills.value)

const forcedName = computed(() => {
  if (!forcedSkill.value) return ''
  const s = skillOf(forcedSkill.value)
  return s ? s.name : forcedSkill.value
})

async function loadForced() {
  try {
    const r = await fetch('/api/management/skills/forced', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    if (d.success) {
      forcedSkill.value = d?.data?.forced_skill || ''
      forcedSel.value = forcedSkill.value
    }
  } catch {}
}

async function saveForced() {
  if (!forcedSel.value) return
  forcedSaving.value = true
  try {
    const r = await fetch('/api/management/skills/forced', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skill_id: forcedSel.value }),
    })
    const d = await r.json()
    if (d.success) {
      forcedSkill.value = d?.data?.forced_skill || ''
      toast.show(t('skills.forcedToast', { name: d?.data?.skill?.name || forcedSel.value }), 'success')
    } else toast.show(t('skills.setFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('skills.setFailed'), 'error') }
  finally { forcedSaving.value = false }
}

async function clearForced() {
  forcedSaving.value = true
  try {
    const r = await fetch('/api/management/skills/forced', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skill_id: '' }),
    })
    const d = await r.json()
    if (d.success) {
      forcedSkill.value = ''
      forcedSel.value = ''
      toast.show(t('skills.forcedCleared'), 'success')
    } else toast.show(t('skills.clearFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('skills.clearFailed'), 'error') }
  finally { forcedSaving.value = false }
}

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
      fetch('/api/management/skills', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
      fetch('/api/management/orchestration', { headers: { Accept: 'application/json' } }).then((r) => r.json()).catch(() => null),
    ])
    skills.value = sr?.data?.skills || []
    agents.value = ar?.data?.agents || []
    await loadForced()
    if (roleSel.value) await loadRoleSkills()
  } catch {} finally { loading.value = false }
}

async function loadRoleSkills() {
  if (!roleSel.value) { roleSkills.value = []; return }
  try {
    const r = await fetch('/api/management/config/role-skills/' + encodeURIComponent(roleSel.value), { headers: { Accept: 'application/json' } })
    const d = await r.json()
    roleSkills.value = d?.data?.skills || []
  } catch { roleSkills.value = [] }
}

async function saveRoleSkills() {
  if (!roleSel.value) return
  try {
    const r = await fetch('/api/management/config/role-skills/' + encodeURIComponent(roleSel.value), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ skills: roleSkills.value }),
    })
    const d = await r.json()
    if (d.success) toast.show(t('skills.roleSkillsSaved'), 'success')
    else toast.show(t('common.saveFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('common.saveFailed'), 'error') }
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
  try { trg = form.value.trigger ? JSON.parse(form.value.trigger) : null } catch { toast.show(t('skills.invalidTriggerJson'), 'error'); return }
  try { tr = form.value.tool_rules ? JSON.parse(form.value.tool_rules) : null } catch { toast.show(t('skills.invalidToolRulesJson'), 'error'); return }
  body.trigger = trg
  body.tool_rules = tr
  saving.value = editing.value || 'new'
  try {
    const r = await fetch('/api/management/skills' + (editing.value ? '/' + encodeURIComponent(editing.value) : ''), {
      method: editing.value ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(editing.value ? body : { id: form.value.id, ...body }),
    })
    const d = await r.json()
    if (d.success) { toast.show(editing.value ? t('skills.updated') : t('skills.created'), 'success'); showForm.value = false; await loadData() }
    else toast.show(t('skills.failedPrefix') + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('common.failed'), 'error') }
  finally { saving.value = '' }
}

async function toggleEnabled(s) {
  const next = !s.enabled
  try {
    const r = await fetch('/api/management/skills/' + encodeURIComponent(s.id) + '/enabled', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: next }),
    })
    const d = await r.json()
    if (d.success) s.enabled = next
    else toast.show(t('skills.failedPrefix') + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('common.failed'), 'error') }
}

async function removeSkill(s) {
  if (!(await confirm(t('skills.confirmDelete', { name: s.name })))) return
  try {
    const r = await fetch('/api/management/skills/' + encodeURIComponent(s.id), { method: 'DELETE' })
    const d = await r.json()
    if (d.success) { toast.show(t('skills.deleted'), 'success'); await loadData() }
    else toast.show(t('skills.deleteFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('skills.deleteFailed'), 'error') }
}

async function reloadSkills() {
  try {
    const r = await fetch('/api/management/skills/reload', { method: 'POST' })
    const d = await r.json()
    if (d.success) { toast.show(t('skills.reloaded', { count: d.data.count }), 'success'); await loadData() }
  } catch (e) { toast.show(t('skills.reloadFailed'), 'error') }
}

onMounted(loadData)
</script>

<template>
  <div>
    <div class="page-header" v-if="!compact">
      <h2>{{ $t('skills.title') }}</h2>
      <div class="btn-group">
        <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
        <button class="btn btn-sm" @click="reloadSkills"><Icon name="refresh" :size="14" /> {{ $t('skills.reload') }}</button>
        <button class="btn btn-sm btn-primary" @click="openNew"><Icon name="plus" :size="14" /> {{ $t('skills.newSkill') }}</button>
      </div>
    </div>
    <div class="page-body" v-show="!loading">
      <!-- 全局强制技能：所有对话必须使用该技能（注入提示词，不可切换/停用） -->
      <div class="card" :style="{ border: forcedSkill ? '1px solid #d29922' : '', marginBottom: '12px' }">
        <div class="card-header card-header-wrap">
          <span class="gap-sm">🔒 {{ $t('skills.forcedTitle') }} <span class="text-xs-muted">{{ $t('skills.forcedSubtitle') }}</span></span>
          <span v-if="forcedSkill" class="forced-status">{{ $t('skills.forcedCurrent', { name: forcedName, id: forcedSkill }) }}</span>
        </div>
        <div class="filter-bar">
          <select v-model="forcedSel" class="input min-w-220 text-sm">
            <option value="">{{ $t('skills.forcedPlaceholder') }}</option>
            <option v-for="s in skills.filter(x => x.enabled)" :key="s.id" :value="s.id">{{ s.name }}（{{ s.id }}）</option>
          </select>
          <button class="btn btn-sm btn-primary" :disabled="!forcedSel || forcedSaving || forcedSel === forcedSkill" @click="saveForced">
            {{ forcedSaving ? $t('common.saving') : (forcedSel === forcedSkill ? $t('skills.forcedApplied') : $t('skills.setForced')) }}
          </button>
          <button v-if="forcedSkill" class="btn btn-sm" :disabled="forcedSaving" @click="clearForced">{{ $t('skills.clearForced') }}</button>
        </div>
      </div>
      <div class="detail-grid">
        <!-- 技能列表 -->
        <div class="card">
          <div class="card-header">{{ $t('skills.list') }}（{{ skills.length }}）</div>
          <div v-if="skills.length" class="scroll-list">
            <div
              v-for="s in filtered"
              :key="s.id"
              class="skill-item"
              :style="{ background: selected === s.id ? 'rgba(56,139,253,0.08)' : '' }"
              @click="selected = s.id"
            >
              <div class="item-header">
                <div class="item-id-group">
                  <span class="skill-id">{{ s.id }}</span>
                  <span v-if="s.metadata?.type === 'builtin'" class="badge badge-blue text-xs">{{ $t('skills.builtin') }}</span>
                  <span v-if="!s.enabled" class="badge badge-disabled">{{ $t('skills.disabled') }}</span>
                </div>
                <label class="toggle-switch" @click.stop :title="$t('skills.toggleHint')">
                  <input type="checkbox" :checked="s.enabled" @change="toggleEnabled(s)" />
                  <span class="toggle-slider"></span>
                </label>
              </div>
              <div class="skill-name">{{ s.name }}</div>
              <div class="skill-desc">{{ s.description || '' }}</div>
            </div>
          </div>
          <div v-else class="empty-state">{{ $t('skills.noSkills') }}</div>
        </div>

        <!-- 详情/编辑 -->
        <div>
          <div class="card" v-if="showForm">
            <div class="card-header card-header-between">
              <span>{{ editing ? $t('skills.editTitle', { id: editing }) : $t('skills.newSkill') }}</span>
              <button class="btn btn-sm" @click="showForm = false"><Icon name="x" :size="13" /> {{ $t('common.close') }}</button>
            </div>
            <div class="form-grid">
              <input v-model="form.id" class="input" :placeholder="$t('skills.idPlaceholder')" :disabled="!!editing" />
              <input v-model="form.name" class="input" :placeholder="$t('skills.namePlaceholder')" />
            </div>
            <textarea v-model="form.description" rows="8" class="input form-textarea" :placeholder="$t('skills.descPlaceholder')"></textarea>
            <input v-model="form.keywords" class="input form-input-sm" :placeholder="$t('skills.keywordsPlaceholder')" />
            <input v-model="form.trigger" class="input form-input-mono" placeholder='触发规则 JSON：{"include":["审查"],"exclude":["架构"],"min_score":1}' />
            <input v-model="form.tool_rules" class="input form-input-mono" placeholder='工具权限 JSON：{"allow_tools":["read_file"],"restrict_to":true}' />
            <div class="form-actions">
              <button class="btn btn-sm btn-primary" :disabled="saving === (editing || 'new')" @click="submit">
                {{ saving === (editing || 'new') ? $t('common.saving') : $t('common.save') }}
              </button>
            </div>
          </div>

          <div class="card" v-else-if="selected && skillOf(selected)">
            <div class="card-header card-header-between">
              <span>{{ skillOf(selected).name }}（{{ selected }}）</span>
              <div class="btn-group">
                <button class="btn btn-sm" @click="openEdit(selected)"><Icon name="pencil" :size="12" /> {{ $t('common.edit') }}</button>
                <button class="btn btn-sm danger" @click="removeSkill(skillOf(selected))"><Icon name="trash" :size="12" /> {{ $t('common.delete') }}</button>
              </div>
            </div>
            <div class="detail-section">
              <div class="skill-meta">
                {{ $t('skills.skillMeta', { source: skillOf(selected).source, trigger: triggerText(skillOf(selected)), enabled: skillOf(selected).enabled ? $t('common.yes') : $t('common.no') }) }}
              </div>
              <pre class="code-block" :style="{ width: '100%', fontFamily: 'monospace', border: selected ? '1px solid var(--border)' : '' }">{{ skillOf(selected).description }}</pre>
            </div>
          </div>
          <div class="card empty-state-lg" v-else>{{ $t('skills.selectHint') }}</div>

          <!-- per-agent 技能可见性 -->
          <div class="card section-card">
            <div class="card-header card-header-wrap">
              <span>{{ $t('skills.roleWhitelist') }}</span>
              <div class="btn-group">
                <select v-model="roleSel" class="input w-160 text-xs" @change="loadRoleSkills">
                  <option value="">{{ $t('skills.selectRole') }}</option>
                  <option v-for="a in agents" :key="a.role" :value="a.role">{{ a.name }}（{{ a.role }}）</option>
                </select>
                <button class="btn btn-sm btn-primary" @click="saveRoleSkills">{{ $t('skills.saveWhitelist') }}</button>
              </div>
            </div>
            <div v-if="roleSel" class="role-grid">
              <label v-for="s in skills" :key="s.id" class="role-label">
                <input type="checkbox" :checked="roleSkills.includes(s.id) || roleSkills.includes('*')" @change="roleSkills.includes('*') ? null : (roleSkills.includes(s.id) ? roleSkills.splice(roleSkills.indexOf(s.id), 1) : roleSkills.push(s.id))" :disabled="roleSkills.includes('*')" />
                <span class="mono">{{ s.id }}</span>
              </label>
              <label class="role-label">
                <input type="checkbox" :checked="roleSkills.includes('*')" @change="roleSkills = roleSkills.includes('*') ? [] : ['*']" />
                <span class="fw-600">{{ $t('skills.allSkills') }}</span>
              </label>
            </div>
            <div v-else class="empty-state-sm">{{ $t('skills.roleEmpty') }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.btn-group {
  display: flex;
  gap: 6px;
}
.gap-sm {
  display: flex;
  gap: 8px;
  align-items: center;
}
.text-xs-muted {
  font-weight: 400;
  font-size: 11px;
  color: var(--text-muted);
}
.forced-status {
  font-size: 12px;
  color: #d29922;
}
.filter-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 10px;
}
.detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.scroll-list {
  max-height: 560px;
  overflow-y: auto;
}
.skill-item {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
}
.item-id-group {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.skill-id {
  font-family: monospace;
  color: var(--accent);
  font-size: 13px;
}
.text-xs {
  font-size: 10px;
}
.badge-disabled {
  font-size: 10px;
  background: rgba(139, 148, 158, 0.15);
  color: #8b949e;
}
.skill-name {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 2px;
}
.skill-desc {
  font-size: 11px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty-state {
  text-align: center;
  padding: 24px;
  color: var(--text-muted);
}
.card-header-between {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 8px;
}
.form-textarea {
  width: 100%;
  margin-top: 8px;
  font-size: 13px;
}
.form-input-sm {
  width: 100%;
  margin-top: 8px;
  font-size: 12px;
}
.form-input-mono {
  width: 100%;
  margin-top: 8px;
  font-size: 12px;
  font-family: monospace;
}
.form-actions {
  text-align: right;
  margin-top: 10px;
}
.detail-section {
  margin-top: 8px;
}
.skill-meta {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 4px;
}
.code-block {
  white-space: pre-wrap;
  font-size: 12px;
  max-height: 420px;
  overflow-y: auto;
  background: var(--bg-2, rgba(255, 255, 255, 0.02));
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.empty-state-lg {
  text-align: center;
  padding: 40px;
  color: var(--text-muted);
}
.section-card {
  margin-top: 12px;
}
.card-header-wrap {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 6px;
  margin-top: 10px;
  max-height: 200px;
  overflow-y: auto;
}
.role-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.mono {
  font-family: monospace;
}
.fw-600 {
  font-weight: 600;
}
.empty-state-sm {
  text-align: center;
  padding: 16px;
  color: var(--text-muted);
  font-size: 12px;
}
</style>
