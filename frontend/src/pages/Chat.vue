<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import { useChatStore } from '@/stores/chat.js'
import { useSessionStore } from '@/stores/session.js'
import { useToastStore } from '@/stores/toast.js'
import { useWsStore } from '@/ws/store.js'
import ChatMessage from '@/components/ChatMessage.vue'
import ChatInput from '@/components/ChatInput.vue'
import SessionList from '@/components/SessionList.vue'
import ModelSelector from '@/components/ModelSelector.vue'
import ThinkingIndicator from '@/components/ThinkingIndicator.vue'

const chat = useChatStore()
const session = useSessionStore()
const ws = useWsStore()
const toast = useToastStore()
const scrollerRef = ref(null)

const _onThinking = (d) => {
  if (d.event === 'thinking_step' && d.content) {
    chat.handleStreamContent(d.content)
    scrollBottom()
  }
}
const _onMessage = (d) => {
  chat.finalizeStream(d.content || '')
  scrollBottom()
}
const _onDone = () => { chat.finalizeStream('') }
const _onError = (d) => {
  chat.finalizeStream('')
  toast.show('错误: ' + (d.content || '未知'), 'error')
}

function scrollBottom() {
  nextTick(() => {
    if (scrollerRef.value && chat.messages.length > 0) {
      scrollerRef.value.scrollToItem(chat.messages.length - 1)
    }
  })
}

onMounted(async () => {
  await session.loadSessions()
  await chat.init()
  ws.wsClient.on('thinking', _onThinking)
  ws.wsClient.on('message', _onMessage)
  ws.wsClient.on('done', _onDone)
  ws.wsClient.on('error', _onError)
})

onUnmounted(() => {
  ws.wsClient.off('thinking', _onThinking)
  ws.wsClient.off('message', _onMessage)
  ws.wsClient.off('done', _onDone)
  ws.wsClient.off('error', _onError)
})

function handleSend({ text, attachments }) {
  chat.sendMessage(text, attachments)
  chat.addMessage({ role: 'user', content: text })
  chat.processing = true
  scrollBottom()
}

async function handleSessionSelect(sid) {
  await chat.switchToSession(sid)
  scrollBottom()
}

function handleSessionDelete(sid) {
  if (!confirm('确定删除此会话？')) return
  session.deleteSession(sid)
  if (sid === session.sessionId) chat.init()
}

function handleCopyMessage(idx) {
  const msg = chat.messages[idx]
  if (msg?.content) {
    navigator.clipboard.writeText(msg.content).then(() => toast.show('已复制', 'success'))
  }
}

function handleDeleteMessage(idx) {
  if (idx >= 0 && idx < chat.messages.length) {
    chat.messages.splice(idx, 1)
  }
}
</script>

<template>
  <div style="display:flex;height:100%">
    <SessionList
      :sessions="session.sessions"
      :activeId="session.sessionId"
      @select="handleSessionSelect"
      @delete="handleSessionDelete"
      @new="chat.init()"
      style="width:260px;flex-shrink:0"
    />
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <div class="chat-header">
        <div class="chat-header-left">
          <span class="chat-header-title">{{ session.currentTitle }}</span>
          <ModelSelector v-model="chat.currentModel" />
        </div>
        <div class="chat-header-right">
          <button class="chat-btn-icon" @click="chat.stop()" v-if="chat.processing" title="停止">⏹</button>
          <button class="chat-btn-icon" @click="chat.clearMessages()" title="清空">🗑</button>
        </div>
      </div>

      <!-- 消息区：无消息时显示欢迎/思考，有消息时使用虚拟滚动 -->
      <div class="chat-messages chat-messages-virtual">
        <div v-if="chat.messages.length === 0 && !chat.processing" class="chat-welcome">
          <div class="welcome-icon">💬</div>
          <h2>开始新对话</h2>
          <p>输入消息开始聊天，支持多模态文件上传和流式回复。</p>
          <div class="quick-actions">
            <div class="quick-action" @click="handleSend({ text: '你好，请介绍一下你自己', attachments: [] })">打招呼</div>
            <div class="quick-action" @click="handleSend({ text: '帮我分析一下项目结构', attachments: [] })">分析项目</div>
            <div class="quick-action" @click="handleSend({ text: '给我写一段代码', attachments: [] })">写代码</div>
          </div>
        </div>
        <div v-if="chat.processing && chat.messages.filter(m => m.role === 'assistant').length === 0">
          <ThinkingIndicator label="正在思考" />
        </div>

        <DynamicScroller
          v-if="chat.messages.length > 0"
          ref="scrollerRef"
          :items="chat.messages"
          :min-item-size="80"
          key-field="_id"
          class="chat-scroller"
        >
          <template #default="{ item, index, active }">
            <DynamicScrollerItem
              :item="item"
              :active="active"
              :size-dependencies="[item.content]"
              :data-index="index"
            >
              <ChatMessage
                :message="item"
                :index="index"
                :isStreaming="ws.isStreaming && index === chat.streamingIdx"
                @copy="handleCopyMessage"
                @delete="handleDeleteMessage"
              />
            </DynamicScrollerItem>
          </template>
        </DynamicScroller>
      </div>

      <ChatInput @send="handleSend">
        <template #actions>
          <button v-if="chat.processing" class="btn btn-sm" style="background:var(--danger);color:white;border-color:var(--danger)" @click="chat.stop()">⏹ 停止</button>
        </template>
      </ChatInput>
    </div>
  </div>
</template>
