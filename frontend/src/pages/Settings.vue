<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getApiKey, setApiKey, endpoints } from '@/api.js'
import { useConfigStore } from '@/stores/config.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm, usePrompt } from '@/composables/useDialog.js'
import Icon from '@/components/Icon.vue'
import OutreachView from '@/pages/Outreach.vue'

const toast = useToastStore()
const prompt = usePrompt()
const confirm = useConfirm()
const configStore = useConfigStore()
const appVersion = __APP_VERSION__

/* ── Tabs ── */
const tabGroups = [
  { label: '用户', tabs: ['对话', '感知', '记忆库', '主动搭话'] },
  { label: '高级', tabs: ['系统', '高级', '通用设置', '授权设置', '关于'] },
]
const activeTab = ref('对话')

/* ── 记忆库 ── */
const memoryLibs = ref([])
const memoryCurrent = ref('')
const newLibName = ref('')
async function loadMemoryLibs() {
  // 失败自动重试（最多 3 次）——首次加载可能撞上后端初始化/网络抖动，
  // 不再静默置空（无兜底/无重试/无报错），最终失败给 toast 提示
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const r = await endpoints.memoryLibs()
      memoryLibs.value = (r.data && r.data.libs) || []
      memoryCurrent.value = (r.data && r.data.current) || ''
      return true
    } catch (e) {
      if (attempt < 3) {
        await new Promise(r => setTimeout(r, 400 * attempt))
        continue
      }
      memoryLibs.value = []
      toast.show('记忆库列表加载失败: ' + (e.body?.error?.message || e.status || '网络错误'), 'error')
    }
  }
  return false
}

