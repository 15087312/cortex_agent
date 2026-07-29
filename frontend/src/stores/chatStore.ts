import { create } from 'zustand'

export interface Session {
  session_id: string
  title: string
  created_at: string
  last_active: string
  message_count: number
  is_active: boolean
}

export interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at?: string
}

interface ChatStore {
  sessions: Session[]
  activeSessionId: string | null
  messages: Record<string, Message[]>
  isStreaming: boolean

  setSessions: (sessions: Session[]) => void
  selectSession: (id: string | null) => void
  addMessage: (sessionId: string, message: Message) => void
  setMessages: (sessionId: string, messages: Message[]) => void
  setStreaming: (v: boolean) => void
  appendToken: (sessionId: string, token: string) => void
}

export const useChatStore = create<ChatStore>((set) => ({
  sessions: [],
  activeSessionId: null,
  messages: {},
  isStreaming: false,

  setSessions: (sessions) => set({ sessions }),
  selectSession: (activeSessionId) => set({ activeSessionId }),

  addMessage: (sessionId, message) =>
    set((state) => ({
      messages: {
        ...state.messages,
        [sessionId]: [...(state.messages[sessionId] || []), message],
      },
    })),

  setMessages: (sessionId, messages) =>
    set((state) => ({
      messages: { ...state.messages, [sessionId]: messages },
    })),

  setStreaming: (isStreaming) => set({ isStreaming }),

  appendToken: (sessionId, token) =>
    set((state) => {
      const msgs = [...(state.messages[sessionId] || [])]
      const last = msgs[msgs.length - 1]
      if (last && last.role === 'assistant' && last.content.endsWith('...streaming')) {
        last.content = last.content.replace('...streaming', '') + token
      } else {
        msgs.push({ role: 'assistant', content: token + '...streaming' })
      }
      return { messages: { ...state.messages, [sessionId]: msgs } }
    }),
}))
