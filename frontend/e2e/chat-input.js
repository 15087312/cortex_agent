// Playwright 真实浏览器冒烟用的最小挂载页：只加载全局 CSS + ChatInput。
import { createApp, h, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import ChatInput from '@/components/ChatInput.vue'
import zh from '@/i18n/locales/zh.js'
import '../css/theme.css'
import '../css/layout.css'
import '../css/components.css'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const app = createApp({
  setup() {
    const last = ref('')
    const onSend = (payload) => { last.value = JSON.stringify(payload) }
    const onToast = (t) => { last.value = 'TOAST:' + t.message }
    return () => h('div', { style: 'padding:24px;max-width:900px' }, [
      h(ChatInput, { onSend, onToast }),
      h('pre', { id: 'last-payload' }, last.value),
    ])
  },
})
app.use(i18n)
app.mount('#app')