// 切到「记忆库」tab 时重新加载（兜底 onMounted 时序/首次失败）
watch(activeTab, (v) => { if (v === '记忆库') loadMemoryLibs() })
async function switchLib(name) {
  try {
    await endpoints.switchMemoryLib(name)
    memoryCurrent.value = name
    toast.show('已切换到记忆库: ' + name, 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show('切换失败: ' + (e.body?.error?.message || e.status), 'error') }
}
async function createLib() {
  const name = newLibName.value.trim()
  if (!name) return
  try {
    await endpoints.createMemoryLib(name)
    newLibName.value = ''
    toast.show('已创建记忆库: ' + name, 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show('创建失败: ' + (e.body?.error?.message || e.status), 'error') }
}
async function renameLib(lib) {
  const newName = await prompt('重命名记忆库', lib.name)
  if (newName === null || !newName.trim() || newName.trim() === lib.name) return
  try {
    await endpoints.renameMemoryLib(lib.name, newName.trim())
    toast.show('已重命名', 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show('重命名失败: ' + (e.body?.error?.message || e.status), 'error') }
}
async function deleteLib(lib) {
  if (!(await confirm(`确定删除记忆库「${lib.name}」？物理数据文件将保留，仅从列表移除。`))) return
  try {
    await endpoints.deleteMemoryLib(lib.name)
    toast.show('已删除记忆库: ' + lib.name, 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show('删除失败: ' + (e.body?.error?.message || e.status), 'error') }
}

/* ── Config keys (persisted to backend) ── */
const CK = {
  launchAtStartup: 'launch_at_startup',
  preventSleep: 'prevent_sleep',
  showFilename: 'show_filename_in_gallery',
  allowLocation: 'allow_geolocation',
  shortcutKeys: 'shortcut_keys',
  storagePath: 'storage_path',
}

function _bool(v, fallback) {
  if (v === true || v === 'true' || v === 1 || v === '1') return true
  if (v === false || v === 'false' || v === 0 || v === '0') return false
  return fallback
}
function _str(v, fallback) { return v != null ? String(v) : fallback }

/** 保存 Cortex 配置（乐观更新 + 后端持久化） */
async function saveCfg(k, v) {
  configStore.$patch((state) => { state.config = { ...state.config, [k]: v } })
  try { await configStore.updateConfig(k, v); toast.show(k + ' 已更新', 'success') }
  catch (e) { toast.show('保存失败: ' + (e.body?.error?.message || e.status), 'error') }
}
function boolCfg(k, fallback) { return computed({ get: () => _bool(configStore.config[k], fallback), set: (v) => saveCfg(k, v) }) }
function numCfg(k, fallback) {
  return computed({
    get: () => { const v = configStore.config[k]; return v != null && v !== '' ? Number(v) : fallback },
    set: (v) => saveCfg(k, Number(v)),
  })
}
function txtCfg(k, fallback) { return computed({ get: () => _str(configStore.config[k], fallback), set: (v) => saveCfg(k, v.trim() || '') }) }
function segCfg(k, fallback) { return computed({ get: () => _str(configStore.config[k], fallback), set: (v) => saveCfg(k, v) }) }

const cortexMode = segCfg('CORTEX_MODE', 'agent')
const execMode = segCfg('EXECUTION_MODE', 'edit')
const userName = txtCfg('USER_NAME', '用户')
const proactiveEnabled = boolCfg('PROACTIVE_OUTREACH_ENABLED', false)
// 全局默认主动搭话规则（会话未单独配置时生效）
const globalDefault = ref({ enabled: false, scheduleOn: false, scheduleTime: '', scheduleJitter: 10, screenOn: false, screenRatio: 0.5, screenProb: 0.5, screenInterval: 30, screenCooldown: 30, idleOn: false, idleMinutes: 30, idleProb: 0.5, idleInterval: 60, windowsOn: false, timeWindowsText: '' })
async function loadGlobalDefault() {
  try {
    const raw = configStore.config?.PROACTIVE_OUTREACH_DEFAULT || '{}'
    const cfg = (typeof raw === 'string' ? (JSON.parse(raw || '{}')) : (raw || {}))
    const scr = cfg.screen || {}; const idle = cfg.idle || {}; const sched = cfg.schedule || {}
    globalDefault.value = {
      enabled: !!cfg.enabled,
      scheduleOn: !!sched.enabled, scheduleTime: sched.time || '', scheduleJitter: sched.jitter_minutes ?? 10,
      screenOn: !!scr.enabled, screenRatio: scr.change_ratio ?? 0.5, screenProb: scr.probability ?? 0.5,
      screenInterval: scr.check_interval_seconds ?? 30, screenCooldown: scr.cooldown_minutes ?? 30,
      idleOn: !!idle.enabled, idleMinutes: idle.idle_minutes ?? 30, idleProb: idle.probability ?? 0.5,
      idleInterval: idle.check_interval_seconds ?? 60, windowsOn: !!cfg.time_windows_enabled,
      timeWindowsText: (cfg.time_windows || []).map((w) => `${w.start}-${w.end}` + (w.probability != null ? `@${w.probability}` : '')).join(','),
    }
  } catch {}
}
async function saveGlobalDefault() {
  const g = globalDefault.value
  const timeWindows = g.timeWindowsText.split(',').map((x) => x.trim()).filter(Boolean)
    .map((x) => { const m = x.split('@'); const [s, e] = m[0].split('-'); const w = { start: (s || '').trim(), end: (e || '').trim() }; if (m[1] != null) w.probability = parseFloat(m[1]); return w })
    .filter((w) => w.start && w.end)
  const cfg = {
    enabled: !!g.enabled,
    schedule: g.scheduleOn ? { enabled: true, time: g.scheduleTime, jitter_minutes: Math.max(0, g.scheduleJitter || 0) } : {},
    screen: { enabled: !!g.screenOn, change_ratio: Math.max(0, Math.min(1, g.screenRatio ?? 0.5)), probability: Math.max(0, Math.min(1, g.screenProb ?? 0.5)), check_interval_seconds: Math.max(1, g.screenInterval || 30), cooldown_minutes: Math.max(0, g.screenCooldown || 30) },
    idle: { enabled: !!g.idleOn, idle_minutes: Math.max(0, g.idleMinutes || 30), probability: Math.max(0, Math.min(1, g.idleProb ?? 0.5)), check_interval_seconds: Math.max(1, g.idleInterval || 60) },
    time_windows_enabled: !!g.windowsOn, time_windows: timeWindows,
  }
  try {
    const r = await fetch('/api/config/PROACTIVE_OUTREACH_DEFAULT', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: JSON.stringify(cfg) }) })
    const d = await r.json()
    if (d.success) toast.show('全局默认规则已保存', 'success')
    else toast.show('保存失败', 'error')
  } catch { toast.show('保存失败', 'error') }
}
const ttsEnabled = boolCfg('OUTPUT_TTS_ENABLED', false)
const petEnabled = boolCfg('DESKTOP_PET_ENABLED', true)
const petSessionId = txtCfg('DESKTOP_PET_SESSION_ID', 'pet_main')

// ── 桌宠状态（实时显示 + 重置）──
const petState = ref({})
const petStateText = ref('')
const petStateLabels = { mood: '心情', satiety: '饱食', energy: '精力', cleanliness: '清洁' }
async function loadPetState() {
  try {
    const r = await fetch('/api/stream/pet/state', { headers: { Accept: 'application/json' } })
    const d = await r.json()
    if (d?.data?.values) petState.value = d.data.values
    if (d?.data?.text) petStateText.value = d.data.text
  } catch (e) {}
}
async function resetPetState() {
  try {
    const r = await fetch('/api/stream/pet/state/reset', { method: 'POST' })
    const d = await r.json()
    if (d?.data?.values) { petState.value = d.data.values; toast.show('桌宠状态已重置', 'success') }
  } catch (e) { toast.show('重置失败', 'error') }
}
onMounted(() => { loadPetState(); setInterval(loadPetState, 5000); loadGlobalDefault() })
const debugEnabled = boolCfg('DEBUG', false)
const loggingEnabled = boolCfg('LOGGING_ENABLED', true)
const logLevel = segCfg('LOG_LEVEL', 'INFO')
const maxWorkers = numCfg('MAX_WORKERS', 4)
const memTtlShort = numCfg('MEMORY_TTL_SHORT', 3600)
const memTtlLong = numCfg('MEMORY_TTL_LONG', 86400)

/* ── 感知系统模块开关 ── */
const perceptionEnabled = boolCfg('PERCEPTION_ENABLED', true)
const perceptionScreen = boolCfg('PERCEPTION_SCREEN_ENABLED', true)
const perceptionFile = boolCfg('PERCEPTION_FILE_ENABLED', true)
const perceptionMcp = boolCfg('PERCEPTION_MCP_ENABLED', true)
const perceptionInternal = boolCfg('PERCEPTION_INTERNAL_ENABLED', true)
const triggerThink = boolCfg('PERCEPTION_TRIGGER_THINK', true)
const triggerCooldown = numCfg('PERCEPTION_TRIGGER_COOLDOWN', 60)
const triggerMinIntensity = numCfg('PERCEPTION_TRIGGER_MIN_INTENSITY', 50)
const spatialEnhancement = boolCfg('SPATIAL_ENHANCEMENT_ENABLED', false)

/* ── 语音识别（Whisper） ── */
const voiceEnabled = boolCfg('PERCEPTION_VOICE_ENABLED', true)
const voiceModel = segCfg('PERCEPTION_VOICE_MODEL', 'tiny')
const voiceMode = segCfg('PERCEPTION_VOICE_MODE', 'hotkey')
const voiceHotkey = txtCfg('PERCEPTION_VOICE_HOTKEY', 'f8')
const voiceLanguage = txtCfg('PERCEPTION_VOICE_LANGUAGE', 'zh')
const voiceWakePrefix = txtCfg('PERCEPTION_VOICE_WAKE_PREFIX', '科特')
const voiceWakeSuffix = txtCfg('PERCEPTION_VOICE_WAKE_SUFFIX', '完毕')
const voiceEnergy = numCfg('PERCEPTION_VOICE_ENERGY_THRESHOLD', 300)
const voiceTimeout = numCfg('PERCEPTION_VOICE_TIMEOUT', 10)
const voiceMaxDuration = numCfg('PERCEPTION_VOICE_MAX_DURATION', 60)
const voiceEndStop = boolCfg('PERCEPTION_VOICE_END_STOP', true)
const voiceBackend = segCfg('PERCEPTION_VOICE_BACKEND', 'local')
const voiceApiKey = txtCfg('PERCEPTION_VOICE_API_KEY', '')
const voiceApiUrl = txtCfg('PERCEPTION_VOICE_API_URL', '')
const voiceApiModel = txtCfg('PERCEPTION_VOICE_API_MODEL', '')
const ttsBackend = segCfg('OUTPUT_TTS_BACKEND', 'local')
const ttsApiKey = txtCfg('OUTPUT_TTS_API_KEY', '')
const ttsApiUrl = txtCfg('OUTPUT_TTS_API_URL', '')
const ttsApiModel = txtCfg('OUTPUT_TTS_API_MODEL', '')
const ttsApiVoice = txtCfg('OUTPUT_TTS_API_VOICE', '')
async function openFolder(folder) {
  try {
    const r = await fetch('/api/management/open-folder', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder }),
    })
    const d = await r.json()
    if (!d.success) toast.show('打开失败: ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show('打开失败', 'error') }
}

