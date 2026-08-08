<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useThemeStore } from '@/stores/theme.js'
import { useWakeLock, useGeolocation } from '@/composables/index.js'
import { dialogState, resolveDialog } from '@/composables/useDialog.js'
import { useToastStore } from '@/stores/toast.js'
import Sidebar from '@/components/Sidebar.vue'
import StatusBar from '@/components/StatusBar.vue'
import Toast from '@/components/Toast.vue'
import DialogHost from '@/components/DialogHost.vue'
import ErrorBoundary from '@/components/ErrorBoundary.vue'
import LoadingState from '@/components/LoadingState.vue'

const theme = useThemeStore()
theme.init()

// Wire system composables (self-initializing, react to config changes)
useWakeLock()
useGeolocation()

// ── 全局键盘快捷键（对齐 js/app.js setupKeyboard） ──
const router = useRouter()
const toast = useToastStore()

function _isTyping(el) {
  const t = (el && el.tagName) || ''
  return t === 'INPUT' || t === 'TEXTAREA' || (el && el.isContentEditable)
}

function onKeydown(e) {
  const mod = e.ctrlKey || e.metaKey
  if (mod && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    router.push('/chat')
    window.dispatchEvent(new CustomEvent('cortex-focus-input'))
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
    toast.show('快捷键: Cmd/Ctrl+K 或 / 聚焦输入 · Esc 取消', 'info')
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
                    <LoadingState text="加载中..." />
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
