<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { endpoints } from '@/api.js'
import { useConfigStore } from '@/stores/config.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm, usePrompt } from '@/composables/useDialog.js'
import Icon from '@/components/Icon.vue'
import OutreachView from '@/pages/Outreach.vue'
import { useI18n } from 'vue-i18n'
import { useLocaleStore } from '@/stores/locale.js'

const { t } = useI18n()
const localeStore = useLocaleStore()
const toast = useToastStore()
const prompt = usePrompt()
const confirm = useConfirm()
const configStore = useConfigStore()
const appVersion = __APP_VERSION__

/* ── Tabs ── */
const tabGroups = [
  { labelKey: 'settings.group.user', tabs: ['chat', 'perception', 'memory', 'proactive'] },
  { labelKey: 'settings.group.advanced', tabs: ['system', 'advanced', 'general', 'auth', 'about'] },
]
const activeTab = ref('chat')

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
      toast.show(t('settings.memory.loadFailed') + ': ' + (e.body?.error?.message || e.status || t('settings.networkError')), 'error')
    }
  }
  return false
}

// 切到「记忆库」tab 时重新加载（兜底 onMounted 时序/首次失败）
watch(activeTab, (v) => { if (v === 'memory') loadMemoryLibs() })
async function switchLib(name) {
  try {
    await endpoints.switchMemoryLib(name)
    memoryCurrent.value = name
    toast.show(t('settings.memory.switchedTo') + ': ' + name, 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show(t('settings.memory.switchFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
}
async function createLib() {
  const name = newLibName.value.trim()
  if (!name) return
  try {
    await endpoints.createMemoryLib(name)
    newLibName.value = ''
    toast.show(t('settings.memory.created') + ': ' + name, 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show(t('settings.memory.createFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
}
async function renameLib(lib) {
  const newName = await prompt(t('settings.memory.renameTitle'), lib.name)
  if (newName === null || !newName.trim() || newName.trim() === lib.name) return
  try {
    await endpoints.renameMemoryLib(lib.name, newName.trim())
    toast.show(t('settings.memory.renamed'), 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show(t('settings.memory.renameFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
}
async function deleteLib(lib) {
  if (!(await confirm(t('settings.memory.deleteConfirm', { name: lib.name })))) return
  const hint = lib.name === '默认' ? t('settings.memory.deleteDefaultHint') : ''
  if (!(await confirm(t('settings.memory.deleteConfirm2', { name: lib.name, hint })))) return
  try {
    await endpoints.deleteMemoryLib(lib.name)
    toast.show(t('settings.memory.physicallyDeleted') + ': ' + lib.name, 'success')
    await loadMemoryLibs()
  } catch (e) { toast.show(t('settings.memory.deleteFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
}

/* ── Config keys (persisted to backend) ── */
const CK = {
  launchAtStartup: 'launch_at_startup',
  preventSleep: 'prevent_sleep',
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
  try { await configStore.updateConfig(k, v); toast.show(k + ' ' + t('settings.updated'), 'success') }
  catch (e) { toast.show(t('settings.saveFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
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
const searchEnginePriority = txtCfg('SEARCH_ENGINE_PRIORITY', '')
const searxngUrl = txtCfg('SEARXNG_URL', '')
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
    if (d.success) toast.show(t('settings.proactive.savedGlobal'), 'success')
    else toast.show(t('settings.saveFailed'), 'error')
  } catch { toast.show(t('settings.saveFailed'), 'error') }
}
const ttsEnabled = boolCfg('OUTPUT_TTS_ENABLED', false)
const mentalEnabled = boolCfg('MENTAL_ACTIVITY_ENABLED', true)
const memorySummaryEnabled = boolCfg('MEMORY_SUMMARY_ENABLED', true)
const petEnabled = boolCfg('DESKTOP_PET_ENABLED', true)
const petSessionId = txtCfg('DESKTOP_PET_SESSION_ID', 'pet_main')

// ── 桌宠状态（实时显示 + 重置）──
const petState = ref({})
const petStateText = ref('')
const petStateLabels = { mood: 'settings.pet.mood', satiety: 'settings.pet.satiety', energy: 'settings.pet.energy', cleanliness: 'settings.pet.cleanliness' }
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
    if (d?.data?.values) { petState.value = d.data.values; toast.show(t('settings.pet.stateReset'), 'success') }
  } catch (e) { toast.show(t('settings.resetFailed'), 'error') }
}
let petPollTimer = null
onMounted(() => {
  loadPetState()
  petPollTimer = setInterval(loadPetState, 5000)
  loadGlobalDefault()
})
onUnmounted(() => { if (petPollTimer) { clearInterval(petPollTimer); petPollTimer = null } })
const debugEnabled = boolCfg('DEBUG', false)
const loggingEnabled = boolCfg('LOGGING_ENABLED', true)
const logLevel = segCfg('LOG_LEVEL', 'INFO')
const maxWorkers = numCfg('MAX_WORKERS', 4)

/* ── 感知系统模块开关 ── */
const perceptionEnabled = boolCfg('PERCEPTION_ENABLED', true)
const perceptionScreen = boolCfg('PERCEPTION_SCREEN_ENABLED', true)
const screenDiff = boolCfg('SCREEN_DIFF_ENABLED', true)
const perceptionMcp = boolCfg('PERCEPTION_MCP_ENABLED', true)
const perceptionInternal = boolCfg('PERCEPTION_INTERNAL_ENABLED', true)
const triggerThink = boolCfg('PERCEPTION_TRIGGER_THINK', true)
const triggerCooldown = numCfg('PERCEPTION_TRIGGER_COOLDOWN', 60)
const triggerMinIntensity = numCfg('PERCEPTION_TRIGGER_MIN_INTENSITY', 50)
const spatialEnhancement = boolCfg('SPATIAL_ENHANCEMENT_ENABLED', false)

/* ── 技能系统 ── */
const skillsEnabled = boolCfg('SKILLS_ENABLED', true)

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
    if (!d.success) toast.show(t('settings.openFolderFailed') + ': ' + (d.error?.message || ''), 'error')
  } catch (e) { toast.show(t('settings.openFolderFailed'), 'error') }
}

const installingDeps = ref(false)
const installResult = ref(null)

async function installVoiceDeps() {
  installingDeps.value = true
  installResult.value = null
  try {
    const r = await endpoints.installVoiceDeps()
    if (r.success) {
      installResult.value = r.data
      toast.show(r.data.message, 'success')
    } else {
      installResult.value = r.data
      toast.show(t('settings.installFailed') + ': ' + (r.data?.message || ''), 'error')
    }
  } catch (e) {
    toast.show(t('settings.installFailed') + ': ' + (e.message || ''), 'error')
  } finally {
    installingDeps.value = false
  }
}

/* ── 主模型配置（大/中/小三层，持久化到 ~/.cortex/settings.json）── */
const modelTiers = [
  { key: 'LARGE', labelKey: 'settings.modelTiers.large', hintKey: 'settings.modelTiers.largeHint' },
  { key: 'MEDIUM', labelKey: 'settings.modelTiers.medium', hintKey: 'settings.modelTiers.mediumHint' },
  { key: 'SMALL', labelKey: 'settings.modelTiers.small', hintKey: 'settings.modelTiers.smallHint' },
]
const modelForm = ref({
  LARGE: { API_KEY: '', API_URL: '', NAME: '', API_FORMAT: '' },
  MEDIUM: { API_KEY: '', API_URL: '', NAME: '' },
  SMALL: { API_KEY: '', API_URL: '', NAME: '' },
})
const modelSaving = ref(false)

function loadModelForm() {
  const c = configStore.config
  for (const t of modelTiers) {
    modelForm.value[t.key].API_KEY = _str(c[t.key + '_MODEL_API_KEY'], '')
    modelForm.value[t.key].API_URL = _str(c[t.key + '_MODEL_API_URL'], '')
    modelForm.value[t.key].NAME = _str(c[t.key + '_MODEL_NAME'], '')
    if (t.key === 'LARGE') modelForm.value[t.key].API_FORMAT = _str(c.LARGE_MODEL_API_FORMAT, '')
  }
}

async function saveModelForm() {
  const c = configStore.config
  modelSaving.value = true
  try {
    const updates = []
    for (const t of modelTiers) {
      const suffixes = ['API_KEY', 'API_URL', 'NAME']
      if (t.key === 'LARGE') suffixes.push('API_FORMAT')
      for (const suffix of suffixes) {
        const key = t.key + '_MODEL_' + suffix
        const val = (modelForm.value[t.key][suffix] || '').trim()
        const old = _str(c[t.key + '_MODEL_' + suffix], '')
        if (val !== old) updates.push([key, val])
      }
    }
    if (updates.length === 0) { toast.show(t('settings.model.noChange'), 'info'); return }
    // 串行保存：每个 PUT 触发后端重建模型实例，并发会交错导致竞态
    for (const [k, v] of updates) {
      await configStore.updateConfig(k, v)
    }
    toast.show(t('settings.model.saved'), 'success')
    loadModelForm()
  } catch (e) { toast.show(t('settings.saveFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
  finally { modelSaving.value = false }
}

/* ── 视觉模型 ── */
const visionBackend = segCfg('VISION_BACKEND', 'local')
const visionApiUrl = txtCfg('VISION_API_URL', '')
const visionApiKey = txtCfg('VISION_API_KEY', '')
const visionApiModel = txtCfg('VISION_API_MODEL', '')
const visionApiFormat = txtCfg('VISION_API_FORMAT', '')
const visionLocalModel = txtCfg('VISION_LOCAL_MODEL', '')
const visionMlxModel = txtCfg('VISION_MLX_MODEL', '')
const chatImageMode = segCfg('CHAT_IMAGE_MODE', 'describe')

// 本地模型文件夹扫描
const visionModelList = ref([])
const visionModelDir = ref('')
async function loadVisionModels() {
  try {
    const r = await endpoints.visionModels()
    if (r.success) {
      visionModelList.value = r.data.models || []
      visionModelDir.value = r.data.dir || ''
    }
  } catch {}
}
function onVisionBackendChange() {
  if (visionBackend.value === 'local') loadVisionModels()
}

/* ── 通用设置 computed（原有） ── */
const launchAtStartup = computed({
  get: () => _bool(configStore.config[CK.launchAtStartup], true),
  set: (v) => saveCfg(CK.launchAtStartup, v),
})
const preventSleep = computed({
  get: () => _bool(configStore.config[CK.preventSleep], false),
  set: (v) => saveCfg(CK.preventSleep, v),
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
function copyPath() { navigator.clipboard.writeText(storagePath.value).then(() => toast.show(t('settings.pathCopied'), 'success')) }

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
function copyDiag() { navigator.clipboard.writeText(JSON.stringify(diagData.value, null, 2)).then(() => toast.show(t('settings.diagCopied'), 'success')) }

/* ── 检查更新 ── */
const checkingUpdate = ref(false)
const updateInfo = ref(null)  // {current, latest, update_available, release_url}
async function checkUpdates() {
  checkingUpdate.value = true
  try {
    // 后端代理检查 GitHub 最新 release（规避浏览器 CORS 与 API 限流）
    const r = await endpoints.latestVersion()
    if (r?.success && r.data) {
      const d = r.data
      updateInfo.value = d
      if (d.update_available) {
        toast.show(t('settings.update.found', { latest: d.latest, current: d.current }), 'info')
        if (d.release_url) window.open(d.release_url, '_blank')
      } else {
        toast.show(t('settings.update.latest', { current: d.current }), 'success')
      }
    } else {
      toast.show(t('settings.update.failed', { current: r?.data?.current || appVersion }), 'warning')
    }
  } catch (e) {
    const cur = e?.body?.data?.current || appVersion
    toast.show(t('settings.update.failed', { current: cur }), 'warning')
  }
  checkingUpdate.value = false
}
function openLink(url) { window.open(url, '_blank') }

/* ── 高级参数表 ── */
const advancedKeys = computed(() => Object.keys(configStore.config).filter(k => /^(ATTENTION|INTERRUPT|CAUSAL)/.test(k)))
const SECRET_KEY_PAT = /(^|_)(API_KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD)(_|$)/
function isSecretKey(k) { return SECRET_KEY_PAT.test(String(k)) }
async function editConfig(k, v) {
  const vs = typeof v === 'object' ? JSON.stringify(v) : String(v)
  const nv = await prompt(t('settings.editKey', { key: k }), vs)
  if (nv === null) return
  let val = nv
  if (val === 'true') val = true
  else if (val === 'false') val = false
  else if (!isNaN(val) && val.trim() !== '') val = Number(val)
  try { await configStore.updateConfig(k, val); toast.show(k + ' ' + t('settings.updated'), 'success') } catch (e) { toast.show(t('settings.updateFailed') + ': ' + (e.body?.error?.message || e.status), 'error') }
}

/* ── 人设 ── */
/* ── Init ── */
onMounted(async () => {
  loadMemoryLibs()
  loadVisionModels()
  await configStore.loadConfig()
  await configStore.loadModelStatus()
  loadModelForm()
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
      <template v-for="g in tabGroups" :key="g.labelKey">
        <div class="settings-group-label">{{ $t(g.labelKey) }}</div>
        <div
          v-for="tab in g.tabs"
          :key="tab"
          class="settings-tab"
          :class="{ active: activeTab === tab }"
          @click="activeTab = tab"
        >{{ $t('settings.tabs.' + tab) }}</div>
      </template>
    </div>

    <div class="settings-content">

      <!-- ═══════════════ 对话 ═══════════════ -->
      <div v-if="activeTab === 'chat'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.chat.userTitle') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.chat.userTitle') }}</div><div class="d">{{ $t('settings.chat.userTitleDesc') }}</div></div>
            <div class="setting-ctl"><input class="input w-200" v-model="userName" /></div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.chat.modeTitle') }}</div>
          <p class="settings-hint">{{ $t('settings.chat.modeHint') }}</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.chat.processing') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: cortexMode === 'agent' }" @click="cortexMode = 'agent'" :title="$t('settings.chat.agentTitle')">{{ $t('settings.chat.agentMode') }}</button>
                <button :class="{ on: cortexMode === 'chatonly' }" @click="cortexMode = 'chatonly'" :title="$t('settings.chat.chatonlyTitle')">{{ $t('settings.chat.chatonlyMode') }}</button>
              </div>
            </div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.chat.execTitle') }}</div>
          <p class="settings-hint">{{ $t('settings.chat.execHint') }}</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.chat.safety') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: execMode === 'plan' }" @click="execMode = 'plan'" :title="$t('settings.chat.planTitle')">{{ $t('settings.chat.plan') }}</button>
                <button :class="{ on: execMode === 'edit' }" @click="execMode = 'edit'" :title="$t('settings.chat.confirmTitle')">{{ $t('common.confirm') }}</button>
                <button :class="{ on: execMode === 'yolo' }" @click="execMode = 'yolo'" :title="$t('settings.chat.yoloTitle')">{{ $t('settings.chat.yolo') }}</button>
                <button :class="{ on: execMode === 'control' }" @click="execMode = 'control'" :title="$t('settings.chat.controlTitle')">{{ $t('settings.chat.control') }}</button>
              </div>
            </div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.chat.searchTitle') }}</div>
          <p class="settings-hint">{{ $t('settings.chat.searchHint') }}</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.chat.searchPriority') }}</div></div>
            <div class="setting-ctl"><input class="input w-360" v-model="searchEnginePriority" placeholder="ddg_html,ddg_lite,ddg_api,sogou,bing_cn,baidu" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.chat.searxngUrl') }}</div><div class="d">{{ $t('settings.chat.searxngDesc') }}</div></div>
            <div class="setting-ctl"><input class="input w-360" v-model="searxngUrl" placeholder="https://searx.example.com" /></div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.model.title') }}</div>
          <p class="settings-hint">{{ $t('settings.model.hint') }}</p>
          <div v-for="tier in modelTiers" :key="tier.key" class="model-tier-block">
            <div class="model-tier-title">{{ $t(tier.labelKey) }}<span class="setting-group-title-hint"> —— {{ $t(tier.hintKey) }}</span></div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiKey') }}</div></div>
              <div class="setting-ctl"><input class="input w-280" type="password" v-model="modelForm[tier.key].API_KEY" placeholder="sk-..." autocomplete="off" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiUrl') }}</div></div>
              <div class="setting-ctl"><input class="input w-360" v-model="modelForm[tier.key].API_URL" placeholder="https://api.deepseek.com/v1/chat/completions" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.name') }}</div></div>
              <div class="setting-ctl"><input class="input w-220" v-model="modelForm[tier.key].NAME" placeholder="deepseek-v4-flash" /></div>
            </div>
            <div v-if="tier.key === 'LARGE'" class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiFormat') }}</div><div class="d">{{ $t('settings.model.apiFormatDesc') }}</div></div>
              <div class="setting-ctl"><input class="input w-200" v-model="modelForm.LARGE.API_FORMAT" placeholder="openai" /></div>
            </div>
          </div>
          <div class="text-right">
            <button class="btn btn-sm btn-primary" :disabled="modelSaving" @click="saveModelForm">{{ modelSaving ? $t('settings.model.saving') : $t('settings.model.saveMain') }}</button>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 主动搭话 ═══════════════ -->
      <div v-if="activeTab === 'proactive'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.proactive.title') }}</div>
          <p class="settings-hint">{{ $t('settings.proactive.hint') }}</p>
          <!-- 触发逻辑说明 -->
          <div class="info-box">
            <div class="info-box-title">{{ $t('settings.proactive.logicTitle') }}</div>
            <div>· <b>{{ $t('settings.proactive.scheduleRule') }}</b>：{{ $t('settings.proactive.scheduleRuleDesc') }}</div>
            <div>· <b>{{ $t('settings.proactive.screenRule') }}</b>：{{ $t('settings.proactive.screenRuleDesc') }}</div>
            <div>· <b>{{ $t('settings.proactive.idleRule') }}</b>：{{ $t('settings.proactive.idleRuleDesc') }}</div>
            <div>· <b>{{ $t('settings.proactive.windowRule') }}</b>：{{ $t('settings.proactive.windowRuleDesc') }}</div>
            <div class="info-box-sub">{{ $t('settings.proactive.prereqTitle') }}</div>
            <div>· {{ $t('settings.proactive.prereqOnline') }}</div>
            <div>· {{ $t('settings.proactive.prereqCooldown') }}</div>
            <div class="info-box-sub">{{ $t('settings.proactive.priorityTitle') }}</div>
            <div>· <b>{{ $t('settings.proactive.globalSwitch') }}</b>（{{ $t('settings.proactive.priorityDesc1') }}）→ <b>{{ $t('settings.proactive.sessionRule') }}</b>（{{ $t('settings.proactive.priorityDesc2') }}）→ {{ $t('settings.proactive.globalDefaultRule') }}（{{ $t('settings.proactive.priorityDesc3') }}）</div>
          </div>
          <div class="setting-row mt-3">
            <div class="lbl"><div class="t">{{ $t('settings.proactive.globalSwitch') }}</div><div class="d">{{ $t('settings.proactive.globalSwitchDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="proactiveEnabled" @change="proactiveEnabled = !proactiveEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
        </div>

        <div class="settings-divider"></div>
        <!-- 会话规则（核心：按会话单独设置） -->
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.proactive.sessionRule') }}<span class="setting-group-title-hint"> —— {{ $t('settings.proactive.sessionRuleHint') }}</span></div>
          <OutreachView :compact="true" />
        </div>

        <div class="settings-divider"></div>
        <!-- 全局默认规则（仅当会话未配置时生效，兜底） -->
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.proactive.globalDefaultRule') }}<span class="setting-group-title-hint"> —— {{ $t('settings.proactive.globalDefaultHint') }}</span></div>
          <div class="flex-col">
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.proactive.enableGlobalDefault') }}</div><div class="d">{{ $t('settings.proactive.enableGlobalDefaultDesc') }}</div></div>
              <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" v-model="globalDefault.enabled" /><span class="toggle-slider"></span></label></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.proactive.scheduleTrigger') }}</div><div class="d">{{ $t('settings.proactive.scheduleTriggerDesc') }}</div></div>
              <div class="setting-ctl ctl-flex">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.scheduleOn" /><span class="toggle-slider"></span></label>
                <span class="text-muted">{{ $t('common.time') }}</span>
                <input class="input w-80" v-model="globalDefault.scheduleTime" placeholder="14:00" :disabled="!globalDefault.scheduleOn" :title="$t('settings.proactive.scheduleTimeTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.jitter') }}</span>
                <input class="input w-60" type="number" v-model.number="globalDefault.scheduleJitter" :disabled="!globalDefault.scheduleOn" :title="$t('settings.proactive.scheduleJitterTitle')" />
                <span class="text-muted">min</span>
              </div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.proactive.screenTrigger') }}</div><div class="d">{{ $t('settings.proactive.screenTriggerDesc') }}</div></div>
              <div class="setting-ctl ctl-flex">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.screenOn" /><span class="toggle-slider"></span></label>
                <input class="input w-50" type="number" v-model.number="globalDefault.screenRatio" :disabled="!globalDefault.screenOn" :title="$t('settings.proactive.screenRatioTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.threshold') }}</span>
                <input class="input w-50" type="number" v-model.number="globalDefault.screenProb" :disabled="!globalDefault.screenOn" :title="$t('settings.proactive.screenProbTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.probability') }}</span>
                <input class="input w-50" type="number" v-model.number="globalDefault.screenInterval" :disabled="!globalDefault.screenOn" :title="$t('settings.proactive.screenIntervalTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.interval') }}</span>
                <input class="input w-50" type="number" v-model.number="globalDefault.screenCooldown" :disabled="!globalDefault.screenOn" :title="$t('settings.proactive.screenCooldownTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.cooldown') }}</span>
              </div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.proactive.idleTrigger') }}</div><div class="d">{{ $t('settings.proactive.idleTriggerDesc') }}</div></div>
              <div class="setting-ctl ctl-flex">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.idleOn" /><span class="toggle-slider"></span></label>
                <input class="input w-50" type="number" v-model.number="globalDefault.idleMinutes" :disabled="!globalDefault.idleOn" :title="$t('settings.proactive.idleMinutesTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.idle') }}</span>
                <input class="input w-50" type="number" v-model.number="globalDefault.idleProb" :disabled="!globalDefault.idleOn" :title="$t('settings.proactive.idleProbTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.probability') }}</span>
                <input class="input w-50" type="number" v-model.number="globalDefault.idleInterval" :disabled="!globalDefault.idleOn" :title="$t('settings.proactive.idleIntervalTitle')" />
                <span class="text-muted">{{ $t('settings.proactive.interval') }}</span>
              </div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.proactive.windowTrigger') }}</div><div class="d">{{ $t('settings.proactive.windowTriggerDesc') }}</div></div>
              <div class="setting-ctl ctl-flex">
                <label class="toggle-switch"><input type="checkbox" v-model="globalDefault.windowsOn" /><span class="toggle-slider"></span></label>
                <input class="input w-flex-220" v-model="globalDefault.timeWindowsText" :placeholder="$t('settings.proactive.windowsPlaceholder')" :disabled="!globalDefault.windowsOn" :title="$t('settings.proactive.windowsTitle')" />
              </div>
            </div>
            <div class="text-right">
              <button class="btn btn-sm btn-primary" @click="saveGlobalDefault">{{ $t('settings.proactive.saveGlobalDefault') }}</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 感知 ═══════════════ -->
      <div v-if="activeTab === 'perception'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.perception.modules') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.master') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionEnabled" @change="perceptionEnabled = !perceptionEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.screen') }}</div><div class="d">{{ $t('settings.perception.screenDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionScreen" @change="perceptionScreen = !perceptionScreen" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.screenDiff') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="screenDiff" @change="screenDiff = !screenDiff" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.mcp') }}</div><div class="d">{{ $t('settings.perception.mcpDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionMcp" @change="perceptionMcp = !perceptionMcp" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.internal') }}</div><div class="d">{{ $t('settings.perception.internalDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="perceptionInternal" @change="perceptionInternal = !perceptionInternal" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.triggerThink') }}</div><div class="d">{{ $t('settings.perception.triggerThinkDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="triggerThink" @change="triggerThink = !triggerThink" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.triggerCooldown') }}</div></div>
            <div class="setting-ctl"><input class="input w-110" type="number" v-model.number="triggerCooldown" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.minIntensity') }}</div></div>
            <div class="setting-ctl"><input class="input w-110" type="number" v-model.number="triggerMinIntensity" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.spatial') }}</div><div class="d">{{ $t('settings.perception.spatialDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="spatialEnhancement" @change="spatialEnhancement = !spatialEnhancement" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.mental') }}</div><div class="d">{{ $t('settings.perception.mentalDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="mentalEnabled" @change="mentalEnabled = !mentalEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.memorySummary') }}</div><div class="d">{{ $t('settings.perception.memorySummaryDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="memorySummaryEnabled" @change="memorySummaryEnabled = !memorySummaryEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.perception.skills') }}</div><div class="d">{{ $t('settings.perception.skillsDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="skillsEnabled" @change="skillsEnabled = !skillsEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.voice.title') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.input') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="voiceEnabled" @change="voiceEnabled = !voiceEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.model') }}</div><div class="d">{{ $t('settings.voice.modelDesc') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button v-for="m in ['tiny', 'base', 'small', 'medium', 'large']" :key="m" :class="{ on: voiceModel === m }" @click="voiceModel = m">{{ m }}</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.triggerMode') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: voiceMode === 'hotkey' }" @click="voiceMode = 'hotkey'">{{ $t('settings.voice.hotkey') }}</button>
                <button :class="{ on: voiceMode === 'wake' }" @click="voiceMode = 'wake'">{{ $t('settings.voice.wake') }}</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.hotkey') }}</div></div>
            <div class="setting-ctl"><input class="input w-120" v-model="voiceHotkey" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.wakeWord') }}</div><div class="d">{{ $t('settings.voice.wakeWordDesc') }}</div></div>
            <div class="setting-ctl"><input class="input w-120" v-model="voiceWakePrefix" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.endWord') }}</div><div class="d">{{ $t('settings.voice.endWordDesc') }}</div></div>
            <div class="setting-ctl"><input class="input w-120" v-model="voiceWakeSuffix" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.language') }}</div></div>
            <div class="setting-ctl"><input class="input w-120" v-model="voiceLanguage" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.energy') }}</div></div>
            <div class="setting-ctl"><input class="input w-110" type="number" v-model.number="voiceEnergy" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.timeout') }}</div></div>
            <div class="setting-ctl"><input class="input w-110" type="number" v-model.number="voiceTimeout" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.maxDuration') }}</div></div>
            <div class="setting-ctl"><input class="input w-110" type="number" v-model.number="voiceMaxDuration" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.endStop') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="voiceEndStop" @change="voiceEndStop = !voiceEndStop" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.backend') }}</div><div class="d">{{ $t('settings.voice.backendDesc') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: voiceBackend === 'local' }" @click="voiceBackend = 'local'">{{ $t('settings.voice.local') }}</button>
                <button :class="{ on: voiceBackend === 'api' }" @click="voiceBackend = 'api'">{{ $t('settings.voice.cloud') }}</button>
              </div>
            </div>
          </div>
          <template v-if="voiceBackend === 'api'">
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiKey') }}</div></div>
              <div class="setting-ctl"><input class="input w-240" v-model="voiceApiKey" type="password" :placeholder="$t('settings.voice.apiKeyPlaceholder')" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiUrl') }}</div><div class="d">{{ $t('settings.voice.emptyOpenAI') }}</div></div>
              <div class="setting-ctl"><input class="input w-240" v-model="voiceApiUrl" placeholder="https://api.openai.com/v1/audio/transcriptions" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.name') }}</div><div class="d">{{ $t('settings.voice.apiModelDesc') }}</div></div>
              <div class="setting-ctl"><input class="input w-200" v-model="voiceApiModel" /></div>
            </div>
          </template>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.voice.modelFolder') }}</div></div>
            <div class="setting-ctl flex-gap-8">
              <button class="btn btn-sm" @click="openFolder('voice')">{{ $t('settings.voice.openFolder') }}</button>
              <button class="btn btn-sm btn-accent" @click="installVoiceDeps" :disabled="installingDeps">
                {{ installingDeps ? $t('settings.voice.installing') : $t('settings.voice.installDeps') }}
              </button>
            </div>
          </div>
          <div v-if="installResult" class="setting-row">
            <div class="lbl"></div>
            <div class="setting-ctl">
              <div class="install-result" :class="installResult.success ? 'success' : 'error'">
                <div v-for="(r, i) in installResult.results" :key="i" class="install-item">
                  <span class="pkg-name">{{ r.package }}</span>
                  <span class="pkg-status" :class="r.status">
                    {{ r.status === 'already_installed' || r.status === 'installed' ? $t('settings.voice.installed') : $t('settings.voice.installFailed') }}
                  </span>
                </div>
                <div class="install-msg">{{ installResult.message }}</div>
              </div>
            </div>
          </div>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.vision.title') }}</div>
          <p class="settings-hint">{{ $t('settings.vision.hint') }}</p>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.vision.chatImageMode') }}</div><div class="d">{{ $t('settings.vision.chatImageModeDesc') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: chatImageMode === 'describe' }" @click="chatImageMode = 'describe'" :title="$t('settings.vision.describeTitle')">{{ $t('settings.vision.describe') }}</button>
                <button :class="{ on: chatImageMode === 'direct' }" @click="chatImageMode = 'direct'" :title="$t('settings.vision.directTitle')">{{ $t('settings.vision.direct') }}</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.vision.backend') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: visionBackend === 'api' }" @click="visionBackend = 'api'; onVisionBackendChange()" :title="$t('settings.vision.apiTitle')">API</button>
                <button :class="{ on: visionBackend === 'local' }" @click="visionBackend = 'local'; onVisionBackendChange()" :title="$t('settings.vision.localTitle')">{{ $t('settings.vision.local') }}</button>
              </div>
            </div>
          </div>
          <!-- API 模式 -->
          <template v-if="visionBackend === 'api'">
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiUrl') }}</div><div class="d">{{ $t('settings.vision.emptyOpenAI') }}</div></div>
              <div class="setting-ctl"><input class="input w-280" v-model="visionApiUrl" placeholder="https://api.openai.com/v1/chat/completions" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiKey') }}</div></div>
              <div class="setting-ctl"><input class="input w-280" v-model="visionApiKey" type="password" placeholder="sk-..." /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.name') }}</div><div class="d">{{ $t('settings.vision.apiModelDesc') }}</div></div>
              <div class="setting-ctl"><input class="input w-240" v-model="visionApiModel" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiFormat') }}</div><div class="d">{{ $t('settings.vision.apiFormatDesc') }}</div></div>
              <div class="setting-ctl"><input class="input w-160" v-model="visionApiFormat" placeholder="openai" /></div>
            </div>
          </template>
          <!-- 本地模式 -->
          <template v-if="visionBackend === 'local'">
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.vision.modelFolder') }}</div><div class="d">{{ $t('settings.vision.modelFolderDesc') }}</div></div>
              <div class="setting-ctl flex-gap-8">
                <button class="btn btn-sm" @click="openFolder('vision')">{{ $t('settings.vision.openFolder') }}</button>
                <button class="btn btn-sm" @click="loadVisionModels" :title="$t('common.refresh')"><Icon name="refresh" :size="14" /></button>
              </div>
            </div>
            <div v-if="visionModelDir" class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.vision.path') }}</div></div>
              <div class="setting-ctl"><code class="text-xs text-muted">{{ visionModelDir }}</code></div>
            </div>
            <div v-if="visionModelList.length" class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.vision.selectModel') }}</div><div class="d">{{ $t('settings.vision.detected', { count: visionModelList.length }) }}</div></div>
              <div class="setting-ctl">
                <select class="input w-280" v-model="visionLocalModel">
                  <option value="">{{ $t('settings.vision.auto') }}</option>
                  <option v-for="m in visionModelList" :key="m.path" :value="m.path">{{ m.name }}{{ m.model_type ? ' (' + m.model_type + ')' : '' }}</option>
                </select>
              </div>
            </div>
            <div v-if="visionModelList.length === 0" class="setting-row">
              <div class="lbl"><div class="t">{{ $t('common.status') }}</div></div>
              <div class="setting-ctl"><span class="text-muted text-sm">{{ $t('settings.vision.noModel') }}</span></div>
            </div>
          </template>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.pet.title') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.pet.enabled') }}</div><div class="d">{{ $t('settings.pet.enabledDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="petEnabled" @change="petEnabled = !petEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.pet.sessionId') }}</div><div class="d">{{ $t('settings.pet.sessionIdDesc') }}</div></div>
            <div class="setting-ctl"><input class="input w-160" v-model="petSessionId" /></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.pet.currentState') }}</div><div class="d">{{ $t('settings.pet.currentStateDesc') }}</div></div>
            <div class="setting-ctl flex-wrap">
              <span v-for="(label, key) in petStateLabels" :key="key" class="badge" :style="{ background: 'rgba(88,166,255,.12)', color: 'var(--accent)' }">
                {{ $t(label) }} <b>{{ petState[key] ?? '-' }}</b>
              </span>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.pet.stateText') }}</div></div>
            <div class="setting-ctl"><span class="setting-hint-inline">{{ petStateText || '—' }}</span></div>
          </div>
          <div class="setting-row">
            <div class="lbl"></div>
            <div class="setting-ctl">
              <button class="btn btn-sm" @click="resetPetState">{{ $t('settings.pet.resetState') }}</button>
              <button class="btn btn-sm ml-2" @click="openFolder('pet')">{{ $t('settings.pet.openFolder') }}</button>
            </div>
          </div>
          <p class="settings-hint">{{ $t('settings.pet.hint', { hotkey: voiceHotkey, wake: voiceWakePrefix }) }}</p>
        </div>
      </div>

      <!-- ═══════════════ 记忆库 ═══════════════ -->
      <div v-if="activeTab === 'memory'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.memory.title') }}</div>
          <p class="settings-hint">{{ $t('settings.memory.hint') }}</p>
          <div class="flex-start-gap">
            <input class="input w-flex-260" v-model="newLibName" :placeholder="$t('settings.memory.newLibPlaceholder')" @keydown.enter="createLib" />
            <button class="btn btn-sm btn-primary" @click="createLib">{{ $t('settings.memory.create') }}</button>
            <button class="btn btn-sm" @click="loadMemoryLibs" :title="$t('common.refresh')"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
          </div>
          <div v-if="memoryLibs.length === 0" class="empty-state empty-padded"><p class="empty-text">{{ $t('settings.memory.empty') }}</p></div>
          <div v-for="lib in memoryLibs" :key="lib.name" class="card card-mb">
            <div class="card-header card-header-flex">
              <b>{{ lib.name }}</b>
              <span v-if="lib.current" class="badge badge-blue">{{ $t('settings.memory.current') }}</span>
              <span class="badge badge-gray">{{ $t('settings.memory.eventCount', { count: lib.event_count ?? 0 }) }}</span>
            </div>
            <div class="flex-mt">
              <button v-if="!lib.current" class="btn btn-sm" @click="switchLib(lib.name)">{{ $t('settings.memory.switchTo') }}</button>
              <button class="btn btn-sm" @click="renameLib(lib)">{{ $t('settings.memory.rename') }}</button>
              <button v-if="lib.name !== '默认'" class="btn btn-sm danger" @click="deleteLib(lib)">{{ $t('common.delete') }}</button>
            </div>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 系统 ═══════════════ -->
      <div v-if="activeTab === 'system'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.system.voiceOutput') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.system.ttsEnabled') }}</div><div class="d">{{ $t('settings.system.ttsEnabledDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="ttsEnabled" @change="ttsEnabled = !ttsEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.system.ttsBackend') }}</div><div class="d">{{ $t('settings.system.ttsBackendDesc') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button :class="{ on: ttsBackend === 'local' }" @click="ttsBackend = 'local'">{{ $t('settings.voice.local') }}</button>
                <button :class="{ on: ttsBackend === 'api' }" @click="ttsBackend = 'api'">{{ $t('settings.voice.cloud') }}</button>
              </div>
            </div>
          </div>
          <template v-if="ttsBackend === 'api'">
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiKey') }}</div></div>
              <div class="setting-ctl"><input class="input w-240" v-model="ttsApiKey" type="password" :placeholder="$t('settings.voice.apiKeyPlaceholder')" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.apiUrl') }}</div><div class="d">{{ $t('settings.vision.emptyOpenAI') }}</div></div>
              <div class="setting-ctl"><input class="input w-240" v-model="ttsApiUrl" placeholder="https://api.openai.com/v1/audio/speech" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.model.name') }}</div><div class="d">{{ $t('settings.system.ttsApiModelDesc') }}</div></div>
              <div class="setting-ctl"><input class="input w-200" v-model="ttsApiModel" /></div>
            </div>
            <div class="setting-row">
              <div class="lbl"><div class="t">{{ $t('settings.system.voice') }}</div><div class="d">{{ $t('settings.system.voiceDesc') }}</div></div>
              <div class="setting-ctl"><input class="input w-200" v-model="ttsApiVoice" /></div>
            </div>
          </template>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.system.debugLog') }}</div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.system.debugMode') }}</div><div class="d">{{ $t('settings.system.debugModeDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="debugEnabled" @change="debugEnabled = !debugEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.system.logging') }}</div><div class="d">{{ $t('settings.system.loggingDesc') }}</div></div>
            <div class="setting-ctl"><label class="toggle-switch"><input type="checkbox" :checked="loggingEnabled" @change="loggingEnabled = !loggingEnabled" /><span class="toggle-slider"></span></label></div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.system.logLevel') }}</div><div class="d">{{ $t('settings.system.logLevelDesc') }}</div></div>
            <div class="setting-ctl">
              <div class="seg">
                <button v-for="lv in ['DEBUG', 'INFO', 'WARNING', 'ERROR']" :key="lv" :class="{ on: logLevel === lv }" @click="logLevel = lv">{{ lv }}</button>
              </div>
            </div>
          </div>
          <div class="setting-row">
            <div class="lbl"><div class="t">{{ $t('settings.system.maxWorkers') }}</div><div class="d">{{ $t('settings.system.restartHint') }}</div></div>
            <div class="setting-ctl"><input class="input w-110" type="number" v-model.number="maxWorkers" /></div>
          </div>
        </div>
      </div>

      <!-- ═══════════════ 高级 ═══════════════ -->
      <div v-if="activeTab === 'advanced'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.advanced.title') }}</div>
          <p class="settings-hint">{{ $t('settings.advanced.hint') }}</p>
          <div v-if="advancedKeys.length === 0" class="empty-state empty-padded"><p class="empty-text">{{ $t('settings.advanced.empty') }}</p></div>
          <table v-else class="data-table">
            <thead><tr><th>{{ $t('common.name') }}</th><th>{{ $t('settings.advanced.currentValue') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
            <tbody>
              <tr v-for="k in advancedKeys" :key="k">
                <td><code class="text-sm">{{ k }}</code></td>
                <td><span class="mono-sm">{{ typeof configStore.config[k] === 'object' ? JSON.stringify(configStore.config[k]) : String(configStore.config[k]) }}</span></td>
                <td><button class="btn btn-sm" @click="editConfig(k, configStore.config[k])">{{ $t('common.edit') }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ═══════════════ 通用设置 ═══════════════ -->
      <div v-if="activeTab === 'general'" class="settings-section">
        <div class="settings-group">
          <label class="settings-row">
            <span class="settings-row-label">{{ $t('settings.general.language') }}</span>
            <span class="flex-1-muted">{{ $t('settings.general.languageDesc') }}</span>
            <select class="input settings-lang-select" :value="localeStore.locale" @change="localeStore.setLocale($event.target.value)">
              <option v-for="(lang, code) in $i18n.availableLocales" :key="code" :value="code">
                {{ code === 'zh' ? '简体中文' : 'English' }}
              </option>
            </select>
          </label>
          <label class="settings-row" @click.prevent="launchAtStartup = !launchAtStartup">
            <span class="settings-row-label">{{ $t('settings.general.launchAtStartup') }}</span>
            <span class="flex-1-muted">{{ $t('settings.general.launchAtStartupDesc') }}</span>
            <span class="toggle-switch"><input type="checkbox" :checked="launchAtStartup" /><span class="toggle-slider"></span></span>
          </label>
          <label class="settings-row" @click.prevent="preventSleep = !preventSleep">
            <span class="settings-row-label">{{ $t('settings.general.preventSleep') }}</span>
            <span class="flex-1-muted">{{ $t('settings.general.preventSleepDesc') }}</span>
            <span class="toggle-switch"><input type="checkbox" :checked="preventSleep" /><span class="toggle-slider"></span></span>
          </label>
          <label class="settings-row" @click.prevent="allowLocation = !allowLocation">
            <span class="settings-row-label">{{ $t('settings.general.allowLocation') }}</span>
            <span class="flex-1-muted">{{ $t('settings.general.allowLocationDesc') }}</span>
            <span class="toggle-switch"><input type="checkbox" :checked="allowLocation" /><span class="toggle-slider"></span></span>
          </label>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.general.shortcut') }}</div>
          <div class="settings-shortcut" @click="startEditShortcut">
            <input v-if="editingShortcut" ref="shortcutInput" v-model="shortcutKeys" class="input shortcut-input" @blur="saveShortcut" @keyup.enter="saveShortcut" />
            <span v-else class="shortcut-key">{{ shortcutKeys }}</span>
            <span class="shortcut-desc">{{ $t('settings.general.shortcutDesc') }}</span>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.general.dock') }}</div>
          <p class="settings-hint">{{ $t('settings.general.dockHint') }}</p>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.general.storage') }}</div>
          <div class="settings-path-row">
            <span class="settings-path">{{ storagePath }}</span>
            <button class="btn btn-sm settings-copy-btn" @click="copyPath">
              <img class="settings-logo" src="/favicon.jpg" alt="Logo" />{{ $t('common.copy') }}
            </button>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-version-row">
            <span class="settings-version-label">{{ $t('settings.general.currentVersion', { version: appVersion }) }}</span>
            <span v-if="updateInfo?.update_available" class="settings-update-badge" :title="$t('settings.general.goDownload')" @click="updateInfo.release_url && openLink(updateInfo.release_url)">{{ $t('settings.general.updateAvailable', { latest: updateInfo.latest }) }} ↗</span>
            <div class="settings-btn-row">
              <button class="btn btn-sm" @click="openDiagnostics">{{ $t('settings.general.diagnostics') }}</button>
              <button class="btn btn-sm" :disabled="checkingUpdate" @click="checkUpdates">{{ checkingUpdate ? $t('settings.general.checking') : $t('settings.general.checkUpdate') }}</button>
            </div>
          </div>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-links">
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/terms')">{{ $t('settings.general.terms') }} <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/privacy')">{{ $t('settings.general.privacy') }} <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com')">{{ $t('settings.general.website') }} <span class="link-arrow">↗</span></a>
          </div>
        </div>
        <div class="settings-copyright">Copyright 1998 – 2026 Tencent. All Rights Reserved</div>
      </div>

      <!-- ═══════════════ 授权设置 ═══════════════ -->
      <div v-if="activeTab === 'auth'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">{{ $t('settings.auth.title', { count: Object.keys(configStore.config).length }) }}</div>
          <div v-if="Object.keys(configStore.config).length === 0" class="empty-state empty-padded"><p class="empty-text">{{ $t('settings.auth.empty') }}</p></div>
          <table v-else class="data-table">
            <thead><tr><th>{{ $t('settings.auth.key') }}</th><th>{{ $t('settings.advanced.currentValue') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
            <tbody>
              <tr v-for="(v, k) in configStore.config" :key="k">
                <td><code class="text-sm">{{ k }}</code></td>
                <td><span class="config-cell-value">{{ isSecretKey(k) ? '••••••••' : (typeof v === 'object' ? JSON.stringify(v) : String(v)) }}</span></td>
                <td><button class="btn btn-sm" @click="editConfig(k, v)">{{ $t('common.edit') }}</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- ═══════════════ 关于 ═══════════════ -->
      <div v-if="activeTab === 'about'" class="settings-section">
        <div class="settings-about">
          <img class="settings-logo-lg" src="/favicon.jpg" alt="Logo" />
          <div class="settings-about-title">Cortex Agent</div>
          <div class="settings-about-version">v{{ appVersion }}</div>
          <p class="settings-about-desc">{{ $t('settings.about.desc') }}</p>
        </div>
        <div class="settings-divider"></div>
        <div class="settings-group">
          <div class="settings-links">
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/terms')">{{ $t('settings.general.terms') }} <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/privacy')">{{ $t('settings.general.privacy') }} <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com')">{{ $t('settings.general.website') }} <span class="link-arrow">↗</span></a>
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
        <span>{{ $t('settings.general.diagnostics') }}</span>
        <button class="btn btn-sm" @click="showDiag = false">✕</button>
      </div>
      <div class="modal-body">
        <div v-if="diagLoading" class="empty-state empty-padded-lg"><p class="empty-text">{{ $t('settings.diagCollecting') }}</p></div>
        <pre v-else class="diag-pre">{{ JSON.stringify(diagData, null, 2) }}</pre>
      </div>
      <div class="modal-footer">
        <button class="btn btn-sm" :disabled="diagLoading || !diagData" @click="copyDiag">{{ $t('settings.diagCopyAll') }}</button>
        <button class="btn btn-sm" @click="showDiag = false">{{ $t('common.close') }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.info-box { background: var(--bg-inset); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; font-size: 13px; color: var(--text-primary); line-height: 2; }
.info-box-title { font-weight: 600; margin-bottom: 4px; }
.info-box-sub { font-weight: 600; margin: 6px 0 0; }
.setting-group-title-hint { font-weight: 400; color: var(--text-muted); font-size: 12px; }
.flex-col { display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
.text-right { text-align: right; }
.mt-3 { margin-top: 12px; }
.flex-wrap { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.flex-start-gap { display: flex; gap: 8px; margin-bottom: 12px; }
.card-mb { margin-bottom: 10px; }
.card-header-flex { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.flex-mt { margin-top: 10px; display: flex; gap: 8px; flex-wrap: wrap; }
.mono-sm { font-family: var(--font-mono); font-size: 12px; }
.flex-1 { flex: 1; }
.setting-hint-inline { font-size: 12px; color: var(--text-secondary); }
.model-tier-block { margin: 4px 0 14px; padding: 12px 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-inset); }
.model-tier-title { font-size: 13px; font-weight: 600; color: var(--text-primary); margin-bottom: 8px; }
.muted-sm { font-size: 12px; color: var(--text-muted); }
.ml-2 { margin-left: 8px; }
.flex-1-muted { flex: 1; font-size: 12px; color: var(--text-muted); }
.key-input { flex: 1; }
.key-status { margin-top: 8px; font-size: 12px; }
.key-unconfigured { color: var(--text-muted); }

.w-50 { width: 50px; }
.w-60 { width: 60px; }
.w-80 { width: 80px; }
.w-110 { width: 110px; text-align: right; }
.w-120 { width: 120px; }
.w-160 { width: 160px; }
.w-200 { width: 200px; }
.w-220 { width: 220px; }
.w-240 { width: 240px; }
.w-280 { width: 280px; }
.w-360 { width: 360px; }
.w-flex-260 { flex: 1; max-width: 260px; }
.w-flex-220 { flex: 1; min-width: 220px; }
.flex-gap-8 { display: flex; gap: 8px; align-items: center; }

/* 依赖安装结果 */
.install-result { display: flex; flex-direction: column; gap: 4px; padding: 8px 12px; border-radius: 6px; font-size: 12px; }
.install-result.success { background: rgba(88,166,255,.08); border: 1px solid rgba(88,166,255,.2); }
.install-result.error { background: rgba(255,88,88,.08); border: 1px solid rgba(255,88,88,.2); }
.install-item { display: flex; justify-content: space-between; gap: 12px; }
.pkg-name { color: var(--text-primary); }
.pkg-status { font-weight: 500; }
.pkg-status.already_installed, .pkg-status.installed { color: #4ade80; }
.pkg-status.install_failed, .pkg-status.error { color: #f87171; }
.install-msg { margin-top: 4px; color: var(--text-secondary); font-style: italic; }
</style>