/* ── 视觉模型 ── */
const visionBackend = segCfg('VISION_BACKEND', 'auto')
const visionApiUrl = txtCfg('VISION_API_URL', '')
const visionApiKey = txtCfg('VISION_API_KEY', '')
const visionApiModel = txtCfg('VISION_API_MODEL', '')
const visionApiFormat = txtCfg('VISION_API_FORMAT', '')
const visionLocalModel = txtCfg('VISION_LOCAL_MODEL', '')
const visionMlxModel = txtCfg('VISION_MLX_MODEL', 'mlx-community/Qwen2.5-VL-3B-Instruct-4bit')

/* ── 通用设置 computed（原有） ── */
const launchAtStartup = computed({
  get: () => _bool(configStore.config[CK.launchAtStartup], true),
  set: (v) => saveCfg(CK.launchAtStartup, v),
})
const preventSleep = computed({
  get: () => _bool(configStore.config[CK.preventSleep], false),
  set: (v) => saveCfg(CK.preventSleep, v),
})
const showFilename = computed({
  get: () => _bool(configStore.config[CK.showFilename], false),
  set: (v) => saveCfg(CK.showFilename, v),
})
const allowLocation = computed({
  get: () => _bool(configStore.config[CK.allowLocation], false),
  set: (v) => saveCfg(CK.allowLocation, v),
})
const shortcutKeys = computed({
  get: () => _str(configStore.config[CK.shortcutKeys], '⌥ + T'),
  set: (v) => saveCfg(CK.shortcutKeys, v),
})
const editingShortcut = ref(false)
function startEditShortcut() { editingShortcut.value = true }
function saveShortcut() { editingShortcut.value = false }

const storagePath = ref('')
function copyPath() { navigator.clipboard.writeText(storagePath.value).then(() => toast.show('路径已复制', 'success')) }

/* ── 诊断 modal ── */
const showDiag = ref(false)
const diagData = ref(null)
const diagLoading = ref(false)
async function openDiagnostics() {
  showDiag.value = true; diagLoading.value = true; diagData.value = null
  try {
    const [sys, health] = await Promise.all([endpoints.systemInfo().catch(() => null), endpoints.health().catch(() => null)])
    const models = await endpoints.thinkingStatus().catch(() => null)
    diagData.value = { appVersion, timestamp: new Date().toISOString(), system: sys?.data || sys || {}, health: health?.data || health || {}, models: models?.data?.models || models?.models || {}, configKeys: Object.keys(configStore.config), navigator: { userAgent: navigator.userAgent, platform: navigator.platform, language: navigator.language } }
  } catch (e) { diagData.value = { error: String(e) } }
  diagLoading.value = false
}
function copyDiag() { navigator.clipboard.writeText(JSON.stringify(diagData.value, null, 2)).then(() => toast.show('诊断日志已复制', 'success')) }

/* ── 检查更新 ── */
const checkingUpdate = ref(false)
async function checkUpdates() {
  checkingUpdate.value = true
  try {
    const health = await endpoints.health()
    const serverVersion = health?.data?.version || health?.version || null
    if (serverVersion) toast.show(serverVersion === appVersion ? '当前已是最新版本 v' + appVersion : '发现新版本: ' + serverVersion + '（当前 v' + appVersion + '）', serverVersion === appVersion ? 'success' : 'info')
    else { const sys = await endpoints.systemInfo(); const v = sys?.data?.version || sys?.version || ''; toast.show(v ? '服务器版本: ' + v : '当前版本 v' + appVersion, 'success') }
  } catch { toast.show('无法连接后端服务', 'error') }
  checkingUpdate.value = false
}
function openLink(url) { window.open(url, '_blank') }

