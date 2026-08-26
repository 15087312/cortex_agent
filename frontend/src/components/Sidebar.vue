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
    labelKey: 'nav.mine',
    items: [
      { route: '/dashboard', labelKey: 'nav.dashboard', icon: 'dashboard' },
      { route: '/chat', labelKey: 'nav.chat', icon: 'message' },
      { route: '/memory', labelKey: 'nav.memory', icon: 'file' },
      { route: '/tasks', labelKey: 'nav.tasks', icon: 'circle' },
      { route: '/settings', labelKey: 'nav.settings', icon: 'settings' },
    ]
  },
]

// 开发者分组（默认折叠）
const devSections = [
  {
    labelKey: 'nav.developer',
    items: [
      { route: '/modules', labelKey: 'nav.modules', icon: 'puzzle' },
      { route: '/causal', labelKey: 'nav.causal', icon: 'network' },
      { route: '/orchestration', labelKey: 'nav.orchestration', icon: 'settings' },
      { route: '/security', labelKey: 'nav.security', icon: 'shield' },
      { route: '/perception', labelKey: 'nav.perception', icon: 'eye' },
      { route: '/system', labelKey: 'nav.system', icon: 'info' },
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
    <div class="sidebar-header"><img class="logo" src="/favicon.jpg" alt="Logo" /><span>cortex</span></div>
    <div class="sidebar-nav">
      <template v-for="section in navSections" :key="section.labelKey">
        <div class="nav-section">{{ $t(section.labelKey) }}</div>
        <div
          v-for="item in section.items"
          :key="item.route"
          class="nav-item"
          :class="{ active: isActive(item.route) }"
          @click="navTo(item)"
        >
          <span class="nav-item-icon"><Icon :name="item.icon" :size="16" /></span>
          <span>{{ $t(item.labelKey) }}</span>
        </div>
      </template>

      <!-- 开发者分组（默认折叠） -->
      <div class="nav-section dev-toggle" @click="devExpanded = !devExpanded">
        <span>{{ $t(devSections[0].labelKey) }}</span>
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
          <span>{{ $t(item.labelKey) }}</span>
        </div>
      </template>
    </div>
    <div class="sidebar-footer">
      <span class="version">v{{ appVersion }}</span>
      <button class="theme-toggle" @click="theme.toggle()" :title="$t('nav.toggleTheme')">
        <Icon :name="theme.isDark ? 'sun' : 'moon'" :size="16" />
      </button>
    </div>
  </nav>
</template>
