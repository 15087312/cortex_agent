<script setup>
// 思考轨迹时间线（DSH 式）—— 可复用核心组件。
// 把 技能 / 思考 / 工具(命令·读取·搜索·查看·修改) / 完成 这些不同来源的步骤，
// 按时间顺序串成一条可折叠的垂直时间线。
// 由 ProcessPanel（本轮实时 / 历史消息）与仪表盘"会话轨迹"页共用，避免重复实现。
defineOptions({ name: 'TrajectoryTimeline' })
import { ref, computed } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  entries: { type: Array, default: () => [] },   // 时间线条目数组（已含 ts/kind/title/view/status 等）
  title: { type: String, default: '思考轨迹' },
  open: { type: Boolean, default: false },
  emptyText: { type: String, default: '暂无轨迹' },
})

const flowOpen = ref(props.open)

// 时间线条目种类 → 图标 / 中文标签 / 配色
const KIND_META = {
  skill:    { icon: 'sparkles', tag: '技能', cls: 'tk-skill' },
  thinking: { icon: 'brain',    tag: '思考', cls: 'tk-think' },
  command:  { icon: 'monitor',  tag: '命令', cls: 'tk-cmd' },
  read:     { icon: 'file',     tag: '读取', cls: 'tk-read' },
  search:   { icon: 'search',   tag: '搜索', cls: 'tk-search' },
  web:      { icon: 'globe',    tag: '查看', cls: 'tk-web' },
  diff:     { icon: 'pencil',   tag: '修改', cls: 'tk-diff' },
  tool:     { icon: 'wrench',   tag: '工具', cls: 'tk-tool' },
  done:     { icon: 'check',    tag: '完成', cls: 'tk-done' },
}
function kindMeta(k) { return KIND_META[k] || KIND_META.tool }

// 工具状态 → 标签 / 配色（与 ToolCard 一致）
const STATUS_META = {
  running:    { label: '运行中', cls: 'st-running' },
  ok:         { label: '完成',   cls: 'st-ok' },
  error:      { label: '失败',   cls: 'st-error' },
  timeout:    { label: '超时',   cls: 'st-error' },
  denied:     { label: '被拒',   cls: 'st-denied' },
  invalid:    { label: '参数错误', cls: 'st-error' },
  interrupted: { label: '中断',  cls: 'st-denied' },
}
function statusMeta(s) { return STATUS_META[s] || STATUS_META.ok }

const sorted = computed(() =>
  (props.entries || []).slice().sort((a, b) => (a.ts || 0) - (b.ts || 0))
)

// 工具条目 → 可展开的细节摘要（命令/路径/查询/链接 等），其余类型展示标题本身
function buildDetail(e) {
  if (e.kind === 'thinking' || e.kind === 'skill' || e.kind === 'done') return e.title || ''
  const v = e.view
  if (!v) return e.preview || e.error || ''
  if (v.card === 'terminal') {
    const lines = []
    if (v.command) lines.push('$ ' + v.command)
    if (v.cwd) lines.push('cwd: ' + v.cwd)
    if (v.exit_code !== undefined && v.exit_code !== null) lines.push('exit: ' + v.exit_code)
    if (v.output) lines.push(v.output)
    return lines.join('\n')
  }
  if (v.card === 'read') {
    const lines = []
    if (v.path) lines.push(v.path)
    lines.push(`共 ${v.total_lines || 0} 行${v.truncated ? '（已截断）' : ''}`)
    return lines.join('\n')
  }
  if (v.card === 'search') return `${v.title || ''} — ${v.total || 0} 条匹配`
  if (v.card === 'web') {
    if (v.url) return v.url
    if (v.sources && v.sources.length) return v.sources.map(s => `• ${s.title || s.url}`).join('\n')
    return v.title || ''
  }
  if (v.card === 'diff') return `${v.path || ''} (+${v.additions || 0} / -${v.deletions || 0})`
  return e.preview || e.error || ''
}

// 展开控制（按条目索引）
const expanded = ref(new Set())
function toggle(i) {
  const s = new Set(expanded.value)
  s.has(i) ? s.delete(i) : s.add(i)
  expanded.value = s
}
function isExpandable(e) {
  const d = buildDetail(e)
  return !!d && d.length > 0 && e.kind !== 'done'
}
function detailText(e) { return buildDetail(e) }
</script>

