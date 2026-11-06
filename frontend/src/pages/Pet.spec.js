import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Pet from './Pet.vue'
import { routeFetch } from '@/test/helpers.js'

let wrapper = null
function mountPage() {
  wrapper = mount(Pet, { global: { plugins: [createPinia()] } })
  return wrapper
}

// SSE 响应 mock：帧数组（如 'data: {"token":"你"}\n\n'）→ ReadableStream
function sseMock(frames) {
  globalThis.fetch = vi.fn(async () => sseMockResponse(frames))
}

function sseMockResponse(frames) {
  const enc = new TextEncoder()
  const body = new ReadableStream({
    start(c) {
      frames.forEach((f) => c.enqueue(enc.encode(f)))
      c.close()
    },
  })
  return { ok: true, status: 200, body }
}

const BASE_ROUTES = (extra = []) => [
  { match: '/stream/pet/history', data: { data: { items: [] } } },
  { match: '/stream/pet/config', data: { data: {} } },
  { match: '/stream/pet/memory', data: { data: { items: [] } } },
  ...extra,
]

function tick() {
  return new Promise((r) => setTimeout(r, 30))
}

describe('Pet 页面', () => {
  afterEach(() => {
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })

  it('渲染三个 Tab，默认聊天页，并加载历史/配置/记忆', async () => {
    routeFetch([
      { match: '/stream/pet/history', data: { data: { items: [{ role: 'user', content: '嗨' }] } } },
      { match: '/stream/pet/config', data: { data: { persona: '猫娘', api_key_set: true, loop_type: 'custom', memory_enabled: true } } },
      { match: '/stream/pet/memory', data: { data: { items: [{ id: 'm1', kind: 'fact', text: '喜欢牛奶', created_at: 1700000000 }] } } },
    ])
    const w = mountPage()
    await tick()
    await w.vm.$nextTick()

    expect(w.text()).toContain('聊天')
    expect(w.text()).toContain('设置')
    expect(w.text()).toContain('记忆')
    expect(w.vm.tab).toBe('chat')
    expect(w.vm.messages.length).toBe(1)
    expect(w.vm.messages[0].content).toBe('嗨')
    expect(w.vm.cfg.persona).toBe('猫娘')
    expect(w.vm.cfg.api_key_set).toBe(true)
    expect(w.vm.cfg.loop_type).toBe('custom')
    expect(w.vm.memories.length).toBe(1)
    expect(w.vm.memories[0].text).toBe('喜欢牛奶')
    // 切到记忆 Tab 时列表渲染
    w.vm.tab = 'memory'
    await w.vm.$nextTick()
    expect(w.text()).toContain('喜欢牛奶')
  })

  it('切换 Tab 渲染设置页与记忆页', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    w.vm.tab = 'settings'
    await w.vm.$nextTick()
    expect(w.text()).toContain('API 配置（独立）')
    expect(w.text()).toContain('ReAct 循环')
    w.vm.tab = 'memory'
    await w.vm.$nextTick()
    expect(w.text()).toContain('暂无独立记忆')
  })

  it('桌宠总开关只在设置 Tab，页眉不再出现按钮', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    await w.vm.$nextTick()
    // 页眉不再有开启/关闭按钮，只保留运行状态徽标
    expect(w.findAll('.pet-header button').filter((b) => b.text().includes('桌面宠物')).length).toBe(0)
    // 聊天 Tab 不出现总开关
    expect(w.find('[data-test="pet-master-toggle"]').exists()).toBe(false)
    // 设置 Tab 有且仅有一个总开关
    w.vm.tab = 'settings'
    await w.vm.$nextTick()
    expect(w.find('[data-test="pet-master-toggle"]').exists()).toBe(true)
    expect(w.text()).toContain('启用桌宠')
  })

  it('发送消息：SSE 逐 token 追加到回复', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    sseMock(['data: {"token":"你好"}\n\n', 'data: {"token":"！"}\n\n'])
    w.vm.input = '在吗'
    await w.vm.send()
    await w.vm.$nextTick()

    expect(w.vm.messages[w.vm.messages.length - 1].role).toBe('assistant')
    expect(w.vm.messages[w.vm.messages.length - 1].content).toBe('你好！')
    expect(w.vm.sending).toBe(false)
  })

  it('发送消息：兼容 sse-starlette 的 CRLF 帧分隔（\\r\\n\\r\\n）', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    // sse-starlette 默认 sep="\r\n"，帧尾为 \r\n\r\n；此前前端按 \n\n 切分 → 一帧都解析不到
    sseMock([
      'event: token\r\ndata: {"token":"你好"}\r\n\r\n',
      'event: token\r\ndata: {"token":"！"}\r\n\r\n',
      'event: round_complete\r\ndata: {"round_complete":true}\r\n\r\n',
      'event: done\r\ndata: {"audio_url":""}\r\n\r\n',
    ])
    w.vm.input = '在吗'
    await w.vm.send()
    await w.vm.$nextTick()

    expect(w.vm.messages[1].content).toBe('你好！')
    expect(w.vm.sending).toBe(false)
  })

  it('发送消息：CRLF 帧中的思考过程（chunk）正常展示', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    sseMock([
      'event: thinking\r\ndata: {"chunk":"先想想"}\r\n\r\n',
      'event: token\r\ndata: {"token":"好了"}\r\n\r\n',
      'event: round_complete\r\ndata: {"round_complete":true}\r\n\r\n',
    ])
    w.vm.input = 'hi'
    await w.vm.send()
    await w.vm.$nextTick()
    expect(w.vm.messages[1].thinking).toBe('先想想')
    expect(w.vm.messages[1].content).toBe('好了')
  })

  it('SSE 返回错误事件且无 token 时展示错误文案', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    sseMock(['data: {"error":"模型超时"}\n\n'])
    w.vm.input = 'hi'
    await w.vm.send()
    await w.vm.$nextTick()
    const last = w.vm.messages[w.vm.messages.length - 1]
    expect(last.content).toContain('模型超时')
  })

  it('SSE 错误：错误气泡保留，且不再自动重拉历史覆盖', async () => {
    const { endpoints } = await import('@/api.js')
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    const histSpy = vi.spyOn(endpoints, 'petHistory')
    sseMock([
      'event: error\ndata: {"error":"402 insufficient balance"}\n\n',
      'event: done\ndata: {}\n\n',
    ])
    w.vm.input = 'hi'
    await w.vm.send()
    await w.vm.$nextTick()
    const last = w.vm.messages[w.vm.messages.length - 1]
    expect(last.content).toContain('402')
    expect(histSpy).not.toHaveBeenCalled()
    histSpy.mockRestore()
  })

  it('切换人设：调用 activate 并重新拉取该人设会话', async () => {
    const { endpoints } = await import('@/api.js')
    routeFetch([
      { match: '/stream/pet/personas/2/activate', data: { success: true } },
      {
        match: '/stream/pet/personas',
        data: { data: { items: [
          { id: 1, name: '默认人设', prompt: 'p1' },
          { id: 2, name: '猫娘', prompt: 'p2' },
        ], active_id: 1 } },
      },
      ...BASE_ROUTES(),
    ])
    const w = mountPage()
    await tick()
    const sel = w.find('.pet-persona-select')
    expect(sel.exists()).toBe(true)
    expect(sel.findAll('option').length).toBe(2)
    const histSpy = vi.spyOn(endpoints, 'petHistory')
    await sel.setValue(2)
    await tick()
    expect(w.vm.activePersonaId).toBe(2)
    expect(histSpy).toHaveBeenCalled()
    histSpy.mockRestore()
  })

  it('一键清空：确认后清空当前人设聊天记录', async () => {
    const { dialogState, resolveDialog } = await import('@/composables/useDialog.js')
    routeFetch([
      {
        match: (u, init) => u.includes('/stream/pet/history') && init?.method === 'DELETE',
        data: { success: true },
      },
      { match: '/stream/pet/history', data: { data: { items: [
        { id: 1, role: 'user', content: '你好' },
        { id: 2, role: 'assistant', content: '喵' },
      ] } } },
      ...BASE_ROUTES(),
    ])
    const w = mountPage()
    await tick()
    expect(w.vm.messages.length).toBe(2)
    await w.find('.pet-header .chat-btn-icon').trigger('click')
    await w.vm.$nextTick()
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await tick()
    expect(w.vm.messages.length).toBe(0)
  })

  it('空输入不发送', async () => {
    const fetchSpy = vi.fn(async () => ({ ok: true, json: async () => ({}) }))
    globalThis.fetch = fetchSpy
    const w = mountPage()
    await tick()
    const callsBefore = fetchSpy.mock.calls.length // onMounted 3 次
    w.vm.input = '   '
    await w.vm.send()
    expect(fetchSpy.mock.calls.length).toBe(callsBefore)
    expect(w.vm.sending).toBe(false)
  })

  it('SSE 结束时清理未产出内容的空白气泡（round_complete 尾随空消息）', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    // direct 循环实际序列：token…→ round_complete → done；done 时尾部无内容的消息应被移除
    sseMock([
      'event: token\r\ndata: {"token":"你好"}\r\n\r\n',
      'event: round_complete\r\ndata: {"round_complete":true}\r\n\r\n',
      'event: done\r\ndata: {"state":{}}\r\n\r\n',
    ])
    w.vm.input = '在吗'
    await w.vm.send()
    await w.vm.$nextTick()
    const contents = w.vm.messages.map((m) => m.content)
    expect(contents).toEqual(['在吗', '你好']) // 没有第三条空白 assistant
  })

  it('连续思考：custom 循环 max_loop_rounds=0 时 SSE 结束后自动发起下一轮', async () => {
    vi.useFakeTimers()
    try {
      routeFetch([
        { match: '/stream/pet/history', data: { data: { items: [] } } },
        { match: '/stream/pet/config', data: { data: { loop_type: 'custom', max_loop_rounds: 0, min_think_interval: 1 } } },
        { match: '/stream/pet/memory', data: { data: { items: [] } } },
      ])
      const w = mountPage()
      await vi.advanceTimersByTimeAsync(100)
      await w.vm.$nextTick()
      // 页面加载 1.5s 后的自动首轮：改为对 /pet/chat 的 POST 建独立 spy（routeFetch
      // 覆盖了 fetch，直接给 chat 响应一个空 SSE 流）
      const chatStreams = []
      const prevFetch = globalThis.fetch
      globalThis.fetch = async (url, init) => {
        if (String(url).includes('/pet/chat')) {
          chatStreams.push(url)
          return sseMockResponse([])
        }
        return prevFetch(url, init)
      }
      // 手动触发首轮（会取消待启动的自动首轮），验证 SSE 结束后的自动续轮链路
      await w.vm.send('')
      await vi.advanceTimersByTimeAsync(100)
      expect(chatStreams.length).toBe(1)
      // SSE 已结束（空流），续轮延迟由 min_think_interval（mock 为 1s）控制 → 应自动续轮
      await vi.advanceTimersByTimeAsync(2200)
      expect(chatStreams.length).toBeGreaterThanOrEqual(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('总开关开启自动定时回复；关闭后停止且不再回复', async () => {
    const { endpoints } = await import('@/api.js')
    const updSpy = vi.spyOn(endpoints, 'updateConfig').mockResolvedValue({ success: true })
    const startSpy = vi.spyOn(endpoints, 'petWindowStart').mockResolvedValue({ success: true, data: { ok: true } })
    const stopSpy = vi.spyOn(endpoints, 'petWindowStop').mockResolvedValue({ success: true, data: { ok: true } })
    const statusSpy = vi.spyOn(endpoints, 'petWindowStatus').mockResolvedValue({ success: true, data: { running: true } })
    vi.useFakeTimers()
    try {
      routeFetch([
        { match: '/stream/pet/history', data: { data: { items: [] } } },
        { match: '/stream/pet/config', data: { data: { loop_type: 'custom', max_loop_rounds: 0, min_think_interval: 1 } } },
        { match: '/stream/pet/memory', data: { data: { items: [] } } },
        { match: '/config/DESKTOP_PET_ENABLED', data: { value: true } },
      ])
      const chatStreams = []
      const prevFetch = globalThis.fetch
      globalThis.fetch = async (url, init) => {
        if (String(url).includes('/pet/chat')) {
          chatStreams.push(url)
          return sseMockResponse([])
        }
        return prevFetch(url, init)
      }
      const w = mountPage()
      // 开启：页面加载后自动发起首轮
      await vi.advanceTimersByTimeAsync(300)
      expect(chatStreams.length).toBe(1)
      // 定时续轮（min_think_interval=1s）
      await vi.advanceTimersByTimeAsync(1100)
      expect(chatStreams.length).toBeGreaterThanOrEqual(2)
      // 关闭：停止自动循环，后续不再请求
      await w.vm.setMasterEnabled(false)
      const afterOff = chatStreams.length
      await vi.advanceTimersByTimeAsync(3000)
      expect(chatStreams.length).toBe(afterOff)
      expect(w.vm.masterEnabled).toBe(false)
      expect(w.find('.pet-input').attributes('disabled')).toBeDefined()
      // 关闭时手动发送也不回复
      await w.vm.send('你好')
      await vi.advanceTimersByTimeAsync(100)
      expect(chatStreams.length).toBe(afterOff)
      // 重新开启：自动回复恢复
      await w.vm.setMasterEnabled(true)
      await vi.advanceTimersByTimeAsync(300)
      expect(chatStreams.length).toBeGreaterThan(afterOff)
    } finally {
      vi.useRealTimers()
      updSpy.mockRestore()
      startSpy.mockRestore()
      stopSpy.mockRestore()
      statusSpy.mockRestore()
    }
  })

  it('停止发送：abort 后 sending 复位', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    // 永不结束的流：abort 时以 AbortError 结束，模拟浏览器 fetch 行为
    let resolveAbort
    const aborted = new Promise((r) => { resolveAbort = r })
    const body = new ReadableStream({
      start(c) {
        aborted.then(() => c.error(new DOMException('Aborted', 'AbortError')))
      },
    })
    globalThis.fetch = vi.fn(async (url, init) => {
      if (init && init.signal) {
        init.signal.addEventListener('abort', () => resolveAbort())
      }
      return { ok: true, status: 200, body }
    })
    w.vm.input = 'hello'
    const p = w.vm.send()
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.sending).toBe(true)
    w.vm.stopSend()
    await p
    expect(w.vm.sending).toBe(false)
  })

  it('保存配置：PUT 后 toast 成功并重新加载配置', async () => {
    let putCalled = false
    routeFetch([
      { match: '/stream/pet/history', data: { data: { items: [] } } },
      { match: '/stream/pet/memory', data: { data: { items: [] } } },
      {
        match: '/stream/pet/config',
        data: (u, init) => {
          if (init && init.method === 'PUT') { putCalled = true; return { success: true } }
          return { data: { persona: '新的人设', loop_type: 'direct', memory_enabled: true } }
        },
      },
    ])
    const w = mountPage()
    await tick()
    w.vm.cfg.persona = '新的人设'
    w.vm.apiKeyDraft = 'sk-new-key'
    await w.vm.saveConfig()

    expect(putCalled).toBe(true)
    expect(w.vm.cfg.persona).toBe('新的人设')
    expect(w.vm.apiKeyDraft).toBe('')
    expect(w.vm.savingCfg).toBe(false)
  })

  it('保存配置失败：toast 错误且 savingCfg 复位', async () => {
    routeFetch([
      { match: '/stream/pet/history', data: { data: { items: [] } } },
      { match: '/stream/pet/memory', data: { data: { items: [] } } },
      { match: '/stream/pet/config', data: (u, init) => (init && init.method === 'PUT' ? { success: false } : { data: {} }) },
    ])
    const w = mountPage()
    await tick()
    await w.vm.saveConfig()
    expect(w.vm.savingCfg).toBe(false)
  })

  it('清空历史：DELETE 后 messages 清空', async () => {
    routeFetch([
      { match: '/stream/pet/history', data: (u, init) => (init && init.method === 'DELETE' ? { success: true } : { data: { items: [{ role: 'user', content: 'x' }] } }) },
      { match: '/stream/pet/config', data: { data: {} } },
      { match: '/stream/pet/memory', data: { data: { items: [] } } },
    ])
    const w = mountPage()
    await tick()
    expect(w.vm.messages.length).toBe(1)
    await w.vm.clearHistory()
    expect(w.vm.messages.length).toBe(0)
  })

  it('删除单条消息：DELETE /stream/pet/history/{id} 后本地移除', async () => {
    const items = [
      { id: 1, role: 'user', content: '问1' },
      { id: 2, role: 'assistant', content: '答1' },
    ]
    const deleted = []
    routeFetch([
      {
        match: '/stream/pet/history',
        data: (u, init) => {
          if (init && init.method === 'DELETE') { deleted.push(String(u)); return { success: true } }
          return { data: { items } }
        },
      },
      { match: '/stream/pet/config', data: { data: {} } },
      { match: '/stream/pet/memory', data: { data: { items: [] } } },
    ])
    const w = mountPage()
    await tick()
    expect(w.vm.messages.length).toBe(2)
    // 历史消息带 id，可逐条删除
    expect(w.vm.messages[1].id).toBe(2)
    await w.vm.delMessage(w.vm.messages[1])
    expect(deleted[0]).toContain('/stream/pet/history/2')
    expect(w.vm.messages.length).toBe(1)
    expect(w.vm.messages[0].content).toBe('问1')
  })

  it('添加记忆：POST 后重新加载列表并清空输入', async () => {
    let memCount = 0
    routeFetch([
      { match: '/stream/pet/history', data: { data: { items: [] } } },
      { match: '/stream/pet/config', data: { data: {} } },
      {
        match: '/stream/pet/memory',
        data: (u, init) => {
          if (init && init.method === 'POST') return { success: true }
          memCount += 1
          return { data: { items: memCount > 1 ? [{ id: 'm1', kind: 'preference', text: '喜欢晒太阳' }] : [] } }
        },
      },
    ])
    const w = mountPage()
    await tick()
    w.vm.memKind = 'preference'
    w.vm.memText = '喜欢晒太阳'
    await w.vm.addMemory()
    expect(w.vm.memText).toBe('')
    expect(w.vm.memories.length).toBe(1)
    expect(w.vm.memories[0].kind).toBe('preference')
  })

  it('删除记忆：DELETE 后列表移除该项', async () => {
    let memItems = [{ id: 'm1', kind: 'fact', text: '旧记忆' }]
    routeFetch([
      { match: '/stream/pet/history', data: { data: { items: [] } } },
      { match: '/stream/pet/config', data: { data: {} } },
      {
        match: '/stream/pet/memory',
        data: (u, init) => {
          if (init && init.method === 'DELETE') { memItems = []; return { success: true } }
          if (init && init.method === 'POST') return { success: true }
          return { data: { items: memItems } }
        },
      },
    ])
    const w = mountPage()
    await tick()
    expect(w.vm.memories.length).toBe(1)
    await w.vm.delMemory('m1')
    expect(w.vm.memories.length).toBe(0)
  })

  it('智能滚动：流式 token 输出时不强制拽回底部（用户上翻可看历史）', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    // 模拟滚动容器：不在底部（scrollHeight 远大于可视区）
    const el = { scrollTop: 0, scrollHeight: 5000, clientHeight: 400 }
    w.vm.messagesWrap = el
    // 触发 scroll 事件逻辑：距底 4600px > 60px → stickToBottom = false
    w.vm.onMessagesScroll()
    const before = el.scrollTop
    sseMock(['data: {"token":"你好"}\n\n'])
    w.vm.input = '在吗'
    await w.vm.send()
    await w.vm.$nextTick()
    // 用户停在上方 → scrollTop 不被强制改写（scrollDown 直接 return）
    expect(el.scrollTop).toBe(before)
    // 回到底部后恢复跟随
    el.scrollTop = 4600
    w.vm.onMessagesScroll()
    expect(w.vm.messages[w.vm.messages.length - 1].content).toBe('你好')
  })

  it('互动接入：加载动作，送礼物点击发送 action_id 并流式回复（不再轮询宠物状态）', async () => {
    let chatBody = null
    let chatCalled = false
    let stateCalls = 0
    const PROMPT = '你送来了一个美味的蛋糕给桌宠吃。'
    // 拦截 /stream/pet/chat：记录 body，返回 SSE 动作回复；历史 mock 模拟落库
    const base = routeFetchFrom([
      {
        match: '/stream/pet/history',
        data: () => ({
          data: {
            items: chatCalled
              ? [{ id: 1, role: 'user', content: PROMPT }, { id: 2, role: 'assistant', content: '喵呜～谢谢蛋糕！' }]
              : [],
          },
        }),
      },
      { match: '/stream/pet/config', data: { data: {} } },
      { match: '/stream/pet/memory', data: { data: { items: [] } } },
      {
        match: '/stream/pet/actions',
        data: {
          data: {
            categories: [{ id: 'feed', label: '喂食', icon: 'utensils' }],
            actions: [{ id: 'cake', label: '送蛋糕', category: 'feed', icon: 'cake', prompt: PROMPT }],
          },
        },
      },
    ])
    globalThis.fetch = vi.fn(async (url, init) => {
      const u = String(url)
      if (u.includes('/stream/pet/state')) stateCalls++
      if (u.includes('/stream/pet/chat')) {
        chatCalled = true
        chatBody = JSON.parse(init.body)
        return sseMockResponse(['data: {"token":"喵呜～谢谢蛋糕！"}\n\n'])
      }
      return base(url, init)
    })
    const w = mountPage()
    await tick()
    // 动作已加载；状态栏已移除 → 页面不再请求/展示宠物状态
    expect(w.vm.petCats.length).toBe(1)
    expect(w.vm.petActs.length).toBe(1)
    expect(stateCalls).toBe(0)
    expect(w.find('.pet-statebar').exists()).toBe(false)
    // 分类点开 → 动作按钮出现
    w.vm.activeCat = 'feed'
    await w.vm.$nextTick()
    const actBtn = w.find('.pet-interact-act')
    expect(actBtn.exists()).toBe(true)
    expect(actBtn.text()).toContain('送蛋糕')
    // 点击送礼物 → 发送 action_id（非 text）
    await actBtn.trigger('click')
    await tick()
    expect(chatBody).toEqual({ action_id: 'cake' })
    // 重拉历史后：用户侧 prompt 匹配动作 → 渲染图标气泡；助手回复入流
    const msgs = w.vm.messages
    expect(msgs.some((m) => m.role === 'user' && m.actionId === 'cake' && !m.content)).toBe(true)
    expect(msgs[msgs.length - 1].content).toBe('喵呜～谢谢蛋糕！')
    expect(stateCalls).toBe(0) // 互动后也不再刷新已删除的状态栏
  })

  it('语音播放：done 帧带 audio_url → 自动播放；无 URL 不播放；停止按钮终止', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    // mock 全局 Audio：记录 src、play/pause 真实调用（不是只断言"不抛异常"）
    const played = []
    const instances = []
    class FakeAudio {
      constructor(src) { this.src = src; played.push(src); this.paused = true; this.pauses = 0; instances.push(this) }
      async play() { this.paused = false }
      pause() { this.paused = true; this.pauses++ }
    }
    globalThis.Audio = FakeAudio
    // done 帧带 audio_url → 播放
    sseMock([
      'data: {"token":"你好呀"}\n\n',
      'event: done\r\ndata: {"audio_url":"/audio/tts_1.mp3"}\r\n\r\n',
    ])
    w.vm.input = '在吗'
    await w.vm.send()
    await tick()
    expect(played.some((s) => s.includes('/audio/tts_1.mp3'))).toBe(true)
    expect(w.vm.messages[w.vm.messages.length - 1].content).toBe('你好呀')
    // 停止按钮 → 当前音频必须被真实 pause（不能只断言"没报错"）
    sseMock(['data: {"token":"hi"}\n\n', 'event: done\r\ndata: {"audio_url":"/audio/tts_2.mp3"}\r\n\r\n'])
    w.vm.input = 'hi'
    await w.vm.send()
    await tick()
    const latest = instances[instances.length - 1]
    expect(latest.src).toContain('/audio/tts_2.mp3')
    expect(latest.paused).toBe(false)
    w.vm.stopSend()
    expect(latest.paused).toBe(true)
    expect(latest.pauses).toBe(1)
  })

  it('语音播放：done 帧无 audio_url（开关关闭）→ 不创建 Audio', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    let audioCreated = 0
    class FakeAudio2 {
      constructor() { audioCreated++ }
      async play() {}
      pause() {}
    }
    globalThis.Audio = FakeAudio2
    sseMock(['data: {"token":"喵"}\n\n', 'event: done\r\ndata: {"state":{}}\r\n\r\n'])
    w.vm.input = '在吗'
    await w.vm.send()
    await tick()
    expect(audioCreated).toBe(0)
    expect(w.vm.messages[w.vm.messages.length - 1].content).toBe('喵')
  })

  it('设置 Tab 桌宠开关：关闭调用 updateConfig(false)，再开启拉起进程', async () => {
    const { endpoints } = await import('@/api.js')
    const updSpy = vi.spyOn(endpoints, 'updateConfig').mockResolvedValue({ success: true })
    const startSpy = vi.spyOn(endpoints, 'petWindowStart').mockResolvedValue({ success: true, data: { ok: true, pid: 1234 } })
    const stopSpy = vi.spyOn(endpoints, 'petWindowStop').mockResolvedValue({ success: true, data: { ok: true } })
    const statusSpy = vi.spyOn(endpoints, 'petWindowStatus').mockResolvedValue({ success: true, data: { running: true, pid: 1234 } })
    try {
      routeFetch(BASE_ROUTES())
      const w = mountPage()
      await tick()
      await w.vm.$nextTick()
      w.vm.tab = 'settings'
      await w.vm.$nextTick()
      let toggle = w.find('[data-test="pet-master-toggle"]')
      expect(w.vm.masterEnabled).toBe(true)
      expect(toggle.element.checked).toBe(true)
      // 关闭：先停窗口进程，再关引擎开关
      await toggle.setValue(false)
      await tick()
      expect(stopSpy).toHaveBeenCalled()
      expect(updSpy).toHaveBeenCalledWith('DESKTOP_PET_ENABLED', false)
      expect(w.vm.masterEnabled).toBe(false)
      await w.vm.$nextTick()
      toggle = w.find('[data-test="pet-master-toggle"]')
      expect(toggle.element.checked).toBe(false)
      // 开启：拉起进程 + 开引擎
      await toggle.setValue(true)
      await tick()
      expect(updSpy).toHaveBeenCalledWith('DESKTOP_PET_ENABLED', true)
      expect(startSpy).toHaveBeenCalled()
      expect(w.vm.masterEnabled).toBe(true)
      expect(statusSpy).toHaveBeenCalled()
    } finally {
      updSpy.mockRestore()
      startSpy.mockRestore()
      stopSpy.mockRestore()
      statusSpy.mockRestore()
    }
  })
})

// 供拦截 chat 的测试复用的路由分发器（data 支持函数形式，参数 (url, init)）
function routeFetchFrom(routes) {
  return async (url, init) => {
    const u = String(url)
    for (const r of routes) {
      if (u.includes(r.match)) {
        const data = typeof r.data === 'function' ? await r.data(u, init) : r.data
        return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) }
      }
    }
    return { ok: true, status: 200, json: async () => ({}), text: async () => '' }
  }
}
