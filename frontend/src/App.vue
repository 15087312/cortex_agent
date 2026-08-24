<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useThemeStore } from '@/stores/theme.js'
import { useConfigStore } from '@/stores/config.js'
import { useWakeLock, useGeolocation } from '@/composables/index.js'
import { dialogState, resolveDialog } from '@/composables/useDialog.js'
import { useToastStore } from '@/stores/toast.js'
import { useI18n } from 'vue-i18n'
import Sidebar from '@/components/Sidebar.vue'
import StatusBar from '@/components/StatusBar.vue'
import Toast from '@/components/Toast.vue'
import DialogHost from '@/components/DialogHost.vue'
import ErrorBoundary from '@/components/ErrorBoundary.vue'
import LoadingState from '@/components/LoadingState.vue'

const theme = useThemeStore()
theme.init()
const configStore = useConfigStore()

// Wire system composables (self-initializing, react to config changes)
useWakeLock()
useGeolocation()

// ── 全局键盘快捷键 ──
// 优先级：用户配置的 shortcut_keys（设置 → 通用设置 → 启动快捷键）> 内置默认
// 解析 '⌥ + T' / 'Cmd+K' / 'Ctrl+Shift+P' 等格式，真实消费后端配置（非摆设）
const router = useRouter()
const toast = useToastStore()
const { t } = useI18n()

function _isTyping(el) {
  const t = (el && el.tagName) || ''
  return t === 'INPUT' || t === 'TEXTAREA' || (el && el.isContentEditable)
}

function parseShortcut(str) {
  const parts = String(str || '').split('+').map((s) => s.trim()).filter(Boolean)
  const sc = { ctrl: false, meta: false, alt: false, shift: false, key: '' }
  for (const p of parts) {
    const l = p.toLowerCase()
    if (['⌘', 'cmd', 'command', 'meta', 'win', '⊞'].includes(l)) sc.meta = true
    else if (['⌥', 'alt', 'option', 'opt'].includes(l)) sc.alt = true
    else if (['⇧', 'shift'].includes(l)) sc.shift = true
    else if (['⌃', 'ctrl', 'control'].includes(l)) sc.ctrl = true
    else if (l) sc.key = l
  }
  return sc
}

function shortcutMatches(e, sc) {
  if (!sc.key) return false
  if (e.ctrlKey !== sc.ctrl || e.metaKey !== sc.meta || e.altKey !== sc.alt || e.shiftKey !== sc.shift) return false
  return e.key.toLowerCase() === sc.key
}

// 聚焦对话输入框（真实生效动作）
function _focusChat() {
  router.push('/chat')
  window.dispatchEvent(new CustomEvent('cortex-focus-input'))
}

function onKeydown(e) {
  // 1) 用户配置的快捷键（后端持久化，实时读取）
  const cfgShortcut = configStore.config?.shortcut_keys || configStore.config?.SHORTCUT_KEYS
  if (cfgShortcut) {
    const sc = parseShortcut(cfgShortcut)
    if (sc.key && shortcutMatches(e, sc)) {
      e.preventDefault()
      _focusChat()
      return
    }
  }
  // 2) 内置默认：Cmd/Ctrl+K 聚焦输入
  const mod = e.ctrlKey || e.metaKey
  if (mod && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    _focusChat()
    return
  }
  if (e.key === 'Escape') {
    if (dialogState().visible) { resolveDialog(null); return }
    return
  }
  if (e.key === '/' && !_isTyping(e.target)) {
    e.preventDefault()
    window.dispatchEvent(new CustomEvent('cortex-focus-input'))
    return
  }
  if (e.key === '?' && !_isTyping(e.target)) {
    e.preventDefault()
    toast.show(t('shortcut.hint'), 'info')
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <div class="app-shell">
    <div class="app-body">
      <Sidebar />
      <main class="main-content">
        <ErrorBoundary>
          <div class="page-view">
            <router-view v-slot="{ Component }">
              <!-- 缓存 Chat：切换页面不销毁，保持 WS 监听与思考状态。
                   不用 <transition mode="out-in">：与 KeepAlive 组合在从设置页切回时
                   会丢失缓存的 Chat（空白页，需再点一次才恢复）——Vue 3.5 已知问题 -->
              <KeepAlive :include="['Chat']">
                <Suspense>
                  <component :is="Component" />
                  <template #fallback>
                    <LoadingState :text="$t('app.loading')" />
                  </template>
                </Suspense>
              </KeepAlive>
            </router-view>
          </div>
        </ErrorBoundary>
      </main>
    </div>
    <StatusBar />
  </div>
  <Toast />
  <DialogHost />
</template>
