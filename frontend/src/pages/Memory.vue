<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm } from '@/composables/useDialog.js'
import Modal from '@/components/Modal.vue'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'
import { useI18n } from 'vue-i18n'

const toast = useToastStore()
const confirm = useConfirm()
const { t } = useI18n()
const events = ref([])
const total = ref(0)
const filter = ref({ type: '', keyword: '' })
const showCreate = ref(false)
const newEvent = ref({ fact: '', keywords: '', importance: 0.5, event_type: 'fact' })
const detailEvent = ref(null)
const viewMode = ref('list')

const groupedByDate = computed(() => {
  const groups = {}
  for (const e of events.value) {
    const date = (e.time || '').slice(0, 10) || t('memory.unknownDate')
    if (!groups[date]) groups[date] = []
    groups[date].push(e)
  }
  return Object.entries(groups).sort((a, b) => b[0].localeCompare(a[0]))
})

onMounted(loadData)

async function loadData() {
  try { const r = await endpoints.memoryEvents(50, filter.value.type, filter.value.keyword); events.value = r.data.events || []; total.value = r.data.total || 0 } catch {}
}
async function handleDelete(id) {
  if (!(await confirm(t('memory.confirmDelete')))) return
  try { await endpoints.deleteMemoryEvent(id); toast.show(t('memory.deleted'), 'success'); loadData() } catch { toast.show(t('memory.deleteFailed'), 'error') }
}
async function handleClear() {
  if (!(await confirm(t('memory.confirmClearAll')))) return
  try { await endpoints.clearMemory(); toast.show(t('memory.cleared'), 'success'); loadData() } catch { toast.show(t('memory.clearFailed'), 'error') }
}
async function handleCreate() { if (!newEvent.value.fact) { toast.show(t('memory.enterContent'), 'error'); return }; try { await endpoints.createMemoryEvent({ fact: newEvent.value.fact, keywords: newEvent.value.keywords, importance: Number(newEvent.value.importance) || 0.5, event_type: newEvent.value.event_type }); toast.show(t('memory.created'), 'success'); showCreate.value = false; loadData() } catch { toast.show(t('memory.createFailed'), 'error') } }
async function handleDetail(id) { try { const r = await endpoints.memoryEvent(id); detailEvent.value = r.data } catch { toast.show(t('memory.loadFailed'), 'error') } }
function typeBadgeClass(t) { return t === 'fact' ? 'badge-blue' : t === 'thought' ? 'badge-green' : t === 'strategy' ? 'badge-yellow' : 'badge-gray' }
function starRating(v) { const s = Math.round(v * 5); return '★'.repeat(s) + '☆'.repeat(5 - s) }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>{{ $t('memory.title') }}</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> {{ $t('common.refresh') }}</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ total }}</div><div class="stat-label">{{ $t('memory.totalEvents') }}</div></div>
        <div class="stat-card"><div class="stat-value">{{ events.length }}</div><div class="stat-label">{{ $t('memory.currentShowing') }}</div></div>
        <div class="stat-card"><button class="btn btn-danger btn-sm" @click="handleClear">{{ $t('memory.clearMemory') }}</button><div class="stat-label">{{ $t('memory.irreversible') }}</div></div>
      </div>
      <div class="search-bar">
        <input class="input search-input" v-model="filter.keyword" :placeholder="$t('memory.searchPlaceholder')" @keyup.enter="loadData" />
        <select class="input" v-model="filter.type"><option value="">{{ $t('common.all') }}</option><option value="fact">fact</option><option value="thought">thought</option><option value="strategy">strategy</option><option value="emotion">emotion</option></select>
        <button class="btn btn-primary btn-sm" @click="loadData">{{ $t('common.search') }}</button>
        <button class="btn btn-sm" @click="showCreate = true">{{ $t('memory.newBtn') }}</button>
      </div>
      <div class="card">
        <div class="card-header card-header-flex">
          <span>{{ $t('memory.listTitle', { total }) }}</span>
          <div class="view-toggle">
            <button class="btn btn-sm" :class="{ 'btn-primary': viewMode === 'list' }" @click="viewMode = 'list'">{{ $t('memory.listView') }}</button>
            <button class="btn btn-sm" :class="{ 'btn-primary': viewMode === 'timeline' }" @click="viewMode = 'timeline'">{{ $t('memory.timelineView') }}</button>
          </div>
        </div>
        <!-- 列表视图 -->
        <table class="data-table" v-if="events.length > 0 && viewMode === 'list'">
          <thead><tr><th>{{ $t('common.type') }}</th><th>{{ $t('memory.content') }}</th><th>{{ $t('memory.importance') }}</th><th>{{ $t('common.time') }}</th><th>{{ $t('common.action') }}</th></tr></thead>
          <tbody>
            <tr v-for="e in events" :key="e.id">
              <td><span class="badge" :class="typeBadgeClass(e.type)">{{ e.type }}</span></td>
              <td><span class="mem-content-ellipsis">{{ e.fact || '' }}</span></td>
              <td><span class="star-rating star-text">{{ starRating(e.importance || 0) }}</span> <span class="importance-text">{{ (e.importance||0).toFixed(2) }}</span></td>
              <td>{{ formatTime(e.time) }}</td>
              <td><button class="btn btn-sm" @click="handleDetail(e.id)">{{ $t('common.details') }}</button> <button class="btn btn-sm btn-danger" @click="handleDelete(e.id)">{{ $t('common.delete') }}</button></td>
            </tr>
          </tbody>
        </table>
        <!-- 时间线视图 -->
        <div v-else-if="events.length > 0 && viewMode === 'timeline'" class="memory-timeline">
          <div v-for="(group, gi) in groupedByDate" :key="gi" class="memory-timeline-group">
            <div class="memory-timeline-date">{{ group[0] }}</div>
            <div v-for="e in group[1]" :key="e.id" class="memory-timeline-item" @click="handleDetail(e.id)">
              <span class="badge" :class="typeBadgeClass(e.type)">{{ e.type }}</span>
              <span class="memory-timeline-fact">{{ e.fact || '' }}</span>
              <span class="timeline-time">{{ formatTime(e.time) }}</span>
              <span class="timeline-spacer"></span>
              <button class="btn btn-sm btn-danger" @click.stop="handleDelete(e.id)"><Icon name="trash" :size="13" /></button>
            </div>
          </div>
        </div>
        <div v-else class="empty-state timeline-empty"><span class="empty-icon"><Icon name="inbox" :size="20" /></span><p class="empty-text">{{ $t('memory.empty') }}</p></div>
      </div>
      <Modal v-if="showCreate" :title="$t('memory.createTitle')" @close="showCreate = false">
        <div class="modal-form">
          <div><label class="form-label w-full">{{ $t('common.type') }}</label><select class="input" v-model="newEvent.event_type" ><option value="fact">fact</option><option value="thought">thought</option><option value="strategy">strategy</option><option value="emotion">emotion</option></select></div>
          <div><label class="form-label w-full min-h-60">{{ $t('memory.content') }} *</label><textarea class="input" v-model="newEvent.fact" ></textarea></div>
          <div><label class="form-label w-full">{{ $t('memory.keywords') }}</label><input class="input" v-model="newEvent.keywords"  /></div>
          <div><label class="form-label w-full">{{ $t('memory.importanceRange') }}</label><input class="input" v-model.number="newEvent.importance" type="number" min="0" max="1" step="0.1"  /></div>
        </div>
        <template #actions><button class="btn" @click="showCreate = false">{{ $t('common.cancel') }}</button><button class="btn btn-primary btn-sm" @click="handleCreate">{{ $t('memory.create') }}</button></template>
      </Modal>
      <Modal v-if="detailEvent" :title="$t('memory.detailTitle')" @close="detailEvent = null">
        <table class="detail-table">
          <tbody>
            <tr><td class="detail-label-cell">ID</td><td>{{ detailEvent.id }}</td></tr>
            <tr><td class="detail-label-cell">{{ $t('common.type') }}</td><td>{{ detailEvent.type }}</td></tr>
            <tr><td class="detail-label-cell">{{ $t('memory.fact') }}</td><td>{{ detailEvent.fact || '-' }}</td></tr>
            <tr><td class="detail-label-cell">{{ $t('memory.thought') }}</td><td>{{ detailEvent.thought || '-' }}</td></tr>
            <tr><td class="detail-label-cell">{{ $t('memory.importance') }}</td><td>{{ (detailEvent.importance||0).toFixed(3) }}</td></tr>
            <tr><td class="detail-label-cell">{{ $t('memory.keywords') }}</td><td><span v-for="k in (detailEvent.keywords || [])" :key="k" class="badge badge-gray keyword-tag">{{ k }}</span></td></tr>
            <tr><td class="detail-label-cell">{{ $t('common.time') }}</td><td>{{ formatTime(detailEvent.time) }}</td></tr>
          </tbody>
        </table>
        <template #actions><button class="btn" @click="detailEvent = null">{{ $t('common.close') }}</button></template>
      </Modal>
    </div>
  </div>
</template>

<style scoped>
.card-header-flex { display: flex; justify-content: space-between; align-items: center; }
.view-toggle { display: flex; gap: 4px; }
.star-text { font-size: 14px; letter-spacing: 1px; }
.importance-text { color: var(--text-muted); font-size: 12px; }
.timeline-time { color: var(--text-muted); font-size: 12px; }
.timeline-spacer { flex: 1; }
.timeline-empty { padding: 40px; }
.modal-form { display: flex; flex-direction: column; gap: 12px; }
.form-label { font-size: 12px; color: var(--text-muted); }
.detail-table { width: 100%; font-size: 13px; line-height: 1.8; }
.detail-label-cell { width: 80px; color: var(--text-muted); }
.keyword-tag { margin-right: 4px; }
.search-input { flex: 1; }
</style>
