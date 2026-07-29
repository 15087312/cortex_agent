import { useEffect } from 'react'
import { useChatStore } from '../stores/chatStore.ts'
import { fetchMessages } from '../services/api.ts'
import { useWebSocket } from '../hooks/useWebSocket.ts'
import MessageList from './MessageList.tsx'
import InputBar from './InputBar.tsx'

interface Props {
  sessionId: string
}

export default function ChatWindow({ sessionId }: Props) {
  const { messages, isStreaming, setMessages, addMessage } = useChatStore()
  const { sendMessage } = useWebSocket(sessionId)

  const sessionMessages = messages[sessionId] || []

  useEffect(() => {
    loadMessages()
  }, [sessionId])

  async function loadMessages() {
    const msgs = await fetchMessages(sessionId)
    setMessages(sessionId, msgs)
  }

  function handleSend(content: string) {
    addMessage(sessionId, { role: 'user', content })
    sendMessage(content)
  }

  return (
    <div className="flex flex-col h-full">
      <div className="border-b border-gray-700 px-4 py-3">
        <h3 className="text-sm font-medium text-gray-300">Chat Session</h3>
      </div>
      <MessageList messages={sessionMessages} isStreaming={isStreaming} />
      <InputBar onSend={handleSend} disabled={isStreaming} />
    </div>
  )
}
