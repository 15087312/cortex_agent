<script setup>
// 过程流面板：展示一轮完整思考过程（连续思考 + 调度语言）与该轮运行的模型状态快照。
// 持久化到会话（role=process），思考结束不消失，切换会话后从 DB 恢复。
defineOptions({ name: 'ProcessPanel' })
import { ref, computed } from 'vue'
import Icon from './Icon.vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const props = defineProps({
  content: { type: String, default: '' },
  runners: { type: Object, default: null },
  open: { type: Boolean, default: false },
})

const flowOpen = ref(props.open)
const panelOpen = ref(props.open)

// 模型树：large 根 → supervisor → expert（与运行时面板一致，但展示"当时"的快照）
const treeNodes = computed(() => {
  const snap = props.runners || {}
  const byId = {}
  const nodes = []
  const add = (r, tier, sup) => { if (r && r.model_id) { const n = { ...r, tier, supervisor: sup || '', children: [] }; byId[n.model_id] = n; nodes.push(n) } }
  if (snap.large_model) add(snap.large_model, 'large', '')
  ;(snap.active_supervisors || []).forEach(s => add(s, 'supervisor', ''))
  ;(snap.active_experts || []).forEach(e => add(e, 'expert', e.supervisor || ''))
  return nodes
})

const tree = computed(() => {
  const byId = {}
  treeNodes.value.forEach(n => { byId[n.model_id] = n })
  const roots = []
  treeNodes.value.forEach(n => {
    const parent = (n.supervisor && byId[n.supervisor]) ? byId[n.supervisor] : null
    if (parent) parent.children.push(n)
    else roots.push(n)
  })
  return roots
})

function ctxPct(r) {
  const win = (r && r.context_window_size) || 0
  const tok = (r && r.context_tokens) || 0
  if (!win || !tok) return 0
  return Math.max(0, Math.min(100, Math.round((tok / win) * 100)))
}

function ctxLabel(r) {
  const tok = (r && r.context_tokens) || 0
  const win = (r && r.context_window_size) || 0
  if (!tok) return ''
  return (tok ? tok.toLocaleString() : '0') + ' / ' + (win ? win.toLocaleString() : '?')
}

const STATUS_META = {
  thinking: { key: 'thinking', cls: 'st-thinking' },
  tool_loop: { key: 'tool_loop', cls: 'st-tool' },
  waiting_delegation: { key: 'waiting_delegation', cls: 'st-waiting' },
  completed: { key: 'completed', cls: 'st-done' },
  error: { key: 'error', cls: 'st-error' },
  idle: { key: 'idle', cls: 'st-idle' },
}
function meta(r) { return STATUS_META[r.status] || STATUS_META.idle }
function statusLabel(r) { return t('processPanel.status.' + meta(r).key) }
</script>

