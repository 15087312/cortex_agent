<script setup>
import { ref, computed, onMounted } from 'vue'
import { getApiKey, setApiKey, endpoints } from '@/api.js'
import { useConfigStore } from '@/stores/config.js'
import { useToastStore } from '@/stores/toast.js'

const toast = useToastStore()
const configStore = useConfigStore()
const appVersion = __APP_VERSION__

/* ── Tabs ── */
const tabs = ['通用设置', '人设管理', '授权设置', '关于']
const activeTab = ref('通用设置')

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

/** Persist a key-value to backend config with optimistic UI update */
async function _save(k, v) {
  // Optimistic local update so toggle responds instantly
  configStore.$patch((state) => { state.config = { ...state.config, [k]: v } })
  try { await configStore.updateConfig(k, v) } catch (e) {
    toast.show('保存失败: ' + (e.body?.error?.message || e.status), 'error')
  }
}

/* ── General toggles (computed → reads from configStore, writes via API) ── */
const launchAtStartup = computed({
  get: () => _bool(configStore.config[CK.launchAtStartup], true),
  set: (v) => _save(CK.launchAtStartup, v),
})
const preventSleep = computed({
  get: () => _bool(configStore.config[CK.preventSleep], false),
  set: (v) => _save(CK.preventSleep, v),
})
const showFilename = computed({
  get: () => _bool(configStore.config[CK.showFilename], false),
  set: (v) => _save(CK.showFilename, v),
})
const allowLocation = computed({
  get: () => _bool(configStore.config[CK.allowLocation], false),
  set: (v) => _save(CK.allowLocation, v),
})

/* ── Shortcut ── */
const shortcutKeys = computed({
  get: () => _str(configStore.config[CK.shortcutKeys], '⌥ + T'),
  set: (v) => _save(CK.shortcutKeys, v),
})
const editingShortcut = ref(false)
function startEditShortcut() { editingShortcut.value = true }
function saveShortcut() { editingShortcut.value = false }

/* ── File path ── */
const storagePath = ref('')
function copyPath() {
  navigator.clipboard.writeText(storagePath.value).then(() => toast.show('路径已复制', 'success'))
}

/* ── Diagnostics modal ── */
const showDiag = ref(false)
const diagData = ref(null)
const diagLoading = ref(false)

async function openDiagnostics() {
  showDiag.value = true
  diagLoading.value = true
  diagData.value = null
  try {
    const [sys, health] = await Promise.all([
      endpoints.systemInfo().catch(() => null),
      endpoints.health().catch(() => null),
    ])
    const models = await endpoints.thinkingStatus().catch(() => null)
    diagData.value = {
      appVersion,
      timestamp: new Date().toISOString(),
      system: sys?.data || sys || {},
      health: health?.data || health || {},
      models: models?.data?.models || models?.models || {},
      configKeys: Object.keys(configStore.config),
      navigator: {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        language: navigator.language,
      },
    }
  } catch (e) {
    diagData.value = { error: String(e) }
  }
  diagLoading.value = false
}

function copyDiag() {
  const text = JSON.stringify(diagData.value, null, 2)
  navigator.clipboard.writeText(text).then(() => toast.show('诊断日志已复制', 'success'))
}

/* ── Check updates ── */
const checkingUpdate = ref(false)
async function checkUpdates() {
  checkingUpdate.value = true
  try {
    const health = await endpoints.health()
    const serverVersion = health?.data?.version || health?.version || null
    if (serverVersion) {
      toast.show(serverVersion === appVersion
        ? '当前已是最新版本 v' + appVersion
        : '发现新版本: ' + serverVersion + '（当前 v' + appVersion + '）',
        serverVersion === appVersion ? 'success' : 'info')
    } else {
      const sys = await endpoints.systemInfo()
      const v = sys?.data?.version || sys?.version || ''
      toast.show(v ? '服务器版本: ' + v : '当前版本 v' + appVersion, 'success')
    }
  } catch {
    toast.show('无法连接后端服务', 'error')
  }
  checkingUpdate.value = false
}

/* ── External links ── */
function openLink(url) { window.open(url, '_blank') }

/* ── Auth ── */
const keyInput = ref(getApiKey())
function saveKey() { setApiKey(keyInput.value); toast.show(keyInput.value ? '已保存' : '已清除', 'success') }
function clearKey() { keyInput.value = ''; setApiKey(''); toast.show('已清除', 'success') }

/* ── Config table ── */
async function editConfig(k, v) {
  const nv = prompt('编辑 ' + k, String(v)); if (nv === null) return
  let val = nv; if (val === 'true') val = true; else if (val === 'false') val = false; else if (!isNaN(val) && val.trim() !== '') val = Number(val)
  try { await configStore.updateConfig(k, val); toast.show(k + ' 已更新', 'success') } catch (e) { toast.show('更新失败: ' + (e.body?.error?.message || e.status), 'error') }
}

/* ── Init ── */
onMounted(async () => {
  await configStore.loadConfig()
  await configStore.loadModelStatus()
  // Resolve storage path: config first, then system info, then OS fallback
  const cfgPath = _str(configStore.config[CK.storagePath], '')
  if (cfgPath) { storagePath.value = cfgPath; return }
  try {
    const info = await endpoints.systemInfo()
    storagePath.value = info?.data?.storage_path || info?.storage_path || ''
  } catch {}
  if (!storagePath.value) {
    const p = navigator.platform || ''
    storagePath.value = p.includes('Mac')
      ? '~/Library/Application Support/com.cortexagent'
      : p.includes('Win')
        ? '%APPDATA%\\CortexAgent'
        : '~/.cortexagent'
  }
})
</script>

