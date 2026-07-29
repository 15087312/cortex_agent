import { useChatStore } from './stores/chatStore.ts'
import SessionSidebar from './components/SessionSidebar.tsx'
import ChatWindow from './components/ChatWindow.tsx'

function App() {
  const activeSessionId = useChatStore((s) => s.activeSessionId)

  return (
    <div className="flex h-screen">
      <SessionSidebar />
      <div className="flex-1 flex flex-col">
        {activeSessionId ? (
          <ChatWindow sessionId={activeSessionId} />
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-500">
            <div className="text-center">
              <h1 className="text-4xl font-bold mb-4">Cortex</h1>
              <p className="text-lg">Select a session or create a new one</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default App
