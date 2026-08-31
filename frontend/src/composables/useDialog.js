import { reactive } from 'vue'
import { useI18n } from '@/i18n/init.js'

/**
 * 非阻塞 confirm / prompt（替代原生 confirm/prompt）
 *
 * 原生 confirm/prompt 在 Qt WebEngine 里会阻塞整个 JS 引擎：
 * 对话框未关闭时所有点击都无响应，导致"点了没反应"。
 * 这里用页内弹层 + Promise 实现，永不阻塞。
 */
const state = reactive({
  visible: false,
  type: 'confirm', // 'confirm' | 'prompt'
  title: '',
  message: '',
  defaultValue: '',
  _resolve: null,
})

export function dialogState() {
  return state
}

export function useConfirm() {
  return (message, title = '') => new Promise((resolve) => {
    state.type = 'confirm'
    state.message = message
    state.title = title || useI18n().global.t('common.confirmTitle')
    state._resolve = resolve
    state.visible = true
  })
}

export function usePrompt() {
  return (message, defaultValue = '') => new Promise((resolve) => {
    state.type = 'prompt'
    state.message = message
    state.title = ''
    state.defaultValue = defaultValue
    state._resolve = resolve
    state.visible = true
  })
}

export function resolveDialog(value) {
  state.visible = false
  if (state._resolve) {
    const fn = state._resolve
    state._resolve = null
    fn(value)
  }
}