/* ── 授权 ── */
const keyInput = ref(getApiKey())
function saveKey() { setApiKey(keyInput.value); toast.show(keyInput.value ? '已保存' : '已清除', 'success') }
function clearKey() { keyInput.value = ''; setApiKey(''); toast.show('已清除', 'success') }

/* ── 高级参数表 ── */
const advancedKeys = computed(() => Object.keys(configStore.config).filter(k => /^(ATTENTION|INTERRUPT|CAUSAL)/.test(k)))
async function editConfig(k, v) {
  const vs = typeof v === 'object' ? JSON.stringify(v) : String(v)
  const nv = await prompt('编辑 ' + k, vs)
  if (nv === null) return
  let val = nv
  if (val === 'true') val = true
  else if (val === 'false') val = false
  else if (!isNaN(val) && val.trim() !== '') val = Number(val)
  try { await configStore.updateConfig(k, val); toast.show(k + ' 已更新', 'success') } catch (e) { toast.show('更新失败: ' + (e.body?.error?.message || e.status), 'error') }
}

/* ── 人设 ── */
const personas = ref([])
const personaDrafts = ref({})
const systemOverrideDrafts = ref({})
const sysOverrideOpen = ref({})
const tierLabel = { large: '大模型', supervisor: '主管', expert: '专家' }
// 按层级分组：总指挥 / 主管 / 专家（人设管理分类展示）
const personasByTier = computed(() => ({
  large: personas.value.filter(p => p.tier === 'large'),
  supervisor: personas.value.filter(p => p.tier === 'supervisor'),
  expert: personas.value.filter(p => p.tier === 'expert'),
}))
async function loadPersonas() {
  // 失败重试 + 报错（同类加固：不再静默置空导致人设列表空白）
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const r = await endpoints.personas()
      personas.value = (r.data && r.data.personas) || []
      personaDrafts.value = {}
      systemOverrideDrafts.value = {}
      for (const p of personas.value) {
        personaDrafts.value[p.role] = p.custom || ''
        systemOverrideDrafts.value[p.role] = p.system_override || ''
      }
      return
    } catch (e) {
      if (attempt < 3) {
        await new Promise(r => setTimeout(r, 400 * attempt))
      } else {
        personas.value = []
        toast.show('人设列表加载失败: ' + (e.body?.error?.message || e.status || '网络错误'), 'error')
      }
    }
  }
}
async function savePersona(role) {
  const val = (personaDrafts.value[role] || '').trim()
  const ovr = (systemOverrideDrafts.value[role] || '').trim()
  try {
    await endpoints.updatePersona(role, val, ovr)
    toast.show(role + ' 人设已保存', 'success')
    await loadPersonas()
  } catch (e) { toast.show('保存失败: ' + (e.body?.error?.message || e.status), 'error') }
}
async function resetPersona(role) {
  if (!(await confirm('确定恢复该角色的默认人设？自定义人设与高级系统提示词覆盖将被清除。'))) return
  personaDrafts.value[role] = ''
  systemOverrideDrafts.value[role] = ''
  try {
    await endpoints.updatePersona(role, '', '')
    toast.show(role + ' 已恢复默认人设', 'success')
    await loadPersonas()
  } catch (e) { toast.show('恢复失败: ' + (e.body?.error?.message || e.status), 'error') }
}

/* ── Init ── */
onMounted(async () => {
  // 记忆库优先并行加载（不阻塞在 loadConfig/loadPersonas 之后，
  // 避免切到"记忆库" tab 时列表还是空的）
  loadMemoryLibs()
  await configStore.loadConfig()
  await configStore.loadModelStatus()
  await loadPersonas()
  const cfgPath = _str(configStore.config[CK.storagePath], '')
  if (cfgPath) { storagePath.value = cfgPath; return }
  try { const info = await endpoints.systemInfo(); storagePath.value = info?.data?.storage_path || info?.storage_path || '' } catch {}
  if (!storagePath.value) {
    const p = navigator.platform || ''
    storagePath.value = p.includes('Mac') ? '~/Library/Application Support/com.cortexagent' : p.includes('Win') ? '%APPDATA%\\CortexAgent' : '~/.cortexagent'
  }
})
</script>

