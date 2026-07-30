<script setup>
import { useThemeStore } from '@/stores/theme.js'
import { useWakeLock, useGeolocation } from '@/composables/index.js'
import Sidebar from '@/components/Sidebar.vue'
import StatusBar from '@/components/StatusBar.vue'
import Toast from '@/components/Toast.vue'
import ErrorBoundary from '@/components/ErrorBoundary.vue'
import LoadingState from '@/components/LoadingState.vue'

const theme = useThemeStore()
theme.init()

// Wire system composables (self-initializing, react to config changes)
useWakeLock()
useGeolocation()
</script>

<template>
  <div class="app-shell">
    <div class="app-body">
      <Sidebar />
      <main class="main-content">
        <ErrorBoundary>
          <router-view v-slot="{ Component }">
            <transition name="page" mode="out-in">
              <Suspense>
                <component :is="Component" />
                <template #fallback>
                  <LoadingState text="加载中..." />
                </template>
              </Suspense>
            </transition>
          </router-view>
        </ErrorBoundary>
      </main>
    </div>
    <StatusBar />
  </div>
  <Toast />
</template>
