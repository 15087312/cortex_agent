import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router.js'
import App from './App.vue'
import { autoDetectApiKey } from './api.js'
import '../css/theme.css'
import '../css/layout.css'
import '../css/components.css'
import './assets/hljs-dark.css'

// 启动即自动检测后端 API key（开发/测试环境后端 /config/api-key 返回 key，免手动录入）
autoDetectApiKey()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
