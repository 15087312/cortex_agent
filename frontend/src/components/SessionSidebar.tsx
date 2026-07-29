import { useEffect } from 'react'
import { useChatStore } from '../stores/chatStore.ts'
import { fetchSessions, createSession, deleteSession } from '../services/api.ts'

export default function SessionSidebar() {
  const { sessions, activeSessionId, setSessions, selectSession } = useChatStore()

  useEffect(() => {
    loadSessions()
  }, [])

  async function loadSessions() {
    const sessions = await fetchSessions()
    setSessions(sessions)
  }

  async function handleCreate() {
    const id = await createSession()
    await loadSessions()
    selectSession(id)
  }

  async function handleDelete(e: React.MouseEvent, sessionId: string) {
    e.stopPropagation()
    if (!confirm('Delete this session?')) return
    await deleteSession(sessionId)
    if (activeSessionId === sessionId) selectSession(null)
    await loadSessions()
  }

  return (
    <div className="w-64 bg-gray-800 border-r border-gray-700 flex flex-col">
      <div className="p-4 border-b border-gray-700">
        <h2 className="text-lg font-semibold mb-3">Cortex</h2>
        <button
          onClick={handleCreate}
          className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg text-sm font-medium transition-colors"
        >
          + New Chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-2">
        {sessions.map((session) => (
          <div
            key={session.session_id}
            onClick={() => selectSession(session.session_id)}
            className={`group flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer mb-1 transition-colors ${
              activeSessionId === session.session_id
                ? 'bg-gray-700'
                : 'hover:bg-gray-750 hover:bg-gray-700/50'
            }`}
          >
            <span className="text-sm truncate flex-1">
              {session.title || 'New Chat'}
            </span>
            <button
              onClick={(e) => handleDelete(e, session.session_id)}
              className="opacity-0 group-hover:opacity-100 text-gray-400 hover:text-red-400 text-xs ml-2 transition-opacity"
            >
              x
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
