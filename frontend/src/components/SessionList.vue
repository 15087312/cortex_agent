<script setup>
import { ref, computed } from 'vue'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
  collapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'delete', 'rename', 'new', 'update:collapsed'])

const search = ref('')
const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return props.sessions
  return props.sessions.filter((s) =>
    (s.title || '').toLowerCase().includes(q) || (s.session_id || '').toLowerCase().includes(q)
  )
})
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
        <button class="session-collapse-btn" @click="emit('update:collapsed', true)" title="收起会话列表"><Icon name="left" :size="16" /></button>
      </div>
      <div class="session-list-body">
        <button class="btn btn-primary btn-sm" style="width:100%;justify-content:center;margin-bottom:8px" @click="emit('new')">+ 新建会话</button>
        <div class="session-search">
          <Icon name="search" :size="14" style="color:var(--text-muted)" />
          <input class="input" v-model="search" placeholder="搜索会话..." style="border:none;background:transparent;flex:1;padding:4px 0;outline:none" />
        </div>
        <div v-if="filtered.length === 0" class="chat-sessions-empty">暂无会话</div>
        <div
          v-for="s in filtered"
          :key="s.session_id"
          class="session-item"
          :class="{ active: activeId === s.session_id }"
          @click="emit('select', s.session_id)"
          @contextmenu.prevent="emit('delete', s.session_id)"
        >
          <div class="session-title">{{ (s.title || s.session_id || '').slice(0, 30) }}</div>
          <div class="session-time">
            {{ (s.last_active || s.created_at || '').slice(5, 16) }}
            <template v-if="s.message_count"> · {{ s.message_count }} 条</template>
          </div>
          <div class="session-item-actions" @click.stop>
            <button class="btn btn-sm" @click="emit('rename', s.session_id)" title="重命名"><Icon name="pencil" :size="13" /></button>
            <button class="btn btn-sm" style="color:var(--danger)" @click="emit('delete', s.session_id)" title="删除"><Icon name="trash" :size="13" /></button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
