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
      { route: '/dashboard', icon: '📊', label: '仪表盘' },
      { route: '/chat', icon: '💬', label: '对话' },
      { route: '/cortex', icon: '🧠', label: 'Cortex' },
    ]
  },
  {
    label: '管理',
    items: [
      { route: '/modules', icon: '🧩', label: '模块' },
      { route: '/memory', icon: '📝', label: '记忆' },
      { route: '/causal', icon: '🔗', label: '因果图' },
      { route: '/tools', icon: '🔧', label: '工具' },
      { route: '/security', icon: '🛡', label: '安全' },
      { route: '/perception', icon: '👁', label: '感知' },
    ]
  },
  {
    label: '系统',
    items: [
      { route: '/sessions', icon: '📋', label: '会话' },
      { route: '/system', icon: 'ℹ', label: '系统' },
      { route: '/settings', icon: '⚙', label: '设置' },
    ]
  }
]

function isActive(itemRoute) {
  return route.path === itemRoute || route.path.startsWith(itemRoute + '/')
}
</script>

<template>
  <nav class="sidebar">
    <div class="sidebar-header"><div class="logo">C</div><span>Cortex Agent</span></div>
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
          <span class="icon">{{ item.icon }}</span>
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
