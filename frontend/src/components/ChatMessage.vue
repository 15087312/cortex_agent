<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { parseMarkdownSegments } from '@/utils/markdown.js'
import CodeBlock from '@/components/CodeBlock.vue'
import Icon from '@/components/Icon.vue'
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

function submitIntent() {
  const val = intentInput.value.trim()
  if (!val) return
  emit('answer-intent', props.message.requestId, val)
}
</script>

<template>
  <div v-if="kind !== 'process' && kind !== 'thinking'" class="message" :class="messageClass">
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
      <div v-else style="opacity:.7">{{ message.stale ? $t('chatMessage.requestExpired') : (message.approved ? $t('chatMessage.approvedResult') : $t('chatMessage.rejectedResult')) }}</div>
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
      <div v-else style="opacity:.7">{{ message.stale ? $t('chatMessage.requestExpired') : ($t('chatMessage.answeredLabel') + '：' + (message.answer || '')) }}</div>
    </div>

    <!-- 心理活动 -->
    <template v-else-if="kind === 'mental'">
      <div class="message-avatar" :class="message.avatarCls"></div>
      <div class="message-body">
        <div class="message-name"><span class="mental-badge">{{ $t('chatMessage.mental') }}</span></div>
        <div class="message-bubble mental-bubble">{{ message.content }}</div>
      </div>
    </template>

    <!-- 主管/专家实际输出：仅 Markdown 内容（推理/工具轨迹统一在过程流） -->
    <template v-else-if="kind === 'expert'">
      <div class="message-avatar" :class="message.avatarCls"></div>
      <div class="message-body">
        <div class="message-name">{{ message.name || $t('chatMessage.expert') }}</div>
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
            <!-- 流式中：光标提示“还在写” -->
            <span v-if="isStreaming" class="streaming-cursor">▊</span>
          </template>
        </div>
        <div v-if="!typing && !isStreaming" class="message-actions">
          <button class="msg-action" @click="emit('copy', index)" :title="$t('common.copy')"><Icon name="copy" :size="13" /></button>
          <button v-if="message.id" class="msg-action" @click="emit('edit', index)" :title="$t('common.edit')"><Icon name="pencil" :size="13" /></button>
          <button class="msg-action danger" @click="emit('delete', index)" :title="$t('common.delete')"><Icon name="trash" :size="13" /></button>
          <span v-if="message.role === 'user' && !message.id" class="msg-saving" :title="$t('chatMessage.waitingSave')">{{ $t('chatMessage.saving') }}</span>
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
