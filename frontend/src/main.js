import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router.js'
import App from './App.vue'
import { autoDetectApiKey } from './api.js'
import { install } from './i18n/init.js'
import { useLocaleStore } from './stores/locale.js'
import '../css/theme.css'
import '../css/layout.css'
import '../css/components.css'
import './assets/hljs-dark.css'

// 启动即自动检测后端 API key（开发/测试环境后端 /config/api-key 返回 key，免手动录入）
autoDetectApiKey()

const app = createApp(App)
app.use(createPinia())
install(app)
app.use(router)

// 应用持久化的界面语言（读取 localStorage）
useLocaleStore().init()

app.mount('#app')