<template>
  <div class="process-panel">
    <div class="process-panel-head" @click="panelOpen = !panelOpen">
      <Icon :name="panelOpen ? 'down' : 'right'" :size="12" />
      <Icon name="activity" :size="13" />
      <span>{{ $t('processPanel.thinkingProcess') }}</span>
      <span v-if="content" class="process-count">{{ $t('processPanel.steps', { count: content.split('\n\n').length }) }}</span>
    </div>

    <div v-if="panelOpen" class="process-panel-body">
      <!-- 模型状态快照（当时的运行情况）：每个模型后边显示上下文小圆环 -->
      <div v-if="tree.length" class="process-tree">
        <div v-for="node in tree" :key="node.model_id" class="process-node" :class="'td-' + (node.tier || 'large')">
          <div class="process-node-row">
            <span class="process-node-dot"></span>
            <span class="process-node-name">{{ node.name || node.role || node.model_id }}</span>
            <span class="process-node-model">{{ node.model_id }}</span>
            <span class="think-badge" :class="meta(node).cls">{{ statusLabel(node) }}</span>
            <span class="process-node-ring" :title="ctxLabel(node)">
              <svg viewBox="0 0 20 20" width="20" height="20">
                <circle cx="10" cy="10" r="8" fill="none" stroke="var(--border, rgba(255,255,255,.12))" stroke-width="2.5" />
                <circle v-if="ctxPct(node) > 0" cx="10" cy="10" r="8" fill="none"
                  stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
                  :stroke-dasharray="2 * Math.PI * 8"
                  :stroke-dashoffset="2 * Math.PI * 8 * (1 - ctxPct(node) / 100)" />
              </svg>
              <span class="process-node-ring-pct">{{ ctxPct(node) }}%</span>
            </span>
          </div>
          <div v-for="child in node.children" :key="child.model_id" class="process-node process-node-child">
            <div class="process-node-row">
              <span class="process-node-dot"></span>
              <span class="process-node-name">{{ child.name || child.role || child.model_id }}</span>
              <span class="process-node-model">{{ child.model_id }}</span>
              <span class="think-badge" :class="meta(child).cls">{{ statusLabel(child) }}</span>
              <span class="process-node-ring" :title="ctxLabel(child)">
                <svg viewBox="0 0 20 20" width="20" height="20">
                  <circle cx="10" cy="10" r="8" fill="none" stroke="var(--border, rgba(255,255,255,.12))" stroke-width="2.5" />
                  <circle v-if="ctxPct(child) > 0" cx="10" cy="10" r="8" fill="none"
                    stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
                    :stroke-dasharray="2 * Math.PI * 8"
                    :stroke-dashoffset="2 * Math.PI * 8 * (1 - ctxPct(child) / 100)" />
                </svg>
                <span class="process-node-ring-pct">{{ ctxPct(child) }}%</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 过程流文本区（可折叠） -->
      <div v-if="content" class="process-flow">
        <div class="process-flow-head" @click="flowOpen = !flowOpen">
          <span class="process-flow-toggle">{{ flowOpen ? $t('processPanel.collapseProcess') + ' ▲' : $t('processPanel.expandProcess') + ' ▼' }}</span>
        </div>
        <div v-if="flowOpen" class="process-flow-body">{{ content }}</div>
      </div>
      <div v-else-if="!tree.length" class="process-empty">{{ $t('processPanel.noContent') }}</div>
    </div>
  </div>
</template>

<style scoped>
.process-panel {
  border: 1px solid var(--border, rgba(255,255,255,.12));
  border-radius: 8px;
  background: var(--bg-secondary, rgba(255,255,255,.03));
  margin: 4px 0;
  overflow: hidden;
}
.process-panel-head {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; cursor: pointer;
  font-size: 12px; color: var(--text-secondary, #bbb);
}
.process-panel-body { padding: 4px 10px 10px; }
.process-count { margin-left: auto; font-size: 11px; opacity: .7 }
.process-tree { margin: 4px 0 }
.process-node { margin-bottom: 4px }
.process-node-child { margin-left: 20px }
.process-node-row {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 3px 0;
}
.process-node-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent, #8b5cf6) }
.td-supervisor .process-node-dot { background: #3b82f6 }
.td-expert .process-node-dot { background: #f59e0b }
.process-node-name { font-weight: 600; color: var(--text, #eee) }
.process-node-model { color: var(--text-faint, #777); font-size: 11px }
.process-node-ring {
  margin-left: auto; display: inline-flex; align-items: center; gap: 4px;
  color: var(--accent, #8b5cf6);
}
.process-node-ring-pct { font-size: 10px; color: var(--text-secondary, #aaa) }
.process-flow { margin-top: 8px; border-top: 1px dashed var(--border, rgba(255,255,255,.12)); padding-top: 6px }
.process-flow-head { cursor: pointer; font-size: 12px; color: var(--text-secondary, #bbb) }
.process-flow-body {
  white-space: pre-wrap; font-size: 12px; line-height: 1.7;
  color: var(--text-secondary, #bbb); margin-top: 6px; max-height: 320px; overflow:auto;
  background: rgba(0,0,0,.25); border-radius: 6px; padding: 8px 10px;
}
.process-empty { font-size: 12px; color: var(--text-faint, #777); padding: 6px 0 }
.think-badge { font-size: 10px; padding: 1px 6px; border-radius: 10px; background: rgba(255,255,255,.08) }
.st-thinking { background: rgba(139,92,246,.2); color: #c4b5fd }
.st-tool { background: rgba(245,158,11,.18); color: #fcd34d }
.st-waiting { background: rgba(59,130,246,.18); color: #bfdbfe }
.st-done { background: rgba(34,197,94,.15); color: #86efac }
.st-error { background: rgba(239,68,68,.18); color: #fca5a5 }
.st-idle { background: rgba(255,255,255,.06); color: #999 }
</style>