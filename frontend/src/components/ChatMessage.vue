<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { parseMarkdownSegments } from '@/utils/markdown.js'
import CodeBlock from '@/components/CodeBlock.vue'
import Icon from '@/components/Icon.vue'

const props = defineProps({
  message: { type: Object, required: true },
  index: { type: Number, required: true },
  isStreaming: { type: Boolean, default: false },
})

const emit = defineEmits(['copy', 'delete', 'edit', 'approve', 'answer-intent'])

const isUser = computed(() => props.message.role === 'user')
const kind = computed(() => props.message.kind || '')

const messageClass = computed(() => {
  if (kind.value === 'approval') return 'approval-banner'
  if (kind.value === 'intent') return 'intent-banner'
  if (kind.value === 'thinking') return 'ai thinking-step'
  return isUser.value ? 'user' : 'ai'
})

/** 用户消息：纯文本转义 + 换行 */
const userHtml = computed(() => {
  return escapeHtml(props.message.content).replace(/\n/g, '<br>')
})

/** AI 消息：Markdown 结构化解析为 text / code 片段 */
const segments = computed(() => {
  if (isUser.value || kind.value) return []
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
        <div style="font-size:13px;font-weight:600;color:var(--danger)"><Icon name="alert" :size="14" /> 等待审批：<b>{{ message.target }}</b></div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px;white-space:pre-wrap;word-break:break-all">{{ message.detail }}</div>
        <div style="margin-top:10px;display:flex;gap:8px">
          <button class="btn btn-sm btn-danger" @click="emit('approve', message.requestId, false)">拒绝</button>
          <button class="btn btn-sm btn-primary" @click="emit('approve', message.requestId, true)">批准</button>
        </div>
      </template>
      <div v-else style="opacity:.7">已{{ message.approved ? '批准' : '拒绝' }}该操作</div>
    </div>

    <!-- 模型提问面板 -->
    <div v-else-if="kind === 'intent'" class="intent-box">
      <template v-if="!message.answered">
        <div style="font-size:13px;font-weight:600;color:var(--accent)">模型需要你确认：<b>{{ message.question }}</b></div>
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
            placeholder="输入你的回答..."
            @keydown.enter.prevent="submitIntent"
          />
          <button class="btn btn-sm btn-primary" @click="submitIntent">提交</button>
        </div>
      </template>
      <div v-else style="opacity:.7">已回答：{{ message.answer }}</div>
    </div>

    <!-- 思考步骤小气泡（supervisor/expert） -->
    <template v-else-if="kind === 'thinking'">
      <div class="message-avatar" :class="message.avatarCls"></div>
      <div class="message-body">
        <div class="message-name">{{ message.name }}</div>
        <div class="message-bubble">{{ message.content }}</div>
      </div>
    </template>

    <!-- 普通消息 -->
    <template v-else>
      <div class="message-avatar" :class="isUser ? 'avatar-user' : 'avatar-large'"></div>
      <div class="message-body">
        <div class="message-name">{{ isUser ? '我' : (message.identity_name || '总指挥') }}</div>
        <div class="message-bubble" :class="{ 'bubble-error': message.error }">
          <!-- 用户消息：纯文本 -->
          <div v-if="isUser" v-html="userHtml"></div>

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
          <button class="msg-action" @click="emit('copy', index)" title="复制"><Icon name="copy" :size="13" /></button>
          <button v-if="message.id" class="msg-action" @click="emit('edit', index)" title="编辑"><Icon name="pencil" :size="13" /></button>
          <button class="msg-action danger" @click="emit('delete', index)" title="删除"><Icon name="trash" :size="13" /></button>
          <span v-if="message.role === 'user' && !message.id" class="msg-saving" title="等待保存">保存中…</span>
        </div>

        <!-- 思考详情展开栏（内心独白 + 相关事件记忆 + 会话记忆） -->
        <div v-if="!isUser && message.meta && !typing" class="meta-collapse">
          <button class="meta-collapse-btn" @click="metaOpen = !metaOpen">
            <Icon :name="metaOpen ? 'down' : 'right'" :size="12" /> 思考详情
          </button>
          <div v-if="metaOpen" class="meta-detail">
            <div v-if="message.meta.innerMonologue" class="meta-block">
              <div class="meta-label">内心独白</div>
              <div class="meta-text">{{ message.meta.innerMonologue }}</div>
            </div>
            <div v-if="message.meta.eventMemory" class="meta-block">
              <div class="meta-label">相关事件记忆</div>
              <div class="meta-text">{{ message.meta.eventMemory }}</div>
            </div>
            <div v-if="message.meta.sessionMemory" class="meta-block">
              <div class="meta-label">会话记忆</div>
              <div class="meta-text">{{ message.meta.sessionMemory }}</div>
            </div>
            <div v-if="!message.meta.innerMonologue && !message.meta.eventMemory && !message.meta.sessionMemory" class="meta-empty">本轮无附加思考上下文</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>
