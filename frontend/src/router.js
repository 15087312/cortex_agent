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
  '/sessions',
  '/system',
  '/settings',
]

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', name: 'Chat', component: () => import('@/pages/Chat.vue') },
  { path: '/cortex', name: 'Cortex', component: () => import('@/pages/CortexChat.vue') },
  { path: '/dashboard', name: 'Dashboard', component: () => import('@/pages/Dashboard.vue') },
  { path: '/modules', name: 'Modules', component: () => import('@/pages/Modules.vue') },
  { path: '/memory', name: 'Memory', component: () => import('@/pages/Memory.vue') },
  { path: '/causal', name: 'Causal', component: () => import('@/pages/Causal.vue') },
  { path: '/tools', name: 'Tools', component: () => import('@/pages/Tools.vue') },
  { path: '/security', name: 'Security', component: () => import('@/pages/Security.vue') },
  { path: '/perception', name: 'Perception', component: () => import('@/pages/Perception.vue') },
  { path: '/sessions', name: 'Sessions', component: () => import('@/pages/Sessions.vue') },
  { path: '/system', name: 'System', component: () => import('@/pages/System.vue') },
  { path: '/gallery', name: 'Gallery', component: () => import('@/pages/Gallery.vue') },
  { path: '/settings', name: 'Settings', component: () => import('@/pages/Settings.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

/**
 * 全局前置守卫：对依赖 Cortex 后端（8080）的页面做健康检查
 *
 * - /chat（对话页本身可作为降级入口）和 /cortex（连 Cortex 8000）不检查
 * - 健康检查超时 3 秒，失败则重定向到 /chat 并 toast 提示
 */
router.beforeEach(async (to, from, next) => {
  const needsCheck = CORTEX_DEPENDENT_PATHS.some(p =>
    to.path === p || to.path.startsWith(p + '/')
  )

  if (!needsCheck) {
    next()
    return
  }

  try {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 3000)
    const res = await fetch('/api/health', { signal: controller.signal })
    clearTimeout(timeout)

    if (res.ok) {
      next()
      return
    }
  } catch {
    // 网络错误或超时
  }

  // 后端不可用 → 回到对话页
  try {
    const toast = useToastStore()
    toast.show('后端服务未连接，已返回对话页', 'warning')
  } catch {
    // Pinia 可能尚未初始化，忽略 toast 失败
  }
  next('/chat')
})

export default router
