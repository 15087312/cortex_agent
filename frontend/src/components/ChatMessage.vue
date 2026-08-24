<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { parseMarkdownSegments } from '@/utils/markdown.js'
import CodeBlock from '@/components/CodeBlock.vue'
import Icon from '@/components/Icon.vue'
import ProcessPanel from '@/components/ProcessPanel.vue'
import { useI18n } from 'vue-i18n'

const props = defineProps({
  message: { type: Object, required: true },
  index: { type: Number, required: true },
  isStreaming: { type: Boolean, default: false },
})

const emit = defineEmits(['copy', 'delete', 'edit', 'approve', 'answer-intent'])
const { t } = useI18n()

const isUser = computed(() => props.message.role === 'user')
const kind = computed(() => props.message.kind || '')

// 思考过程：淡化文本 + 展开/收起
function shortOf(msg) {
  const c = msg.content || ''
  return c.length > 120 ? c.slice(0, 120) + '…' : c
}
function isThinkingLong(msg) { return (msg.content || '').length > 120 }
function toggleThinking(msg) { msg._expanded = !msg._expanded }

// ── 桌宠互动消息：content 匹配动作提示词 → 显示图标（提示词不展示） ──
const petActions = ref([])
fetch('/api/stream/pet/actions', { headers: { Accept: 'application/json' } })
  .then((r) => r.json())
  .then((d) => { petActions.value = (d?.data?.actions) || [] })
  .catch(() => {})
const petAction = computed(() => {
  if (!isUser.value || !props.message.content) return null
  return petActions.value.find((a) => a.prompt === props.message.content) || null
})

const messageClass = computed(() => {
  if (kind.value === 'approval') return 'approval-banner'
  if (kind.value === 'intent') return 'intent-banner'
  if (kind.value === 'mental') return 'ai mental-step'
  if (kind.value === 'thinking') return 'ai thinking-step'
  return isUser.value ? 'user' : 'ai'
})

/** 用户消息：纯文本转义 + 换行 */
const userHtml = computed(() => {
  return escapeHtml(props.message.content).replace(/\n/g, '<br>')
})

/** AI 消息：Markdown 结构化解析为 text / code 片段（主管/专家气泡同样支持） */
const segments = computed(() => {
  if (isUser.value || (kind.value && kind.value !== 'expert')) return []
  return parseMarkdownSegments(props.message.content)
})

function escapeHtml(s) {
  const d = document.createElement('div')
  d.textContent = s
  return d.innerHTML
}

// ── 打字机效果（仅 typing:true 的 AI 消息，对齐 js _typeMessage） ──
const typing = ref(false)
const shown = ref('')
let typeTimer = null

watch(
  () => props.message?.content,
  (val) => {
    if (props.message?.typing === true && !props.message?.typingDone && val) startTyping()
  },
  { immediate: true },
)

function startTyping() {
  const full = props.message.content || ''
  if (!full) return
  typing.value = true
  shown.value = ''
  let i = 0
  const step = () => {
    i = Math.min(i + 3, full.length)
    shown.value = full.slice(0, i)
    if (i < full.length) {
      typeTimer = setTimeout(step, 18)
    } else {
      typing.value = false
      props.message.typingDone = true
    }
  }
  step()
}

onBeforeUnmount(() => {
  if (typeTimer) clearTimeout(typeTimer)
})

// ── 提问面板：无预设选项时用输入框 ──
const intentInput = ref('')
const metaOpen = ref(false)

function submitIntent() {
  const val = intentInput.value.trim()
  if (!val) return
  emit('answer-intent', props.message.requestId, val)
}
</script>

