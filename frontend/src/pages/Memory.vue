<script setup>
import { ref, onMounted } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import Modal from '@/components/Modal.vue'
import { formatTime } from '@/utils/format.js'

const toast = useToastStore()
const events = ref([])
const total = ref(0)
const filter = ref({ type: '', keyword: '' })
const showCreate = ref(false)
const newEvent = ref({ fact: '', keywords: '', importance: 0.5, event_type: 'fact' })
const detailEvent = ref(null)

onMounted(loadData)

async function loadData() {
  try { const r = await endpoints.memoryEvents(50, filter.value.type, filter.value.keyword); events.value = r.data.events || []; total.value = r.data.total || 0 } catch {}
}
async function handleDelete(id) { if (!confirm('确定删除？')) return; try { await endpoints.deleteMemoryEvent(id); toast.show('已删除', 'success'); loadData() } catch { toast.show('删除失败', 'error') } }
async function handleClear() { if (!confirm('确定清空所有？')) return; try { await endpoints.clearMemory(); toast.show('已清空', 'success'); loadData() } catch { toast.show('清空失败', 'error') } }
async function handleCreate() { if (!newEvent.value.fact) { toast.show('请输入内容', 'error'); return }; try { await endpoints.createMemoryEvent({ fact: newEvent.value.fact, keywords: newEvent.value.keywords, importance: Number(newEvent.value.importance) || 0.5, event_type: newEvent.value.event_type }); toast.show('已创建', 'success'); showCreate.value = false; loadData() } catch { toast.show('创建失败', 'error') } }
async function handleDetail(id) { try { const r = await endpoints.memoryEvent(id); detailEvent.value = r.data } catch { toast.show('加载失败', 'error') } }
function typeBadgeClass(t) { return t === 'fact' ? 'badge-blue' : t === 'thought' ? 'badge-green' : t === 'strategy' ? 'badge-yellow' : 'badge-gray' }
</script>

<template>
  <div>
    <div class="page-header"><h2>📝 记忆管理</h2></div>
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
        <div class="card-header">记忆列表</div>
        <table class="data-table" v-if="events.length > 0">
          <thead><tr><th>类型</th><th>内容</th><th>重要性</th><th>时间</th><th>操作</th></tr></thead>
          <tbody><tr v-for="e in events" :key="e.id"><td><span class="badge" :class="typeBadgeClass(e.type)">{{ e.type }}</span></td><td><span style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">{{ (e.fact||'').slice(0,60) }}</span></td><td>{{ (e.importance||0).toFixed(2) }}</td><td>{{ formatTime(e.time) }}</td><td><button class="btn btn-sm" @click="handleDetail(e.id)">详情</button> <button class="btn btn-sm btn-danger" @click="handleDelete(e.id)">删除</button></td></tr></tbody>
        </table>
        <div v-else class="empty-state" style="padding:40px"><span class="empty-icon">📭</span><p class="empty-text">暂无记忆</p></div>
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
            <tr><td style="width:80px;color:var(--text-muted)">时间</td><td>{{ formatTime(detailEvent.time) }}</td></tr>
          </tbody>
        </table>
        <template #actions><button class="btn" @click="detailEvent = null">关闭</button></template>
      </Modal>
    </div>
  </div>
</template>
