<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useThemeStore } from '@/stores/theme.js'
import Icon from '@/components/Icon.vue'

const router = useRouter()
const route = useRoute()
const theme = useThemeStore()
const appVersion = __APP_VERSION__

const navSections = [
  {
    label: '我的',
    items: [
      { route: '/dashboard', label: '仪表盘', icon: 'dashboard' },
      { route: '/chat', label: '对话', icon: 'message' },
      { route: '/memory', label: '记忆', icon: 'file' },
      { route: '/tasks', label: '定时任务', icon: 'circle' },
      { route: '/settings', label: '设置', icon: 'settings' },
    ]
  },
]

// 开发者分组（默认折叠）
const devSections = [
  {
    label: '开发者',
    items: [
      { route: '/modules', label: '模块', icon: 'puzzle' },
      { route: '/causal', label: '因果图', icon: 'network' },
      { route: '/orchestration', label: '编排', icon: 'settings' },
      { route: '/security', label: '安全', icon: 'shield' },
      { route: '/perception', label: '感知', icon: 'eye' },
      { route: '/system', label: '系统', icon: 'info' },
    ]
  },
]
const devExpanded = ref(false)

function isActive(itemRoute) {
  return route.path === itemRoute || route.path.startsWith(itemRoute + '/')
}
function navTo(item) {
  router.push(item.route)
}
</script>

<template>
  <nav class="sidebar">
    <div class="sidebar-header"><img class="logo" src="/favicon.jpg" alt="Logo" /><span>Cortex Agent</span></div>
    <div class="sidebar-nav">
      <template v-for="section in navSections" :key="section.label">
        <div class="nav-section">{{ section.label }}</div>
        <div
          v-for="item in section.items"
          :key="item.route"
          class="nav-item"
          :class="{ active: isActive(item.route) }"
          @click="navTo(item)"
        >
          <span class="nav-item-icon"><Icon :name="item.icon" :size="16" /></span>
          <span>{{ item.label }}</span>
        </div>
      </template>

      <!-- 开发者分组（默认折叠） -->
      <div class="nav-section dev-toggle" @click="devExpanded = !devExpanded">
        <span>{{ devSections[0].label }}</span>
        <Icon :name="devExpanded ? 'down' : 'right'" :size="12" />
      </div>
      <template v-if="devExpanded">
        <div
          v-for="item in devSections[0].items"
          :key="item.route"
          class="nav-item"
          :class="{ active: isActive(item.route) }"
          @click="navTo(item)"
        >
          <span class="nav-item-icon"><Icon :name="item.icon" :size="16" /></span>
          <span>{{ item.label }}</span>
        </div>
      </template>
    </div>
    <div class="sidebar-footer">
      <span class="version">v{{ appVersion }}</span>
      <button class="theme-toggle" @click="theme.toggle()" title="切换主题">
        <Icon :name="theme.isDark ? 'sun' : 'moon'" :size="16" />
      </button>
    </div>
  </nav>
</template>
