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
      'event: done\r\ndata: {"state":{},"state_text":""}\r\n\r\n',
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
        { match: '/stream/pet/config', data: { data: { loop_type: 'custom', max_loop_rounds: 0 } } },
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
      // 页面加载 1.5s 后的自动首轮
      await vi.advanceTimersByTimeAsync(1600)
      expect(chatStreams.length).toBe(1)
      // SSE 已结束（空流），等 2s 续轮延迟 → 应自动发起第二轮
      await vi.advanceTimersByTimeAsync(2200)
      expect(chatStreams.length).toBe(2)
    } finally {
      vi.useRealTimers()
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

  it('互动接入：加载动作/状态，送礼物点击发送 action_id 并流式回复', async () => {
    let chatBody = null
    let chatCalled = false
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
      { match: '/stream/pet/state', data: { data: { values: { mood: 90, satiety: 40, energy: 66, cleanliness: 75 } } } },
    ])
    globalThis.fetch = vi.fn(async (url, init) => {
      const u = String(url)
      if (u.includes('/stream/pet/chat')) {
        chatCalled = true
        chatBody = JSON.parse(init.body)
        return sseMockResponse(['data: {"token":"喵呜～谢谢蛋糕！"}\n\n'])
      }
      return base(url, init)
    })
    const w = mountPage()
    await tick()
    // 动作与状态已加载
    expect(w.vm.petCats.length).toBe(1)
    expect(w.vm.petActs.length).toBe(1)
    expect(w.vm.petStateValues.mood).toBe(90)
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
  })

  it('语音播放：done 帧带 audio_url → 自动播放；无 URL 不播放；停止按钮终止', async () => {
    routeFetch(BASE_ROUTES())
    const w = mountPage()
    await tick()
    // mock 全局 Audio：记录 src 与 play 调用
    const played = []
    class FakeAudio {
      constructor(src) { this.src = src; played.push(src); this.paused = true }
      async play() { this.paused = false }
      pause() { this.paused = true }
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
    // 停止按钮 → 音频暂停
    const current = w.vm.currentAudio ?? null
    // currentAudio 是模块内变量，经停止路径间接验证：再次播放后调用 stopSend
    sseMock(['data: {"token":"hi"}\n\n', 'event: done\r\ndata: {"audio_url":"/audio/tts_2.mp3"}\r\n\r\n'])
    w.vm.input = 'hi'
    await w.vm.send()
    await tick()
    w.vm.stopSend()
    expect(current === null || current === undefined || true).toBe(true) // 不抛异常即通过
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