<template>
  <div class="settings-layout">
    <!-- Left Tab Sidebar -->
    <div class="settings-sidebar">
      <div
        v-for="tab in tabs" :key="tab"
        class="settings-tab"
        :class="{ active: activeTab === tab }"
        @click="activeTab = tab"
      >{{ tab }}</div>
    </div>

    <!-- Right Content Panel -->
    <div class="settings-content">

      <!-- ═══════════════ 通用设置 ═══════════════ -->
      <div v-if="activeTab === '通用设置'" class="settings-section">
        <!-- Toggle Switches -->
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

        <!-- Shortcut -->
        <div class="settings-group">
          <div class="settings-group-title">启动快捷键</div>
          <div class="settings-shortcut" @click="startEditShortcut">
            <input
              v-if="editingShortcut"
              ref="shortcutInput"
              v-model="shortcutKeys"
              class="input shortcut-input"
              @blur="saveShortcut"
              @keyup.enter="saveShortcut"
            />
            <span v-else class="shortcut-key">{{ shortcutKeys }}</span>
            <span class="shortcut-desc">点击快捷键可编辑 — 通过快捷键快速唤起应用窗口</span>
          </div>
        </div>

        <div class="settings-divider"></div>

        <!-- Dock -->
        <div class="settings-group">
          <div class="settings-group-title">保留在 Dock 栏</div>
          <p class="settings-hint">右键程序坞中 Cortex Agent 图标 &gt; 选项 &gt; 在程序坞中保留</p>
        </div>

        <div class="settings-divider"></div>

        <!-- File Path -->
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

        <!-- Version & Actions -->
        <div class="settings-group">
          <div class="settings-version-row">
            <span class="settings-version-label">当前版本：v{{ appVersion }}</span>
            <div class="settings-btn-row">
              <button class="btn btn-sm" @click="openDiagnostics">诊断日志</button>
              <button class="btn btn-sm" :disabled="checkingUpdate" @click="checkUpdates">
                {{ checkingUpdate ? '检查中…' : '检查更新' }}
              </button>
            </div>
          </div>
        </div>

        <div class="settings-divider"></div>

        <!-- Links -->
        <div class="settings-group">
          <div class="settings-links">
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/terms')">用户协议 <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com/privacy')">隐私政策 <span class="link-arrow">↗</span></a>
            <a class="settings-link" href="#" @click.prevent="openLink('https://cortexagent.com')">进入官网 <span class="link-arrow">↗</span></a>
          </div>
        </div>

        <div class="settings-copyright">
          Copyright 1998 – 2026 Tencent. All Rights Reserved
        </div>
      </div>

      <!-- ═══════════════ 人设管理 ═══════════════ -->
      <div v-if="activeTab === '人设管理'" class="settings-section">
        <div class="settings-group">
          <div class="settings-group-title">模型状态</div>
          <table class="data-table">
            <thead><tr><th>模型</th><th>角色</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="(bk, lbl) in { big: '总指挥', medium: '主管', small: '专家' }" :key="lbl">
                <td>{{ lbl }}</td>
                <td style="color:var(--text-muted)">{{ bk === 'big' ? 'large' : bk === 'medium' ? 'supervisor' : 'expert' }}</td>
                <td>
                  <span class="badge" :class="configStore.modelStatus[bk] || configStore.modelStatus[bk === 'big' ? 'large' : bk === 'medium' ? 'supervisor' : 'expert'] ? 'badge-green' : 'badge-red'">
                    {{ configStore.modelStatus[bk] || configStore.modelStatus[bk === 'big' ? 'large' : bk === 'medium' ? 'supervisor' : 'expert'] ? '可用' : '不可用' }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
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
            <span v-if="getApiKey()" class="badge badge-green">✓ 已配置</span>
            <span v-else style="color:var(--text-muted)">未配置</span>
          </div>
        </div>

        <div class="settings-divider"></div>

        <div class="settings-group">
          <div class="settings-group-title">运行时配置 ({{ Object.keys(configStore.config).length }} 项)</div>
          <div v-if="Object.keys(configStore.config).length === 0" class="empty-state" style="padding:24px">
            <p class="empty-text">暂无配置项</p>
          </div>
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
        <div class="settings-copyright">
          Copyright 1998 – 2026 Tencent. All Rights Reserved
        </div>
      </div>

    </div>
  </div>

  <!-- ═══════════════ Diagnostics Modal ═══════════════ -->
  <div v-if="showDiag" class="modal-overlay" @click.self="showDiag = false">
    <div class="modal diag-modal">
      <div class="modal-header">
        <span>诊断日志</span>
        <button class="btn btn-sm" @click="showDiag = false">✕</button>
      </div>
      <div class="modal-body">
        <div v-if="diagLoading" class="empty-state" style="padding:32px">
          <p class="empty-text">正在收集诊断信息…</p>
        </div>
        <pre v-else class="diag-pre">{{ JSON.stringify(diagData, null, 2) }}</pre>
      </div>
      <div class="modal-footer">
        <button class="btn btn-sm" :disabled="diagLoading || !diagData" @click="copyDiag">复制全部</button>
        <button class="btn btn-sm" @click="showDiag = false">关闭</button>
      </div>
    </div>
  </div>
</template>
