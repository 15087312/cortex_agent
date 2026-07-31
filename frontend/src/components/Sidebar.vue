<script setup>
import { useRouter, useRoute } from 'vue-router'
import { useThemeStore } from '@/stores/theme.js'

const router = useRouter()
const route = useRoute()
const theme = useThemeStore()
const appVersion = __APP_VERSION__

const navSections = [
  {
    label: '概览',
    items: [
      { route: '/dashboard', label: '仪表盘' },
      { route: '/chat', label: '对话' },
      { route: '/cortex', label: 'Cortex' },
    ]
  },
  {
    label: '管理',
    items: [
      { route: '/modules', label: '模块' },
      { route: '/memory', label: '记忆' },
      { route: '/causal', label: '因果图' },
      { route: '/tools', label: '工具' },
      { route: '/gallery', label: '图库' },
      { route: '/security', label: '安全' },
      { route: '/perception', label: '感知' },
    ]
  },
  {
    label: '系统',
    items: [
      { route: '/sessions', label: '会话' },
      { route: '/system', label: '系统' },
      { route: '/settings', label: '设置' },
    ]
  }
]

function isActive(itemRoute) {
  return route.path === itemRoute || route.path.startsWith(itemRoute + '/')
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
          @click="router.push(item.route)"
        >
          <span>{{ item.label }}</span>
        </div>
      </template>
    </div>
    <div class="sidebar-footer">
      <span class="version">v{{ appVersion }}</span>
      <button class="theme-toggle" @click="theme.toggle()" title="切换主题">
        {{ theme.isDark ? '☀️' : '🌙' }}
      </button>
    </div>
  </nav>
</template>
