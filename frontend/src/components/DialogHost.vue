<script setup>
import { ref, watch, nextTick } from 'vue'
import { dialogState, resolveDialog } from '@/composables/useDialog.js'

const state = dialogState()
const input = ref('')
const inputRef = ref(null)

watch(() => state.visible, async (v) => {
  if (v) {
    input.value = state.defaultValue || ''
    await nextTick()
    if (state.type === 'prompt' && inputRef.value) {
      inputRef.value.focus()
      inputRef.value.select()
    }
  }
})

function ok() {
  resolveDialog(state.type === 'prompt' ? input.value : true)
}

function cancel() {
  resolveDialog(state.type === 'prompt' ? null : false)
}
</script>

<template>
  <div v-if="state.visible" class="modal-overlay" @click.self="cancel">
    <div class="modal" :style="{ minWidth: state.type === 'prompt' ? '360px' : '320px' }">
      <h3>{{ state.title }}</h3>
      <p v-if="state.type === 'confirm'" style="color:var(--text-secondary);font-size:14px;line-height:1.6;white-space:pre-wrap">{{ state.message }}</p>
      <input
        v-if="state.type === 'prompt'"
        ref="inputRef"
        v-model="input"
        class="input"
        style="width:100%;margin:12px 0"
        @keydown.enter="ok"
        @keydown.esc="cancel"
      />
      <div class="modal-actions">
        <button class="btn" @click="cancel">取消</button>
        <button class="btn" :class="state.type === 'confirm' ? 'btn-danger' : 'btn-primary'" @click="ok">确定</button>
      </div>
    </div>
  </div>
</template>
