import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../stores/chatStore.ts'

export function useWebSocket(sessionId: string | null) {
  const wsRef = useRef<WebSocket | null>(null)
  const { appendToken, addMessage, setStreaming } = useChatStore()

  useEffect(() => {
    if (!sessionId) return

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const ws = new WebSocket(`${protocol}//${host}/ws/${sessionId}`)

    ws.onopen = () => {
      console.log('WebSocket connected')
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'token' && msg.content) {
          appendToken(sessionId, msg.content)
        } else if (msg.type === 'done') {
          // Clean up streaming marker
          const msgs = useChatStore.getState().messages[sessionId] || []
          const last = msgs[msgs.length - 1]
          if (last && last.role === 'assistant') {
            last.content = last.content.replace('...streaming', '')
          }
          setStreaming(false)
        } else if (msg.type === 'error') {
          addMessage(sessionId, { role: 'system', content: `Error: ${msg.content}` })
          setStreaming(false)
        }
      } catch (e) {
        console.error('Failed to parse WS message:', e)
      }
    }

    ws.onclose = () => {
      console.log('WebSocket disconnected')
    }

    wsRef.current = ws

    return () => {
      ws.close()
      wsRef.current = null
    }
  }, [sessionId, appendToken, addMessage, setStreaming])

  const sendMessage = useCallback(
    (content: string) => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
      setStreaming(true)
      wsRef.current.send(JSON.stringify({ type: 'message', content }))
    },
    [setStreaming]
  )

  return { sendMessage }
}
