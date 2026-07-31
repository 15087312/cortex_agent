import { defineStore } from 'pinia'
import { ref } from 'vue'
import { endpoints } from '@/api.js'

export const useSessionStore = defineStore('session', () => {
  const sessionId = ref(null)
  const sessions = ref([])
  const currentTitle = ref('新会话')

  async function loadSessions() {
    try { const r = await endpoints.sessions(); sessions.value = r.data || [] } catch { sessions.value = [] }
  }

  async function createSession() {
    try {
      const r = await endpoints.createSession()
      sessionId.value = r.data.session_id
    } catch {
      sessionId.value = 'session_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8)
    }
    currentTitle.value = '新会话'
    await loadSessions()
    return sessionId.value
  }

  async function switchSession(sid) {
    sessionId.value = sid
  }

  async function loadDialog(sid) {
    try {
      const r = await endpoints.sessionDialog(sid, 100)
      return r.data?.dialog || []
    } catch { return [] }
  }

  async function deleteSession(sid) {
    try { await endpoints.deleteSession(sid) } catch {}
    sessions.value = sessions.value.filter(s => s.session_id !== sid)
  }

  return {
    sessionId, sessions, currentTitle,
    loadSessions, createSession, switchSession, loadDialog, deleteSession,
  }
})
