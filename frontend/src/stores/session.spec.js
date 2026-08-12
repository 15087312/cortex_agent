import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSessionStore } from './session.js'
import { endpoints } from '@/api.js'

describe('useSessionStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('loadSessions 成功填充列表', async () => {
    const spy = vi.spyOn(endpoints, 'sessions').mockResolvedValue({ data: [{ session_id: 'a' }] })
    const s = useSessionStore()
    await s.loadSessions()
    expect(s.sessions).toEqual([{ session_id: 'a' }])
    spy.mockRestore()
  })

  it('loadSessions 失败置空', async () => {
    const spy = vi.spyOn(endpoints, 'sessions').mockRejectedValue(new Error('x'))
    const s = useSessionStore()
    await s.loadSessions()
    expect(s.sessions).toEqual([])
    spy.mockRestore()
  })

  it('createSession 成功后设置 sessionId 并刷新列表', async () => {
    const spy = vi.spyOn(endpoints, 'createSession').mockResolvedValue({ data: { session_id: 'new1' } })
    const spyL = vi.spyOn(endpoints, 'sessions').mockResolvedValue({ data: [] })
    const s = useSessionStore()
    const id = await s.createSession()
    expect(id).toBe('new1')
    expect(s.sessionId).toBe('new1')
    expect(spyL).toHaveBeenCalled()
    spy.mockRestore(); spyL.mockRestore()
  })

  it('createSession 失败生成本地回退 id', async () => {
    const spy = vi.spyOn(endpoints, 'createSession').mockRejectedValue(new Error('x'))
    const s = useSessionStore()
    const id = await s.createSession()
    expect(id).toMatch(/^session_/)
    spy.mockRestore()
  })

  it('switchSession 切换当前会话', () => {
    const s = useSessionStore()
    s.switchSession('sid2')
    expect(s.sessionId).toBe('sid2')
  })

  it('loadDialog 返回对话列表', async () => {
    const spy = vi.spyOn(endpoints, 'sessionDialog').mockResolvedValue({ data: { dialog: [{ role: 'user' }] } })
    const s = useSessionStore()
    const d = await s.loadDialog('s1')
    expect(d).toEqual([{ role: 'user' }])
    spy.mockRestore()
  })

  it('deleteSession 删除并过滤列表', async () => {
    const spy = vi.spyOn(endpoints, 'deleteSession').mockResolvedValue({})
    const s = useSessionStore()
    s.sessions = [{ session_id: 'a' }, { session_id: 'b' }]
    await s.deleteSession('a')
    expect(s.sessions.map(x => x.session_id)).toEqual(['b'])
    spy.mockRestore()
  })
})
