<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useI18n } from 'vue-i18n'
import Icon from '@/components/Icon.vue'

const { t } = useI18n()
const toast = useToastStore()
const mods = ref([])
const total = ref(0)
const withApi = ref(0)
const withCore = ref(0)

async function loadData() {
  try {
    const r = await endpoints.modules()
    mods.value = (r.data.modules || []).map(m => ({ name: m.name || m, has_api: m.has_api, has_core: m.has_core, status: m.status || '' }))
    total.value = mods.value.length
    withApi.value = r.data.with_api || 0
    withCore.value = r.data.with_core || 0
  } catch {}
}

onMounted(loadData)

async function refreshMod(name) {
  try {
    await endpoints.refreshModule(name)
    toast.show(t('modules.refreshed', { name }), 'success')
    await loadData()
  } catch { toast.show(t('common.refreshFailed'), 'error') }
}
function statusClass(s) { return s === 'healthy' ? 'badge-green' : s === 'degraded' ? 'badge-yellow' : 'badge-red' }
function statusLabel(m) {
  if (m.status === 'healthy') return t('modules.normal')
  if (m.status === 'degraded') return t('modules.degraded')
  return m.status || t('modules.unknown')
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2>{{ $t('modules.title') }}</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ total }}</div><div class="stat-label">{{ $t('modules.total') }}</div></div>
        <div class="stat-card"><div class="stat-value">{{ withApi }}</div><div class="stat-label">{{ $t('modules.withApi') }}</div></div>
        <div class="stat-card"><div class="stat-value">{{ withCore }}</div><div class="stat-label">{{ $t('modules.withCore') }}</div></div>
      </div>
      <div class="card">
        <div class="card-header">{{ $t('modules.list') }} ({{ total }})</div>
        <table class="data-table" v-if="mods.length > 0">
          <thead><tr><th>{{ $t('modules.moduleName') }}</th><th>{{ $t('modules.api') }}</th><th>{{ $t('modules.core') }}</th><th>{{ $t('common.status') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
          <tbody>
            <tr v-for="m in mods" :key="m.name">
              <td><strong>{{ m.name }}</strong></td>
              <td><span class="badge" :class="m.has_api ? 'badge-green' : 'badge-gray'"><Icon :name="m.has_api ? 'check' : 'x'" :size="12" /> {{ m.has_api ? $t('common.has') : $t('common.none') }}</span></td>
              <td><span class="badge" :class="m.has_core ? 'badge-green' : 'badge-gray'"><Icon :name="m.has_core ? 'check' : 'x'" :size="12" /> {{ m.has_core ? $t('common.has') : $t('common.none') }}</span></td>
              <td><span class="badge" :class="statusClass(m.status)">{{ statusLabel(m) }}</span></td>
              <td><button class="btn btn-sm" @click="refreshMod(m.name)">{{ $t('common.refresh') }}</button></td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-state"><span class="empty-icon"><Icon name="inbox" :size="20" /></span><p class="empty-text">{{ $t('modules.loadEmpty') }}</p></div>
      </div>
    </div>
  </div>
</template>
