const BASE = '/api'

export async function fetchSessions() {
  const res = await fetch(`${BASE}/sessions`)
  const data = await res.json()
  return data.sessions || []
}

export async function createSession() {
  const res = await fetch(`${BASE}/sessions`, { method: 'POST' })
  const data = await res.json()
  return data.session_id
}

export async function deleteSession(sessionId: string) {
  await fetch(`${BASE}/sessions/${sessionId}`, { method: 'DELETE' })
}

export async function fetchMessages(sessionId: string) {
  const res = await fetch(`${BASE}/sessions/${sessionId}/messages`)
  const data = await res.json()
  return data.messages || []
}
