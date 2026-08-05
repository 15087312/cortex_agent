import { createRouter, createWebHashHistory } from 'vue-router'
import { useToastStore } from '@/stores/toast.js'

// 依赖 Cortex 后端（localhost:8080）的页面，访问前需健康检查
const CORTEX_DEPENDENT_PATHS = [
  '/dashboard',
  '/modules',
  '/memory',
  '/causal',
  '/tools',
  '/security',
  '/perception',
  '/system',
  '/settings',
]

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', name: 'Chat', component: () => import('@/pages/Chat.vue') },
  { path: '/dashboard', name: 'Dashboard', component: () => import('@/pages/Dashboard.vue') },
  { path: '/modules', name: 'Modules', component: () => import('@/pages/Modules.vue') },
  { path: '/memory', name: 'Memory', component: () => import('@/pages/Memory.vue') },
  { path: '/outreach', name: 'Outreach', component: () => import('@/pages/Outreach.vue') },
  { path: '/causal', name: 'Causal', component: () => import('@/pages/Causal.vue') },
  { path: '/tools', name: 'Tools', component: () => import('@/pages/Tools.vue') },
  { path: '/security', name: 'Security', component: () => import('@/pages/Security.vue') },
  { path: '/perception', name: 'Perception', component: () => import('@/pages/Perception.vue') },
  { path: '/system', name: 'System', component: () => import('@/pages/System.vue') },
  { path: '/gallery', name: 'Gallery', component: () => import('@/pages/Gallery.vue') },
  { path: '/settings', name: 'Settings', component: () => import('@/pages/Settings.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

/**
 * 全局前置守卫：对依赖 Cortex 后端（8080）的页面，先等后端完全就绪再进入。
 *
 * - /chat（对话页本身可作为降级入口）不检查
 * - 后端启动较慢（感知/视觉/embedding 初始化可达数十秒），首次打开可能未就绪。
 *   此时持续轮询 /api/health 等就绪（最多 60s），就绪后再进入目标页，
 *   不再"失败就重定向 /chat"——后端启动完，前端自然加载好，无需手动重进。
 * - 超过 60s 仍未就绪 → 提示并回到 /chat（此时多半是后端确实没启动）
 */
const HEALTH_POLL_INTERVAL = 2000
const HEALTH_POLL_TIMEOUT = 60000

async function _waitForBackend() {
  const deadline = Date.now() + HEALTH_POLL_TIMEOUT
  let hinted = false
  while (Date.now() < deadline) {
    try {
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 5000)
      const res = await fetch('/api/health', { signal: controller.signal })
      clearTimeout(timeout)
      if (res.ok) return true
    } catch {
      // 网络错误/超时，继续轮询
    }
    if (!hinted) {
      hinted = true
      try {
        const toast = useToastStore()
        toast.show('正在等待后端启动...', 'info')
      } catch {}
    }
    await new Promise(r => setTimeout(r, HEALTH_POLL_INTERVAL))
  }
  return false
}

router.beforeEach(async (to, from, next) => {
  const needsCheck = CORTEX_DEPENDENT_PATHS.some(p =>
    to.path === p || to.path.startsWith(p + '/')
  )

  if (!needsCheck) {
    next()
    return
  }

  const ready = await _waitForBackend()
  if (ready) {
    next()
    return
  }

  // 长时间未就绪 → 提示并回到对话页
  try {
    const toast = useToastStore()
    toast.show('后端服务长时间未响应，请确认已启动后端', 'error')
  } catch {
    // Pinia 可能尚未初始化，忽略 toast 失败
  }
  next('/chat')
})

export default router