<template>
  <div class="message" :class="messageClass">
    <!-- 安全审批横幅 -->
    <div v-if="kind === 'approval'" class="approval-box">
      <template v-if="!message.resolved">
        <div style="font-size:13px;font-weight:600;color:var(--danger)"><Icon name="alert" :size="14" /> {{ $t('chatMessage.waitingApproval') }}：<b>{{ message.target }}</b></div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;white-space:pre-wrap;word-break:break-all">{{ message.detail }}</div>
        <div style="margin-top:10px;display:flex;gap:8px">
          <button class="btn btn-sm btn-danger" @click="emit('approve', message.requestId, false)">{{ $t('chatMessage.reject') }}</button>
          <button class="btn btn-sm btn-primary" @click="emit('approve', message.requestId, true)">{{ $t('chatMessage.approve') }}</button>
        </div>
      </template>
      <div v-else style="opacity:.7">{{ message.approved ? $t('chatMessage.approvedResult') : $t('chatMessage.rejectedResult') }}</div>
    </div>

    <!-- 模型提问面板 -->
    <div v-else-if="kind === 'intent'" class="intent-box">
      <template v-if="!message.answered">
        <div style="font-size:13px;font-weight:600;color:var(--accent)">{{ $t('chatMessage.needsConfirmation') }}：<b>{{ message.question }}</b></div>
        <div v-if="message.options && message.options.length" style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
          <button
            v-for="opt in message.options"
            :key="opt"
            class="btn btn-sm"
            @click="emit('answer-intent', message.requestId, String(opt))"
          >{{ opt }}</button>
        </div>
        <div v-else style="margin-top:8px;display:flex;gap:8px">
          <input
            v-model="intentInput"
            class="input"
            style="flex:1;min-width:160px"
            :placeholder="$t('chatMessage.answerPlaceholder')"
            @keydown.enter.prevent="submitIntent"
          />
          <button class="btn btn-sm btn-primary" @click="submitIntent">{{ $t('common.submit') }}</button>
        </div>
      </template>
      <div v-else style="opacity:.7">{{ $t('chatMessage.answeredLabel') }}：{{ message.answer }}</div>
    </div>

    <!-- 思考步骤：淡化文本 + 点击展开/收起 -->
    <template v-else-if="kind === 'thinking'">
      <div class="message-avatar" :class="message.avatarCls"></div>
      <div class="message-body">
        <div class="message-name"><span class="thinking-badge">{{ $t('chatMessage.thinking') }}</span></div>
        <div class="thinking-box" @click="toggleThinking(message)">
          <div class="thinking-text">{{ message._expanded ? message.content : shortOf(message) }}</div>
          <div v-if="isThinkingLong(message)" class="thinking-toggle">{{ message._expanded ? $t('chatMessage.collapse') + ' ▲' : $t('chatMessage.expand') + ' ▼' }}</div>
        </div>
      </div>
    </template>

    <!-- 过程流面板（持久化的连续思考 + 调度语言 + 模型状态快照） -->
    <template v-else-if="kind === 'process'">
      <ProcessPanel :content="message.content || ''" :runners="message.runners || null" :open="false" />
    </template>

    <!-- 心理活动 -->
    <template v-else-if="kind === 'mental'">
      <div class="message-avatar" :class="message.avatarCls"></div>
      <div class="message-body">
        <div class="message-name"><span class="mental-badge">{{ $t('chatMessage.mental') }}</span></div>
        <div class="message-bubble mental-bubble">{{ message.content }}</div>
      </div>
    </template>

    <!-- 主管/专家实际输出：思考过程折叠 + 工具调用 + Markdown 内容 -->
    <template v-else-if="kind === 'expert'">
      <div class="message-avatar" :class="message.avatarCls"></div>
      <div class="message-body">
        <div class="message-name">{{ message.name || $t('chatMessage.expert') }}</div>
        <!-- 思考过程折叠（该身份推理） -->
        <div v-if="message._thinking" class="thinking-box" @click="toggleThinking(message)">
          <div class="thinking-text">{{ message._expanded ? message._thinking : shortOf({ content: message._thinking }) }}</div>
          <div v-if="(message._thinking || '').length > 120" class="thinking-toggle">{{ message._expanded ? $t('chatMessage.collapse') + ' ▲' : $t('chatMessage.expand') + ' ▼' }}</div>
        </div>
        <!-- 工具调用列表 -->
        <div v-if="message._tools && message._tools.length" class="expert-tools">
          <div v-for="t in message._tools" :key="t" class="expert-tool">{{ t }}</div>
        </div>
        <!-- Markdown 内容（与总指挥一致：文本 v-html + 代码块） -->
        <div class="message-bubble expert-bubble">
          <template v-if="segments.length">
            <template v-for="(seg, si) in segments" :key="si">
              <div v-if="seg.type === 'text'" v-html="seg.html"></div>
              <CodeBlock
                v-else-if="seg.type === 'code'"
                :language="seg.language"
                :code="seg.code"
                :highlighted-html="seg.highlightedHtml"
              />
            </template>
          </template>
          <template v-else>{{ message.content }}</template>
        </div>
      </div>
    </template>

    <!-- 普通消息 -->
    <template v-else>
      <div class="message-avatar" :class="isUser ? 'avatar-user' : 'avatar-large'"></div>
      <div class="message-body">
        <div class="message-name">{{ isUser ? $t('chatMessage.me') : (message.identity_name || $t('chatMessage.commander')) }}</div>
        <!-- 思考折叠区（与回复同一框，顶部可折叠） -->
        <div v-if="!isUser && message.thinking" class="think-collapse" @click="toggleThinking(message)">
          <div class="think-collapse-title">{{ message._expanded ? $t('chatMessage.collapseThinking') + ' ▲' : $t('chatMessage.expandThinking') + ' ▼' }}</div>
          <div v-if="message._expanded" class="think-collapse-text">{{ message.thinking }}</div>
        </div>
        <div class="message-bubble" :class="{ 'bubble-error': message.error, 'bubble-proactive': !isUser && message.proactive }">
          <!-- 用户消息：桌宠互动 → 图标；否则纯文本 -->
          <div v-if="isUser && petAction" class="pet-interaction" :title="petAction.label">
            <span class="pet-interaction-ic"><Icon :name="petAction.icon" :size="22" /></span>
          </div>
          <div v-else-if="isUser">
            <div v-if="message.images && message.images.length" class="user-attachments">
              <img v-for="(src, i) in message.images" :key="i" :src="src" class="user-attachment-img" />
            </div>
            <div v-if="message.content" v-html="userHtml"></div>
          </div>

          <!-- AI 消息打字中：纯文本逐字揭示 -->
          <template v-else-if="typing">
            <div v-html="escapeHtml(shown)"></div>
            <span class="streaming-cursor">▊</span>
          </template>

          <!-- AI 消息完成：文本片段 v-html + 代码块 CodeBlock 组件 -->
          <template v-else>
            <template v-for="(seg, i) in segments" :key="i">
              <div v-if="seg.type === 'text'" v-html="seg.html"></div>
              <CodeBlock
                v-else-if="seg.type === 'code'"
                :language="seg.language"
                :code="seg.code"
                :highlighted-html="seg.highlightedHtml"
              />
            </template>
          </template>
        </div>
        <div v-if="!typing" class="message-actions">
          <button class="msg-action" @click="emit('copy', index)" :title="$t('common.copy')"><Icon name="copy" :size="13" /></button>
          <button v-if="message.id" class="msg-action" @click="emit('edit', index)" :title="$t('common.edit')"><Icon name="pencil" :size="13" /></button>
          <button class="msg-action danger" @click="emit('delete', index)" :title="$t('common.delete')"><Icon name="trash" :size="13" /></button>
          <span v-if="message.role === 'user' && !message.id" class="msg-saving" :title="$t('chatMessage.waitingSave')">{{ $t('chatMessage.saving') }}</span>
        </div>

        <!-- 思考详情展开栏（内心独白 + 相关事件记忆 + 会话记忆） -->
        <div v-if="!isUser && message.meta && !typing" class="meta-collapse">
          <button class="meta-collapse-btn" @click="metaOpen = !metaOpen">
            <Icon :name="metaOpen ? 'down' : 'right'" :size="12" /> {{ $t('chatMessage.thinkingDetails') }}
          </button>
          <div v-if="metaOpen" class="meta-detail">
            <div v-if="message.meta.innerMonologue" class="meta-block">
              <div class="meta-label">{{ $t('chatMessage.innerMonologue') }}</div>
              <div class="meta-text">{{ message.meta.innerMonologue }}</div>
            </div>
            <div v-if="message.meta.eventMemory" class="meta-block">
              <div class="meta-label">{{ $t('chatMessage.eventMemory') }}</div>
              <div class="meta-text">{{ message.meta.eventMemory }}</div>
            </div>
            <div v-if="message.meta.sessionMemory" class="meta-block">
              <div class="meta-label">{{ $t('chatMessage.sessionMemory') }}</div>
              <div class="meta-text">{{ message.meta.sessionMemory }}</div>
            </div>
            <div v-if="!message.meta.innerMonologue && !message.meta.eventMemory && !message.meta.sessionMemory" class="meta-empty">{{ $t('chatMessage.noExtraContext') }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.thinking-box {
  background: var(--bg-secondary, rgba(255,255,255,0.03));
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
  cursor: pointer;
  max-width: 520px;
}
.thinking-text {
  font-size: 12px;
  color: var(--text-muted, #8b949e);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.thinking-toggle {
  font-size: 11px;
  color: var(--accent, #58a6ff);
  margin-top: 4px;
}
.thinking-badge {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-tertiary, rgba(139,148,158,0.15));
  padding: 1px 7px;
  border-radius: 8px;
}
.think-collapse {
  margin-bottom: 8px;
  padding: 5px 10px;
  background: var(--bg-secondary, rgba(255,255,255,0.03));
  border: 1px solid var(--border);
  border-radius: 8px;
  cursor: pointer;
  max-width: 520px;
}
.think-collapse-title {
  font-size: 11px;
  color: var(--text-muted, #8b949e);
}
.think-collapse-text {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-muted, #8b949e);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}
</style>