<template>
  <div class="trajectory">
    <div class="trajectory-head" @click="flowOpen = !flowOpen">
      <Icon :name="flowOpen ? 'down' : 'right'" :size="12" />
      <Icon name="activity" :size="13" />
      <span>{{ title }}</span>
      <span class="trajectory-count">{{ sorted.length }} 步</span>
    </div>

    <div v-if="flowOpen" class="trajectory-body">
      <div v-if="!sorted.length" class="trajectory-empty">{{ emptyText }}</div>
      <ul v-else class="timeline">
        <li
          v-for="(e, i) in sorted"
          :key="i"
          class="tl-item"
          :class="[kindMeta(e.kind).cls, e.kind === 'done' ? 'tl-done' : '']"
        >
          <span class="tl-rail"><span class="tl-dot"><Icon :name="kindMeta(e.kind).icon" :size="12" /></span></span>
          <div class="tl-main">
            <div class="tl-row" :class="{ 'tl-clickable': isExpandable(e) }" @click="isExpandable(e) && toggle(i)">
              <span class="tl-tag">{{ kindMeta(e.kind).tag }}</span>
              <span class="tl-title">{{ e.title }}</span>
              <span v-if="e.status && e.kind !== 'done'" class="tl-status" :class="statusMeta(e.status).cls">{{ statusMeta(e.status).label }}</span>
              <span v-if="e.durationMs" class="tl-dur">{{ e.durationMs }}ms</span>
              <Icon v-if="isExpandable(e)" :name="expanded.has(i) ? 'down' : 'right'" :size="11" class="tl-caret" />
            </div>
            <pre v-if="expanded.has(i) && isExpandable(e)" class="tl-detail">{{ detailText(e) }}</pre>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.trajectory {
  border: 1px solid var(--border, rgba(255, 255, 255, 0.12));
  border-radius: 8px;
  background: var(--bg-secondary, rgba(255, 255, 255, 0.03));
  margin: 4px 0;
  overflow: hidden;
}
.trajectory-head {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 10px; cursor: pointer;
  font-size: 12px; color: var(--text-secondary, #5f6368);
}
.trajectory-count { margin-left: auto; font-size: 11px; color: var(--text-secondary, #5f6368); }
.trajectory-body { padding: 4px 10px 10px; }
.trajectory-empty { font-size: 12px; color: var(--text-secondary, #5f6368); padding: 6px 0; }

.timeline { list-style: none; margin: 0; padding: 0; }
.tl-item {
  display: flex; gap: 8px;
  padding: 2px 0;
}
.tl-rail {
  position: relative;
  width: 18px; flex: 0 0 18px;
  display: flex; justify-content: center;
}
/* 连接竖线 */
.tl-rail::before {
  content: ""; position: absolute; top: 0; bottom: 0; left: 50%;
  width: 2px; background: var(--border, rgba(255, 255, 255, 0.14));
  transform: translateX(-50%);
}
.tl-item:first-child .tl-rail::before { top: 12px; }
.tl-item:last-child .tl-rail::before { bottom: calc(100% - 12px); }
.tl-dot {
  position: relative; z-index: 1;
  width: 18px; height: 18px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: #1f1f22; color: #a5b4fc;
  border: 1px solid var(--border, rgba(255, 255, 255, 0.16));
}
.tl-main { flex: 1 1 auto; min-width: 0; padding-bottom: 6px; }
.tl-row {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 12px; line-height: 1.6; color: var(--text-primary, #111111);
}
.tl-clickable { cursor: pointer; }
.tl-tag {
  font-size: 10px; padding: 1px 6px; border-radius: 10px;
  background: var(--bg-tertiary, #f4f4f5); color: var(--text-secondary, #5f6368);
  white-space: nowrap;
}
.tl-title {
  flex: 1 1 auto; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.tl-status { font-size: 10px; padding: 1px 6px; border-radius: 10px; }
.tl-dur { font-size: 10px; color: var(--text-secondary, #5f6368); }
.tl-caret { color: var(--text-secondary, #5f6368); margin-left: 2px; }
.tl-detail {
  margin: 4px 0 0; padding: 6px 8px;
  background: var(--bg-inset, #f2f2f3); border: 1px solid var(--border-light, #ececee); border-radius: 6px;
  font-size: 11px; line-height: 1.5; color: var(--text-primary, #111111);
  white-space: pre-wrap; word-break: break-all; max-height: 240px; overflow: auto;
}

/* 各类别配色（点与标签左侧点缀） */
.tk-skill .tl-dot { color: #f0abfc; }
.tk-think .tl-dot { color: #c4b5fd; }
.tk-cmd   .tl-dot { color: #22d3ee; }
.tk-read  .tl-dot { color: #93c5fd; }
.tk-search .tl-dot { color: #fcd34d; }
.tk-web   .tl-dot { color: #86efac; }
.tk-diff  .tl-dot { color: #fda4af; }
.tk-tool  .tl-dot { color: #cbd5e1; }
.tk-done  .tl-dot { color: #86efac; }

/* 状态徽标 */
.st-running { background: rgba(139, 92, 246, 0.14); color: #6d28d9; }
.st-ok      { background: rgba(34, 197, 94, 0.14); color: #15803d; }
.st-error   { background: rgba(239, 68, 68, 0.14); color: #b91c1c; }
.st-denied  { background: rgba(245, 158, 11, 0.16); color: #b45309; }
[data-theme="dark"] .st-running { color: #c4b5fd; }
[data-theme="dark"] .st-ok      { color: #86efac; }
[data-theme="dark"] .st-error   { color: #fca5a5; }
[data-theme="dark"] .st-denied  { color: #fcd34d; }
</style>
