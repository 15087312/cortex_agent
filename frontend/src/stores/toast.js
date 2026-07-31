import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useToastStore = defineStore('toast', () => {
  const toasts = ref([])
  let _id = 0

  function show(msg, type = 'info') {
    const id = ++_id
    toasts.value.push({ id, msg, type })
    setTimeout(() => dismiss(id), 3500)
  }

  function dismiss(id) {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  return { toasts, show, dismiss }
})
