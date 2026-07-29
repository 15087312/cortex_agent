<script setup>
defineProps({
  sessions: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
})

const emit = defineEmits(['select', 'delete', 'new'])
</script>

<template>
  <div class="session-list">
    <div style="padding:16px;border-bottom:1px solid var(--border)">
      <h3 style="font-size:14px;color:var(--text-secondary);margin-bottom:12px">会话列表</h3>
      <button class="btn btn-primary btn-sm" style="width:100%;justify-content:center" @click="emit('new')">+ 新建会话</button>
    </div>
    <div style="flex:1;overflow-y:auto;padding:8px">
      <div v-if="sessions.length === 0" class="chat-sessions-empty">暂无会话</div>
      <div
        v-for="s in sessions"
        :key="s.session_id"
        class="session-item"
        :style="{
          background: activeId === s.session_id ? 'var(--accent-bg)' : 'transparent',
          borderLeft: activeId === s.session_id ? '3px solid var(--accent)' : '3px solid transparent'
        }"
        @click="emit('select', s.session_id)"
        @contextmenu.prevent="emit('delete', s.session_id)"
      >
        <div style="font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          {{ (s.title || s.session_id || '').slice(0, 30) }}
        </div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px">
          {{ (s.last_active || s.created_at || '').slice(5, 16) }} · {{ s.message_count || 0 }}条
        </div>
      </div>
    </div>
  </div>
</template>
