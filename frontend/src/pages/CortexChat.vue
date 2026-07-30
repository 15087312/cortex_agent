<script setup>
/**
 * Cortex 对话页面
 *
 * 连接 Cortex 后端 (localhost:8000)
 */

import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import { useCortexStore } from '@/stores/cortex.js'
import { useToastStore } from '@/stores/toast.js'
import ChatMessage from '@/components/ChatMessage.vue'
import ChatInput from '@/components/ChatInput.vue'
import SessionList from '@/components/SessionList.vue'
import ModelSelector from '@/components/ModelSelector.vue'
import ThinkingIndicator from '@/components/ThinkingIndicator.vue'

const cortex = useCortexStore()
const toast = useToastStore()
const scrollerRef = ref(null)
const titleEditing = ref(false)
const editTitleVal = ref('')

onMounted(async () => {
  await cortex.loadSessions()
  await cortex.initSession()
})

onUnmounted(() => {
  cortex.disconnect()
})

// ── 滚动 ──

function scrollBottom() {
  nextTick(() => {
    if (scrollerRef.value && cortex.messages.length > 0) {
      scrollerRef.value.scrollToItem(cortex.messages.length - 1)
    }
  })
}

// 流式接收时自动滚屏：监听 streamingContent 而不是 messages.length
// 因为流式更新是改同一条消息的内容，数组长度不变
// 注意：不判断 isStreaming，因为流式结束时 isStreaming 已变 false，
// 但仍需滚动到底部让用户看到完整回复
watch(
  () => cortex.streamingContent,
  () => scrollBottom()
)

// ── 会话操作 ──

async function handleSessionSelect(sid) {
  await cortex.switchSession(sid)
  scrollBottom()
}

async function handleSessionDelete(sid) {
  if (!confirm('确定删除此会话？')) return
  await cortex.deleteSession(sid)
}

async function handleNewSession() {
  await cortex.initSession()
  scrollBottom()
}

// ── 消息操作 ──

function handleSend({ text }) {
  cortex.sendMessage(text)
  scrollBottom()
}

function handleCopyMessage(idx) {
  const msg = cortex.messages[idx]
  if (msg?.content) {
    navigator.clipboard.writeText(msg.content).then(() => {
      toast.show('已复制', 'success')
    }).catch(() => {
      toast.show('复制失败', 'error')
    })
  }
}

function handleDeleteMessage(idx) {
  if (idx >= 0 && idx < cortex.messages.length) {
    cortex.messages.splice(idx, 1)
  }
}
</script>

<template>
  <div style="display:flex;height:100%">
    <!-- 左侧会话列表 -->
    <SessionList
      :sessions="cortex.sessionList"
      :activeId="cortex.sessionId"
      @select="handleSessionSelect"
      @delete="handleSessionDelete"
      @new="handleNewSession"
      style="width:260px;flex-shrink:0"
    />

    <!-- 右侧聊天区 -->
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <!-- 顶栏 -->
      <div class="chat-header">
        <div class="chat-header-left">
          <span class="chat-header-title">{{ cortex.currentTitle }}</span>
          <ModelSelector v-model="cortex.currentModel" />
          <span
            v-if="cortex.isConnected"
            class="badge badge-green"
            style="font-size:11px;margin-left:8px"
          >已连接</span>
          <span
            v-else
            class="badge badge-red"
            style="font-size:11px;margin-left:8px"
          >未连接</span>
        </div>
        <div class="chat-header-right">
          <button
            class="chat-btn-icon"
            @click="cortex.stopGeneration()"
            v-if="cortex.processing"
            title="停止"
          >⏹</button>
          <button
            class="chat-btn-icon"
            @click="cortex.clearMessages()"
            title="清空"
          >🗑</button>
        </div>
      </div>

      <!-- 消息列表（虚拟滚动） -->
      <div class="chat-messages chat-messages-virtual">
        <!-- 空状态 -->
        <div v-if="cortex.messages.length === 0 && !cortex.processing" class="chat-welcome">
          <div class="welcome-icon">🧠</div>
          <h2>Cortex 对话</h2>
          <p>Cortex 具备持久化记忆，会记住你的偏好。<br>试试告诉它"我喜欢吃辣"，下次问"推荐餐厅"它会想起来。</p>
          <div class="quick-actions">
            <div class="quick-action" @click="handleSend({ text: '你好，介绍一下你的能力' })">了解能力</div>
            <div class="quick-action" @click="handleSend({ text: '帮我记住一个偏好' })">存储偏好</div>
          </div>
        </div>

        <!-- 思考中指示器 -->
        <div v-if="cortex.processing && cortex.messages.filter(m => m.role === 'assistant').length === 0">
          <ThinkingIndicator label="Cortex 正在思考..." />
        </div>

        <!-- 虚拟滚动消息列表 -->
        <DynamicScroller
          v-if="cortex.messages.length > 0"
          ref="scrollerRef"
          :items="cortex.messages"
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
                :isStreaming="cortex.isStreaming && index === cortex.streamingIdx"
                @copy="handleCopyMessage"
                @delete="handleDeleteMessage"
              />
            </DynamicScrollerItem>
          </template>
        </DynamicScroller>
      </div>

      <!-- 输入框 -->
      <ChatInput @send="handleSend">
        <template #actions>
          <button
            v-if="cortex.processing"
            class="btn btn-sm"
            style="background:var(--danger);color:white;border-color:var(--danger)"
            @click="cortex.stopGeneration()"
          >⏹ 停止</button>
        </template>
      </ChatInput>
    </div>
  </div>
</template>
