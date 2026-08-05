<script setup>
import { ref, computed } from 'vue'
import { endpoints } from '@/api.js'
import { useToastStore } from '@/stores/toast.js'
import { useConfirm } from '@/composables/useDialog.js'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  collapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'delete', 'rename', 'new', 'update:collapsed'])
const toast = useToastStore()
const confirm = useConfirm()

const search = ref('')
const manage = ref(false)
const selected = ref({})

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return props.sessions
  return props.sessions.filter((s) =>
    (s.title || '').toLowerCase().includes(q) || (s.session_id || '').toLowerCase().includes(q)
  )
})
const selectedCount = computed(() => Object.values(selected.value).filter(Boolean).length)

function toggleManage() {
  manage.value = !manage.value
  selected.value = {}
}

function toggleOne(sid) {
  selected.value = { ...selected.value, [sid]: !selected.value[sid] }
}

function toggleAll() {
  const allSelected = filtered.value.length > 0 && filtered.value.every((s) => selected.value[s.session_id])
  selected.value = {}
  if (!allSelected) for (const s of filtered.value) selected.value[s.session_id] = true
}

async function handleBatchDelete() {
  const ids = filtered.value.filter((s) => selected.value[s.session_id]).map((s) => s.session_id)
  if (!ids.length) return
  if (!(await confirm(`确定删除选中的 ${ids.length} 个会话？不可撤销`))) return
  try {
    const r = await endpoints.batchDeleteSessions(ids)
    toast.show(`已删除 ${r.data?.count || ids.length} 个会话`, 'success')
    manage.value = false
    selected.value = {}
    emit('delete', null)  // 通知父组件刷新
  } catch (e) {
    toast.show('删除失败: ' + (e.body?.error?.message || e.status), 'error')
  }
}
</script>

<template>
  <div class="session-list" :class="{ collapsed }">
    <template v-if="collapsed">
      <div class="session-list-collapsed-inner">
        <button class="session-expand-btn" @click="emit('update:collapsed', false)" title="展开会话列表"><Icon name="right" :size="16" /></button>
      </div>
    </template>

    <template v-else>
      <div class="session-list-header">
        <h3 style="font-size:14px;color:var(--text-secondary)">会话列表</h3>
        <div style="display:flex;gap:4px">
          <button v-if="manage" class="btn btn-sm btn-danger" :disabled="!selectedCount" @click="handleBatchDelete"><Icon name="trash" :size="13" /> 删除({{ selectedCount }})</button>
          <button class="btn btn-sm" :class="{ 'btn-primary': manage }" @click="toggleManage">{{ manage ? '完成' : '批量' }}</button>
          <button class="session-collapse-btn" @click="emit('update:collapsed', true)" title="收起会话列表"><Icon name="left" :size="16" /></button>
        </div>
      </div>
      <div class="session-list-body">
        <button class="btn btn-primary btn-sm" style="width:100%;justify-content:center;margin-bottom:8px" @click="emit('new')">+ 新建会话</button>
        <div class="session-search">
          <Icon name="search" :size="14" style="color:var(--text-muted)" />
          <input class="input" v-model="search" placeholder="搜索会话..." style="border:none;background:transparent;flex:1;padding:4px 0;outline:none" />
        </div>
        <div v-if="manage" class="session-manage-bar">
          <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text-secondary);cursor:pointer">
            <input type="checkbox" :checked="filtered.length > 0 && filtered.every(s => selected[s.session_id])" @change="toggleAll" /> 全选
          </label>
          <span style="flex:1"></span>
          <span style="font-size:11px;color:var(--text-muted)">点会话勾选</span>
        </div>
        <div v-if="filtered.length === 0" class="chat-sessions-empty">暂无会话</div>
        <div
          v-for="s in filtered"
          :key="s.session_id"
          class="session-item"
          :class="{ active: !manage && activeId === s.session_id, 'manage-checked': manage && selected[s.session_id] }"
          @click="manage ? toggleOne(s.session_id) : emit('select', s.session_id)"
          @contextmenu.prevent="manage ? null : emit('delete', s.session_id)"
        >
          <div style="display:flex;align-items:center;gap:8px;min-width:0">
            <input v-if="manage" type="checkbox" :checked="!!selected[s.session_id]" @click.stop="toggleOne(s.session_id)" />
            <div style="min-width:0">
              <div class="session-title">{{ (s.title || s.session_id || '').slice(0, 30) }}</div>
              <div class="session-time">
                {{ (s.last_active || s.created_at || '').slice(5, 16) }}
                <template v-if="s.message_count"> · {{ s.message_count }} 条</template>
              </div>
            </div>
          </div>
          <div v-if="!manage" class="session-item-actions" @click.stop>
            <button class="btn btn-sm" @click="emit('rename', s.session_id)" title="重命名"><Icon name="pencil" :size="13" /></button>
            <button class="btn btn-sm" style="color:var(--danger)" @click="emit('delete', s.session_id)" title="删除"><Icon name="trash" :size="13" /></button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
