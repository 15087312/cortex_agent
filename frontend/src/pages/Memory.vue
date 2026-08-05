<script setup>
import { ref, computed, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm } from '@/composables/useDialog.js'
import Modal from '@/components/Modal.vue'
import { formatTime } from '@/utils/format.js'
import Icon from '@/components/Icon.vue'

const toast = useToastStore()
const confirm = useConfirm()
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
    const date = (e.time || '').slice(0, 10) || '未知日期'
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
  if (!(await confirm('确定删除？不可撤销'))) return
  try { await endpoints.deleteMemoryEvent(id); toast.show('已删除', 'success'); loadData() } catch { toast.show('删除失败', 'error') }
}
async function handleClear() {
  if (!(await confirm('确定清空所有记忆？不可撤销'))) return
  try { await endpoints.clearMemory(); toast.show('已清空', 'success'); loadData() } catch { toast.show('清空失败', 'error') }
}
async function handleCreate() { if (!newEvent.value.fact) { toast.show('请输入内容', 'error'); return }; try { await endpoints.createMemoryEvent({ fact: newEvent.value.fact, keywords: newEvent.value.keywords, importance: Number(newEvent.value.importance) || 0.5, event_type: newEvent.value.event_type }); toast.show('已创建', 'success'); showCreate.value = false; loadData() } catch { toast.show('创建失败', 'error') } }
async function handleDetail(id) { try { const r = await endpoints.memoryEvent(id); detailEvent.value = r.data } catch { toast.show('加载失败', 'error') } }
function typeBadgeClass(t) { return t === 'fact' ? 'badge-blue' : t === 'thought' ? 'badge-green' : t === 'strategy' ? 'badge-yellow' : 'badge-gray' }
function starRating(v) { const s = Math.round(v * 5); return '★'.repeat(s) + '☆'.repeat(5 - s) }
</script>

<template>
  <div>
    <div class="page-header">
      <h2>记忆管理</h2>
      <button class="btn btn-sm" @click="loadData"><Icon name="refresh" :size="14" /> 刷新</button>
    </div>
    <div class="page-body">
      <div class="stat-grid">
        <div class="stat-card"><div class="stat-value">{{ total }}</div><div class="stat-label">总事件</div></div>
        <div class="stat-card"><div class="stat-value">{{ events.length }}</div><div class="stat-label">当前显示</div></div>
        <div class="stat-card"><button class="btn btn-danger btn-sm" @click="handleClear">清空记忆</button><div class="stat-label">不可撤销</div></div>
      </div>
      <div class="search-bar">
        <input class="input" v-model="filter.keyword" placeholder="搜索关键词..." @keyup.enter="loadData" style="flex:1" />
        <select class="input" v-model="filter.type"><option value="">全部</option><option value="fact">fact</option><option value="thought">thought</option><option value="strategy">strategy</option><option value="emotion">emotion</option></select>
        <button class="btn btn-primary btn-sm" @click="loadData">搜索</button>
        <button class="btn btn-sm" @click="showCreate = true">+新建</button>
      </div>
      <div class="card">
        <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
          <span>记忆列表 ({{ total }})</span>
          <div style="display:flex;gap:4px">
            <button class="btn btn-sm" :class="{ 'btn-primary': viewMode === 'list' }" @click="viewMode = 'list'">列表</button>
            <button class="btn btn-sm" :class="{ 'btn-primary': viewMode === 'timeline' }" @click="viewMode = 'timeline'">时间线</button>
          </div>
        </div>
        <!-- 列表视图 -->
        <table class="data-table" v-if="events.length > 0 && viewMode === 'list'">
          <thead><tr><th>类型</th><th>内容</th><th>重要性</th><th>时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="e in events" :key="e.id">
              <td><span class="badge" :class="typeBadgeClass(e.type)">{{ e.type }}</span></td>
              <td><span class="mem-content-ellipsis">{{ e.fact || '' }}</span></td>
              <td><span class="star-rating" style="font-size:14px;letter-spacing:1px">{{ starRating(e.importance || 0) }}</span> <span style="color:var(--text-muted);font-size:12px">{{ (e.importance||0).toFixed(2) }}</span></td>
              <td>{{ formatTime(e.time) }}</td>
              <td><button class="btn btn-sm" @click="handleDetail(e.id)">详情</button> <button class="btn btn-sm btn-danger" @click="handleDelete(e.id)">删除</button></td>
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
              <span style="color:var(--text-muted);font-size:12px">{{ formatTime(e.time) }}</span>
              <span style="flex:1"></span>
              <button class="btn btn-sm btn-danger" @click.stop="handleDelete(e.id)"><Icon name="trash" :size="13" /></button>
            </div>
          </div>
        </div>
        <div v-else class="empty-state" style="padding:40px"><span class="empty-icon"><Icon name="inbox" :size="20" /></span><p class="empty-text">暂无记忆</p></div>
      </div>
      <Modal v-if="showCreate" title="新建记忆" @close="showCreate = false">
        <div style="display:flex;flex-direction:column;gap:12px">
          <div><label style="font-size:12px;color:var(--text-muted)">类型</label><select class="input" v-model="newEvent.event_type" style="width:100%"><option value="fact">fact</option><option value="thought">thought</option><option value="strategy">strategy</option><option value="emotion">emotion</option></select></div>
          <div><label style="font-size:12px;color:var(--text-muted)">内容 *</label><textarea class="input" v-model="newEvent.fact" style="width:100%;min-height:60px"></textarea></div>
          <div><label style="font-size:12px;color:var(--text-muted)">关键词</label><input class="input" v-model="newEvent.keywords" style="width:100%" /></div>
          <div><label style="font-size:12px;color:var(--text-muted)">重要性 0-1</label><input class="input" v-model.number="newEvent.importance" type="number" min="0" max="1" step="0.1" style="width:100%" /></div>
        </div>
        <template #actions><button class="btn" @click="showCreate = false">取消</button><button class="btn btn-primary btn-sm" @click="handleCreate">创建</button></template>
      </Modal>
      <Modal v-if="detailEvent" title="记忆详情" @close="detailEvent = null">
        <table style="width:100%;font-size:13px;line-height:1.8">
          <tbody>
            <tr><td style="width:80px;color:var(--text-muted)">ID</td><td>{{ detailEvent.id }}</td></tr>
            <tr><td style="width:80px;color:var(--text-muted)">类型</td><td>{{ detailEvent.type }}</td></tr>
            <tr><td style="width:80px;color:var(--text-muted)">事实</td><td>{{ detailEvent.fact || '-' }}</td></tr>
            <tr><td style="width:80px;color:var(--text-muted)">思考</td><td>{{ detailEvent.thought || '-' }}</td></tr>
            <tr><td style="width:80px;color:var(--text-muted)">重要性</td><td>{{ (detailEvent.importance||0).toFixed(3) }}</td></tr>
            <tr><td style="width:80px;color:var(--text-muted)">关键词</td><td><span v-for="k in (detailEvent.keywords || [])" :key="k" class="badge badge-gray" style="margin-right:4px">{{ k }}</span></td></tr>
            <tr><td style="width:80px;color:var(--text-muted)">时间</td><td>{{ formatTime(detailEvent.time) }}</td></tr>
          </tbody>
        </table>
        <template #actions><button class="btn" @click="detailEvent = null">关闭</button></template>
      </Modal>
    </div>
  </div>
</template>