<template>
  <div class="settings-layout">
    <div class="settings-sidebar">
      <template v-for="g in tabGroups" :key="g.label">
        <div class="settings-group-label">{{ g.label }}</div>
        <div
          v-for="tab in g.tabs"
          :key="tab"
          class="settings-tab"
          :class="{ active: activeTab === tab }"
          @click="activeTab = tab"
        >{{ tab }}</div>
      </template>
    </div>

    <div class="settings-content">

      <!-- ═══════════════ 对话 ═══════════════ -->
      <div v-if="activeTab === '对话'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">用户称呼</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">用户称呼</div><div class="d">例如你的名字或昵称</div></div>
            <div class="setting-ctl"><input class="input" v-model="userName" style="width:200px" /></div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">对话模式</div>
          <p class="settings-hint">选择后端处理对话的方式，切换后新消息立即生效</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">处理方式</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: cortexMode === 'agent' }" @click="cortexMode = 'agent'" title="多模型协作：主管+专家分解任务，功能完整">智能体模式</button>
                <button :class="{ on: cortexMode === 'chatonly' }" @click="cortexMode = 'chatonly'" title="轻量单模型直答，无多模型编排，响应更快">纯对话模式</button>
              </div>
            </div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">执行模式</div>
          <p class="settings-hint">工具调用的安全级别</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">安全级别</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: execMode === 'plan' }" @click="execMode = 'plan'" title="禁止所有写操作">只读</button>
                <button :class="{ on: execMode === 'edit' }" @click="execMode = 'edit'" title="写操作前需确认">确认</button>
                <button :class="{ on: execMode === 'yolo' }" @click="execMode = 'yolo'" title="仅安全检测，跳过确认">宽松</button>
                <button :class="{ on: execMode === 'control' }" @click="execMode = 'control'" title="MEDIUM+ 工具需单独确认">完全控制</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 主动搭话 ═══════════════ -->
      <div v-if="activeTab === '主动搭话'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">主动搭话</div>
          <p class="settings-hint">空闲/屏幕变化/指定时段时系统会主动关心你。全局总开关 &gt; 会话规则（单会话覆盖全局默认）</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">全局总开关</div><div class="d">关闭后所有会话（含全局默认）不触发</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="proactiveEnabled" @change="proactiveEnabled = !proactiveEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
        </div>

        <div class="settings-divider"></div>
        <!-- 全局默认规则（会话未单独配置时生效） -->
        <div class="settings-group">
          <div class="settings-group-title">全局默认规则<span style="font-weight:400;color:var(--text-muted);font-size:12px"> —— 会话未单独配置时生效</span></div>
          <div style="display:flex;flex-direction:column;gap:8px;margin-top:4px">
            <div class="setting-row">
              <div class="lbl"><div class="t">启用全局默认规则</div></div>
              <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="globalDefault.enabled" /><span class="toggle-slider"></span></label></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">定点发送</div></div>
              <div class="setting-ctl" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.scheduleOn" /><span class="toggle-slider"></span></label>
                <input class="input" v-model="globalDefault.scheduleTime" style="width:80px" placeholder="14:00" :disabled="!globalDefault.scheduleOn" />
                <span style="font-size:12px;color:var(--text-muted)">±</span>
                <input class="input" type="number" v-model.number="globalDefault.scheduleJitter" style="width:60px" :disabled="!globalDefault.scheduleOn" />
                <span style="font-size:12px;color:var(--text-muted)">min</span>
              </div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">屏幕触发</div></div>
              <div class="setting-ctl" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.screenOn" /><span class="toggle-slider"></span></label>
                <input class="input" type="number" v-model.number="globalDefault.screenRatio" style="width:50px" :disabled="!globalDefault.screenOn" title="变化幅度" />
                <input class="input" type="number" v-model.number="globalDefault.screenProb" style="width:50px" :disabled="!globalDefault.screenOn" title="概率" />
                <input class="input" type="number" v-model.number="globalDefault.screenInterval" style="width:50px" :disabled="!globalDefault.screenOn" title="间隔(s)" />
                <input class="input" type="number" v-model.number="globalDefault.screenCooldown" style="width:50px" :disabled="!globalDefault.screenOn" title="冷却(min)" />
              </div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">空闲触发</div></div>
              <div class="setting-ctl" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.idleOn" /><span class="toggle-slider"></span></label>
                <input class="input" type="number" v-model.number="globalDefault.idleMinutes" style="width:50px" :disabled="!globalDefault.idleOn" title="空闲(min)" />
                <input class="input" type="number" v-model.number="globalDefault.idleProb" style="width:50px" :disabled="!globalDefault.idleOn" title="概率" />
                <input class="input" type="number" v-model.number="globalDefault.idleInterval" style="width:50px" :disabled="!globalDefault.idleOn" title="间隔(s)" />
              </div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">时段触发</div></div>
              <div class="setting-ctl" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.windowsOn" /><span class="toggle-slider"></span></label>
                <input class="input" v-model="globalDefault.timeWindowsText" style="flex:1;min-width:180px" placeholder="09:00-12:00@0.5,14:00-18:00@0.8" :disabled="!globalDefault.windowsOn" />
              </div>
            </div>
            <div style="text-align:right;margin-top:4px">
              <button class="btn btn-sm btn-primary" @click="saveGlobalDefault">保存全局默认规则</button>
            </div>
          </div>
        </div>

        <div class="settings-divider"></div>
        <!-- 会话规则管理（Outreach 并入，每会话可覆盖全局默认） -->
        <div class="settings-group">
          <div class="settings-group-title">会话规则<span style="font-weight:400;color:var(--text-muted);font-size:12px"> —— 单会话配置，覆盖全局默认</span></div>
          <OutreachView :compact="true" />
        </div>
      </div>

      <!-- ═══════════════ 感知 ═══════════════ -->
      <div v-if="activeTab === '感知'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">感知系统模块</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">感知系统总开关</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionEnabled" @change="perceptionEnabled = !perceptionEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">屏幕监控</div><div class="d">监控屏幕变化</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionScreen" @change="perceptionScreen = !perceptionScreen" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">屏幕差异检测</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="screenDiff" @change="screenDiff = !screenDiff" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">文件监控</div><div class="d">监控工作目录文件变化</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionFile" @change="perceptionFile = !perceptionFile" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">MCP 感知</div><div class="d">通过 MCP 服务器采集屏幕/界面</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionMcp" @change="perceptionMcp = !perceptionMcp" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">内部状态感知</div><div class="d">空闲/时间等内部状态差异</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionInternal" @change="perceptionInternal = !perceptionInternal" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">变化触发思考</div><div class="d">感知到高强度变化后自动触发 AI 思考</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="triggerThink" @change="triggerThink = !triggerThink" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">触发冷却(秒)</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="triggerCooldown" style="width:110px;text-align:right" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">最小变化强度</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="triggerMinIntensity" style="width:110px;text-align:right" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">空间增强</div><div class="d">心理活动额外输出当前空间位置/动作序列</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="spatialEnhancement" @change="spatialEnhancement = !spatialEnhancement" /><span class="toggle-slider"></span></label></div>
          </div>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">语音识别（Whisper）</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">语音输入</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="voiceEnabled" @change="voiceEnabled = !voiceEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">识别模型</div><div class="d">Whisper 模型大小</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button v-for="m in ['tiny', 'base', 'small', 'medium', 'large']" :key="m" :class="{ on: voiceModel === m }" @click="voiceModel = m">{{ m }}</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">触发模式</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: voiceMode === 'hotkey' }" @click="voiceMode = 'hotkey'">热键</button>
                <button :class="{ on: voiceMode === 'wake' }" @click="voiceMode = 'wake'">唤醒词</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">热键</div></div>
            <div class="setting-ctl"><input class="input" v-model="voiceHotkey" style="width:120px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">唤醒词</div><div class="d">说话以唤醒词开头</div></div>
            <div class="setting-ctl"><input class="input" v-model="voiceWakePrefix" style="width:120px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">结束词</div><div class="d">说话以结束词结尾</div></div>
            <div class="setting-ctl"><input class="input" v-model="voiceWakeSuffix" style="width:120px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">语言</div></div>
            <div class="setting-ctl"><input class="input" v-model="voiceLanguage" style="width:120px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">能量阈值</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="voiceEnergy" style="width:110px;text-align:right" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">超时(秒)</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="voiceTimeout" style="width:110px;text-align:right" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">最长录音(秒)</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="voiceMaxDuration" style="width:110px;text-align:right" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">检测到结束词停止</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="voiceEndStop" @change="voiceEndStop = !voiceEndStop" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">识别后端</div><div class="d">本地 Whisper 或云端 API</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: voiceBackend === 'local' }" @click="voiceBackend = 'local'">本地</button>
                <button :class="{ on: voiceBackend === 'api' }" @click="voiceBackend = 'api'">云端</button>
              </div>
            </div>
          </div>
          <template v-if="voiceBackend === 'api'">
            <div class="setting-row">
              <div class="lbl"><div class="t">API Key</div></div>
              <div class="setting-ctl"><input class="input" v-model="voiceApiKey" type="password" style="width:240px" placeholder="OpenAI 兼容 /audio/transcriptions" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">API URL</div><div class="d">留空用 OpenAI</div></div>
              <div class="setting-ctl"><input class="input" v-model="voiceApiUrl" style="width:240px" placeholder="https://api.openai.com/v1/audio/transcriptions" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">模型名</div><div class="d">留空用 whisper-1</div></div>
              <div class="setting-ctl"><input class="input" v-model="voiceApiModel" style="width:200px" /></div>
            </div>
          </template>
          <div class="setting-row">
            <div class="lbl"><div class="t">语音模型文件夹</div></div>
            <div class="setting-ctl"><button class="btn btn-sm" @click="openFolder('voice')">打开文件夹</button></div>
          </div>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">视觉模型</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">后端</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: visionBackend === 'auto' }" @click="visionBackend = 'auto'" title="自动选择">自动</button>
                <button :class="{ on: visionBackend === 'api' }" @click="visionBackend = 'api'" title="OpenAI 兼容 API">API</button>
                <button :class="{ on: visionBackend === 'mlx' }" @click="visionBackend = 'mlx'" title="Apple Silicon MLX 本地">MLX</button>
                <button :class="{ on: visionBackend === 'transformers' }" @click="visionBackend = 'transformers'" title="Transformers 本地">本地</button>
                <button :class="{ on: visionBackend === 'mock' }" @click="visionBackend = 'mock'" title="测试">Mock</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">API URL</div></div>
            <div class="setting-ctl"><input class="input" v-model="visionApiUrl" style="width:240px" placeholder="https://..." /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">API Key</div></div>
            <div class="setting-ctl"><input class="input" v-model="visionApiKey" type="password" style="width:240px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">API 模型名</div></div>
            <div class="setting-ctl"><input class="input" v-model="visionApiModel" style="width:200px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">API 格式</div></div>
            <div class="setting-ctl"><input class="input" v-model="visionApiFormat" style="width:160px" placeholder="openai / dashscope" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">本地模型名</div></div>
            <div class="setting-ctl"><input class="input" v-model="visionLocalModel" style="width:240px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">MLX 模型</div></div>
            <div class="setting-ctl"><input class="input" v-model="visionMlxModel" style="width:280px" /></div>
          </div>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">桌面宠物</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">桌宠开关</div><div class="d">桌宠窗口 + 语音对话（实时生效）</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="petEnabled" @change="petEnabled = !petEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">主会话 ID</div><div class="d">桌宠对话记忆，永不删除</div></div>
            <div class="setting-ctl"><input class="input" v-model="petSessionId" style="width:160px" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">当前状态</div><div class="d">互动影响状态，随时间衰减</div></div>
            <div class="setting-ctl" style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
              <span v-for="(label, key) in petStateLabels" :key="key" class="badge" :style="{ background: 'rgba(88,166,255,.12)', color: 'var(--accent)' }">
                {{ label }} <b>{{ petState[key] ?? '-' }}</b>
              </span>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">状态描述</div></div>
            <div class="setting-ctl"><span style="font-size:12px;color:var(--text-secondary)">{{ petStateText || '—' }}</span></div>
          </div>
          <div class="setting-row">
            <div class="lbl"></div>
            <div class="setting-ctl">
              <button class="btn btn-sm" @click="resetPetState">重置状态</button>
              <button class="btn btn-sm" @click="openFolder('pet')" style="margin-left:8px">打开桌宠模型文件夹</button>
            </div>
          </div>
          <p class="settings-hint">语音触发：按 <b>{{ voiceHotkey }}</b> 或说"<b>{{ voiceWakePrefix }}</b>…"后开始说话，桌宠回复会语音播报并显示气泡。关闭开关后桌宠窗口自动隐藏。</p>
        </div>
      </div>

      <!-- ═══════════════ 记忆库 ═══════════════ -->
      <div v-if="activeTab === '记忆库'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">记忆库</div>
          <p class="settings-hint">事件记忆按记忆库隔离存储，可命名多个库并切换当前使用的库。切换即时生效。</p>
          <div style="display:flex;gap:8px;margin-bottom:12px">
            <input class="input" v-model="newLibName" style="flex:1;max-width:260px" placeholder="新记忆库名称" @keydown.enter="createLib" />
            <button class="btn btn-sm btn-primary" @click="createLib">新建记忆库</button>
            <button class="btn btn-sm" @click="loadMemoryLibs" title="刷新记忆库列表"><Icon name="refresh" :size="14" /> 刷新</button>
          </div>
          <div v-if="memoryLibs.length === 0" class="empty-state" style="padding:24px"><p class="empty-text">暂无记忆库（可点击刷新）</p></div>
          <div v-for="lib in memoryLibs" :key="lib.name" class="card" style="margin-bottom:10px">
            <div class="card-header" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
              <b>{{ lib.name }}</b>
              <span v-if="lib.current" class="badge badge-blue">当前</span>
              <span class="badge badge-gray">{{ lib.event_count ?? 0 }} 条事件</span>
            </div>
            <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
              <button v-if="!lib.current" class="btn btn-sm" @click="switchLib(lib.name)">切换到此库</button>
              <button class="btn btn-sm" @click="renameLib(lib)">重命名</button>
              <button v-if="lib.name !== '默认'" class="btn btn-sm danger" @click="deleteLib(lib)">删除</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 系统 ═══════════════ -->
      <div v-if="activeTab === '系统'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">语音与输出</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">TTS 语音输出</div><div class="d">回复时自动合成语音</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="ttsEnabled" @change="ttsEnabled = !ttsEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">合成后端</div><div class="d">本地 gTTS（内置）或云端 API</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: ttsBackend === 'local' }" @click="ttsBackend = 'local'">本地</button>
                <button :class="{ on: ttsBackend === 'api' }" @click="ttsBackend = 'api'">云端</button>
              </div>
            </div>
          </div>
          <template v-if="ttsBackend === 'api'">
            <div class="setting-row">
              <div class="lbl"><div class="t">API Key</div></div>
              <div class="setting-ctl"><input class="input" v-model="ttsApiKey" type="password" style="width:240px" placeholder="OpenAI 兼容 /audio/speech" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">API URL</div><div class="d">留空用 OpenAI</div></div>
              <div class="setting-ctl"><input class="input" v-model="ttsApiUrl" style="width:240px" placeholder="https://api.openai.com/v1/audio/speech" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">模型名</div><div class="d">留空用 tts-1</div></div>
              <div class="setting-ctl"><input class="input" v-model="ttsApiModel" style="width:200px" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">音色</div><div class="d">alloy/echo/fable/onyx/nova/shimmer</div></div>
              <div class="setting-ctl"><input class="input" v-model="ttsApiVoice" style="width:200px" /></div>
            </div>
          </template>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">调试与日志</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">调试模式</div><div class="d">开启 DEBUG 日志</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="debugEnabled" @change="debugEnabled = !debugEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">日志输出</div><div class="d">记录日志文件</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="loggingEnabled" @change="loggingEnabled = !loggingEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">日志级别</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button v-for="lv in ['DEBUG', 'INFO', 'WARNING', 'ERROR']" :key="lv" :class="{ on: logLevel === lv }" @click="logLevel = lv">{{ lv }}</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">最大工作线程</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="maxWorkers" style="width:110px;text-align:right" /></div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">记忆</div>
          <p class="settings-hint">记忆事件有效期（秒）</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">短期记忆 TTL</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="memTtlShort" style="width:110px;text-align:right" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">长期记忆 TTL</div></div>
            <div class="setting-ctl"><input class="input" type="number" v-model.number="memTtlLong" style="width:110px;text-align:right" /></div>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 高级 ═══════════════ -->
      <div v-if="activeTab === '高级'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">注意力 / 因果图</div>
          <p class="settings-hint">进阶参数，谨慎修改</p>
          <div v-if="advancedKeys.length === 0" class="empty-state" style="padding:24px"><p class="empty-text">暂无高级参数</p></div>
          <table v-else class="data-table">
            <thead><tr><th>参数</th><th>当前值</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="k in advancedKeys" :key="k">
                <td><code style="font-size:12px">{{ k }}</code></td>
                <td><span style="font-family:var(--font-mono);font-size:12px">{{ typeof configStore.config[k] === 'object' ? JSON.stringify(configStore.config[k]) : String(configStore.config[k]) }}</span></td>
                <td><button class="btn btn-sm" @click="editConfig(k, configStore.config[k])">编辑</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ═══════════════ 通用设置 ═══════════════ -->
      <div v-if="activeTab === '通用设置'" class="settings-section">
        <div class="settings-group">
          <label class="settings-row" @click.prevent="launchAtStartup = !launchAtStartup">
            <span class="settings-row-label">开机启动</span>
            <span class="toggle-switch"><input type="checkbox" :checked="launchAtStartup" /><span class="toggle-slider"></span></span>
          </label>
          <label class="settings-row" @click.prevent="preventSleep = !preventSleep">
            <span class="settings-row-label">防休眠</span>
            <span class="toggle-switch"><input type="checkbox" :checked="preventSleep" /><span class="toggle-slider"></span></span>
          </label>
          <label class="settings-row" @click.prevent="showFilename = !showFilename">
            <span class="settings-row-label">图库内展示文件名称</span>
            <span class="toggle-switch"><input type="checkbox" :checked="showFilename" /><span class="toggle-slider"></span></span>
          </label>
          <label class="settings-row" @click.prevent="allowLocation = !allowLocation">
            <span class="settings-row-label">授权访问地理位置</span>
            <span class="toggle-switch"><input type="checkbox" :checked="allowLocation" /><span class="toggle-slider"></span></span>
          </label>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">启动快捷键</div>
          <div class="settings-shortcut" @click="startEditShortcut">
            <input v-if="editingShortcut" ref="shortcutInput" v-model="shortcutKeys" class="input shortcut-input" @blur="saveShortcut" @keyup.enter="saveShortcut" />
            <span v-else class="shortcut-key">{{ shortcutKeys }}</span>
            <span class="shortcut-desc">点击快捷键可编辑 — 通过快捷键快速唤起应用窗口</span>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">保留在 Dock 栏</div>
          <p class="settings-hint">右键程序坞中 Cortex Agent 图标 &gt; 选项 &gt; 在程序坞中保留</p>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">文件存储位置</div>
          <div class="settings-path-row">
            <span class="settings-path">{{ storagePath }}</span>
            <button class="btn btn-sm settings-copy-btn" @click="copyPath">
              <img class="settings-logo" src="/favicon.jpg" alt="Logo" />复制
            </button>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-version-row">
            <span class="settings-version-label">当前版本：v{{ appVersion }}</span>
            <div class="settings-btn-row">
              <button class="btn btn-sm" @click="openDiagnostics">诊断日志</button>
              <button class="btn btn-sm" :disabled="checkingUpdate" @click="checkUpdates">{{ checkingUpdate ? '检查中…' : '检查更新' }}</button>
            </div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-links">
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/terms')">用户协议 <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/privacy')">隐私政策 <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com')">进入官网 <span class="link-arrow">↗</span></a>
          </div>
        </div>
        <div class="settings-copyright">Copyright 1998 – 2026 Tencent. All Rights Reserved</div>
      </div>

      <!-- ═══════════════ 授权设置 ═══════════════ -->
      <div v-if="activeTab === '授权设置'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">API 密钥</div>
          <div class="config-api-hint">用于访问需要认证的后端接口。由后端 .env 中的 SIMPLE_API_KEY 控制。</div>
          <div class="search-bar" style="margin-bottom:0">
            <input class="input" v-model="keyInput" placeholder="输入 X-API-Key" style="flex:1" />
            <button class="btn btn-primary btn-sm" @click="saveKey">保存</button>
            <button v-if="keyInput" class="btn btn-sm" @click="clearKey">清除</button>
          </div>
          <div style="margin-top:8px;font-size:12px">
            <span v-if="getApiKey()" class="badge badge-green"><Icon name="check" :size="13" /> 已配置</span>
            <span v-else style="color:var(--text-muted)">未配置</span>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">运行时配置 ({{ Object.keys(configStore.config).length }} 项)</div>
          <div v-if="Object.keys(configStore.config).length === 0" class="empty-state" style="padding:24px"><p class="empty-text">暂无配置项</p></div>
          <table v-else class="data-table">
            <thead><tr><th>配置键</th><th>当前值</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="(v, k) in configStore.config" :key="k">
                <td><code style="font-size:12px">{{ k }}</code></td>
                <td><span class="config-cell-value">{{ typeof v === 'object' ? JSON.stringify(v) : String(v) }}</span></td>
                <td><button class="btn btn-sm" @click="editConfig(k, v)">编辑</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ═══════════════ 关于 ═══════════════ -->
      <div v-if="activeTab === '关于'" class="settings-section">
        <div class="settings-about">
          <img class="settings-logo-lg" src="/favicon.jpg" alt="Logo" />
          <div class="settings-about-title">Cortex Agent</div>
          <div class="settings-about-version">v{{ appVersion }}</div>
          <p class="settings-about-desc">AI Agent 管理控制台 — Vue 3 + Pinia + WebSocket 流式聊天</p>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-links">
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/terms')">用户协议 <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/privacy')">隐私政策 <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com')">进入官网 <span class="link-arrow">↗</span></a>
          </div>
        </div>
        <div class="settings-copyright">Copyright 1998 – 2026 Tencent. All Rights Reserved</div>
      </div>

    </div>
  </div>

  <!-- Diagnostics Modal -->
  <div v-if="showDiag" class="modal-overlay" @click.self="showDiag = false">
    <div class="modal diag-modal">
      <div class="modal-header">
        <span>诊断日志</span>
        <button class="btn btn-sm" @click="showDiag = false">✕</button>
      </div>
      <div class="modal-body">
        <div v-if="diagLoading" class="empty-state" style="padding:32px"><p class="empty-text">正在收集诊断信息…</p></div>
        <pre v-else class="diag-pre">{{ JSON.stringify(diagData, null, 2) }}</pre>
      </div>
      <div class="modal-footer">
        <button class="btn btn-sm" :disabled="diagLoading || !diagData" @click="copyDiag">复制全部</button>
        <button class="btn btn-sm" @click="showDiag = false">关闭</button>
      </div>
    </div>
  </div>
</template>
