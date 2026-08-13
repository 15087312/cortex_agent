import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import Settings from './Settings.vue'
import { routeFetch } from '@/test/helpers.js'
import { dialogState, resolveDialog } from '@/composables/useDialog.js'
import { setApiKey } from '@/api.js'

const libs = [{ name: 'lib_a', size: 1024 }, { name: 'lib_b', size: 2048 }]

// 记录所有写请求，便于断言
const writes = []

function mockApi(cfg = {}) {
  const configData = cfg.config || {
    USER_NAME: '测试用户',
    CORTEX_MODE: 'agent',
    EXECUTION_MODE: 'edit',
    PROACTIVE_OUTREACH_ENABLED: false,
    DESKTOP_PET_ENABLED: true,
    LARGE_MODEL_API_URL: 'https://x/v1',
  }
  let currentLib = 'lib_a'  // 模拟后端当前记忆库状态
  routeFetch([
    // ── 记忆库 ──
    { match: '/api/config/memory-libs/current', data: (u, init) => { writes.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); currentLib = JSON.parse(init.body).name; return { success: true } } },
    { match: '/api/config/memory-libs/rename', data: (u, init) => { writes.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/config/memory-libs/', data: (u, init) => { writes.push({ url: String(u), method: init?.method }); return { success: true } } },
    { match: '/api/config/memory-libs', data: (u, init) => {
      if (init?.method === 'POST') { writes.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } }
      return { success: true, data: { libs, current: currentLib } }
    } },
    // ── 配置读写 ──
    { match: '/api/config/PROACTIVE_OUTREACH_DEFAULT', data: (u, init) => { writes.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/config/', data: (u, init) => {
      writes.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) })
      // 模拟后端持久化：PUT 后 GET 返回新值
      const key = decodeURIComponent(String(u).split('/api/config/')[1])
      configData[key] = JSON.parse(init.body).value
      return { success: true }
    } },
    { match: '/api/config', data: { success: true, data: configData } },
    // ── 系统信息 / 健康 / 模型状态 ──
    { match: '/api/management/thinking', data: { success: true, data: { models: { LARGE: { status: 'ready' } } } } },
    { match: '/api/management/vision-models', data: { success: true, data: { models: ['mlx'], dir: '/tmp/models' } } },
    { match: '/api/management/install-voice-deps', data: { success: true, data: { message: '已安装' } } },
    { match: '/api/management/open-folder', data: (u, init) => { writes.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/stream/pet/state/reset', data: (u, init) => { writes.push({ url: String(u), method: init?.method }); return { success: true, data: { values: { mood: 80 } } } } },
    { match: '/api/stream/pet/state', data: { success: true, data: { values: { mood: 50, satiety: 60 }, text: '心情不错' } } },
    { match: '/api/health', data: { success: true, data: { version: __APP_VERSION__ } } },
    { match: '/api/', data: { success: true, data: { version: '9.9.9', storage_path: '/tmp/cortex' } } },
  ])
  return configData
}

let wrapper = null
async function mountSettings(pinia) {
  wrapper = mount(Settings, { global: { plugins: [pinia || createPinia()] } })
  await new Promise((r) => setTimeout(r, 60))
  return wrapper
}

describe('Settings.vue', () => {
  beforeEach(() => {
    writes.length = 0
    setActivePinia(createPinia())
    dialogState().visible = false
  })

  afterEach(() => {
    // 真正卸载组件 → onUnmounted 清理 5s 桌宠轮询定时器（此前空壳注释，轮询实际残留）
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })

  it('渲染 tab 分组并切换', async () => {
    mockApi()
    const w = await mountSettings()
    expect(w.text()).toContain('用户')
    expect(w.text()).toContain('对话')
    expect(w.text()).toContain('主动搭话')
    expect(w.text()).toContain('高级')
    expect(w.text()).toContain('通用设置')
    await w.findAll('.settings-tab').find((t) => t.text() === '感知').trigger('click')
    expect(w.vm.activeTab).toBe('感知')
    await w.findAll('.settings-tab').find((t) => t.text() === '记忆库').trigger('click')
    expect(w.vm.activeTab).toBe('记忆库')
  })

  it('加载并渲染记忆库列表', async () => {
    mockApi()
    const w = await mountSettings()
    await w.findAll('.settings-tab').find((t) => t.text() === '记忆库').trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    expect(w.text()).toContain('lib_a')
    expect(w.text()).toContain('lib_b')
  })

  it('switchLib 切换记忆库', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.switchLib('lib_b')
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/current'))
    expect(put).toBeTruthy()
    expect(put.method).toBe('PUT')
    expect(put.body).toEqual({ name: 'lib_b' })
    expect(w.vm.memoryCurrent).toBe('lib_b')
  })

  it('createLib 创建记忆库', async () => {
    mockApi()
    const w = await mountSettings()
    w.vm.newLibName = 'lib_c'
    await w.vm.createLib()
    await new Promise((r) => setTimeout(r, 30))
    const post = writes.find((x) => x.url.includes('/config/memory-libs') && x.method === 'POST')
    expect(post).toBeTruthy()
    expect(post.body).toEqual({ name: 'lib_c' })
  })

  it('deleteLib 删除记忆库（confirm 通过）', async () => {
    mockApi()
    const w = await mountSettings()
    w.vm.deleteLib({ name: 'lib_b' })
    await new Promise((r) => setTimeout(r, 10))
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await new Promise((r) => setTimeout(r, 30))
    const del = writes.find((x) => x.url.includes('/memory-libs/lib_b') && x.method === 'DELETE')
    expect(del).toBeTruthy()
  })

  it('saveCfg 乐观更新 + 后端持久化', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.saveCfg('DEBUG', true)
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/api/config/DEBUG'))
    expect(put).toBeTruthy()
    expect(put.method).toBe('PUT')
    expect(put.body).toEqual({ value: true })
  })

  it('保存全局默认主动搭话规则（序列化规则结构）', async () => {
    mockApi()
    const w = await mountSettings()
    w.vm.globalDefault.enabled = true
    w.vm.globalDefault.scheduleOn = true
    w.vm.globalDefault.scheduleTime = '14:00'
    w.vm.globalDefault.screenOn = true
    w.vm.globalDefault.screenRatio = 0.7
    await w.vm.saveGlobalDefault()
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/PROACTIVE_OUTREACH_DEFAULT'))
    expect(put).toBeTruthy()
    const cfg = JSON.parse(put.body.value)
    expect(cfg.enabled).toBe(true)
    expect(cfg.schedule).toEqual({ enabled: true, time: '14:00', jitter_minutes: 10 })
    expect(cfg.screen.enabled).toBe(true)
    expect(cfg.screen.change_ratio).toBe(0.7)
  })

  it('saveModelForm 串行保存主模型配置（只保存变更字段）', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 20))
    // 修改大模型 URL
    w.vm.modelForm.LARGE.API_URL = 'https://new-url/v1'
    await w.vm.saveModelForm()
    await new Promise((r) => setTimeout(r, 30))
    const puts = writes.filter((x) => x.url.includes('/api/config/') && x.method === 'PUT' && !x.url.includes('PROACTIVE') && !x.url.includes('memory-libs'))
    expect(puts.some((x) => x.url.includes('LARGE_MODEL_API_URL'))).toBe(true)
  })

  it('授权：saveKey 保存 API Key（内存）', async () => {
    mockApi()
    const w = await mountSettings()
    w.vm.keyInput = 'sk-test-123'
    w.vm.saveKey()
    const { getApiKey } = await import('@/api.js')
    expect(getApiKey()).toBe('sk-test-123')
    setApiKey('')
  })

  it('openFolder 打开目录', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.openFolder('storage')
    await new Promise((r) => setTimeout(r, 20))
    const post = writes.find((x) => x.url.includes('/open-folder'))
    expect(post).toBeTruthy()
    expect(post.body).toEqual({ folder: 'storage' })
  })

  it('openDiagnostics 聚合系统/健康/模型诊断数据', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.openDiagnostics()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.showDiag).toBe(true)
    expect(w.vm.diagLoading).toBe(false)
    expect(w.vm.diagData.system).toBeTruthy()
    expect(w.vm.diagData.health).toBeTruthy()
    expect(w.vm.diagData.models.LARGE).toBeTruthy()
    expect(w.vm.diagData.configKeys).toContain('USER_NAME')
  })

  it('checkUpdates 比较版本（同版本提示最新）', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.checkUpdates()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.checkingUpdate).toBe(false)
  })

  it('加载桌宠状态并重置', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.petState.mood).toBe(50)
    await w.vm.resetPetState()
    await new Promise((r) => setTimeout(r, 20))
    expect(w.vm.petState.mood).toBe(80)
  })

  it('卸载时清理桌宠轮询定时器（防泄漏）', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.petPollTimer).not.toBeNull()
    w.unmount()
    expect(w.vm.petPollTimer).toBeNull()
  })

  it('computed 配置包装：boolCfg 读写并持久化', async () => {
    mockApi({ config: { DEBUG: false } })
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    // 读取：未配置时用 fallback
    expect(w.vm.petEnabled).toBe(true)
    // 写入：setter → saveCfg → configStore.$patch + PUT
    w.vm.petEnabled = false
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/api/config/DESKTOP_PET_ENABLED'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ value: false })
    // 乐观更新立即生效
    expect(w.vm.petEnabled).toBe(false)
  })

  it('computed 配置包装：txtCfg/numCfg/segCfg 读写', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    // txtCfg 字符串
    expect(w.vm.userName).toBe('测试用户')
    // numCfg 数字 fallback
    expect(w.vm.maxWorkers).toBe(4)
    w.vm.maxWorkers = 8
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/api/config/MAX_WORKERS'))
    expect(put.body).toEqual({ value: 8 })
    // segCfg 段值
    expect(w.vm.cortexMode).toBe('agent')
    w.vm.cortexMode = 'chatonly'
    await new Promise((r) => setTimeout(r, 30))
    const put2 = writes.find((x) => x.url.includes('/api/config/CORTEX_MODE'))
    expect(put2.body).toEqual({ value: 'chatonly' })
  })

  it('saveKey 清除 API Key', async () => {
    mockApi()
    const w = await mountSettings()
    const { getApiKey } = await import('@/api.js')
    setApiKey('temp-key')
    w.vm.keyInput = ''
    w.vm.saveKey()
    expect(getApiKey()).toBe('')
    setApiKey('')
  })

  it('copyPath / copyDiag 复制到剪贴板', async () => {
    mockApi()
    const w = await mountSettings()
    const writeSpy = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    w.vm.storagePath = '/tmp/cortex'
    await w.vm.copyPath()
    expect(writeSpy).toHaveBeenCalledWith('/tmp/cortex')
    w.vm.diagData = { a: 1 }
    await w.vm.copyDiag()
    expect(writeSpy).toHaveBeenCalledWith(JSON.stringify({ a: 1 }, null, 2))
    vi.restoreAllMocks()
  })

  it('saveModelForm 无变更时提示', async () => {
    mockApi({ config: {
      LARGE_MODEL_API_URL: 'https://x/v1', LARGE_MODEL_API_KEY: 'k', LARGE_MODEL_NAME: 'm',
    } })
    const pinia = createPinia()
    setActivePinia(pinia)
    const w = await mountSettings(pinia)
    const { useToastStore } = await import('@/stores/toast.js')
    const toast = useToastStore()
    await new Promise((r) => setTimeout(r, 40))
    await w.vm.saveModelForm()
    await new Promise((r) => setTimeout(r, 20))
    expect(toast.toasts.some((t) => t.msg.includes('无变化'))).toBe(true)
  })

  it('renameLib 重命名记忆库（prompt 通过）', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.loadMemoryLibs()
    const p = w.vm.renameLib({ name: 'lib_a' })
    await new Promise((r) => setTimeout(r, 10))
    const { dialogState, resolveDialog } = await import('@/composables/useDialog.js')
    expect(dialogState().type).toBe('prompt')
    resolveDialog('lib_new')
    await p
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/rename'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ old_name: 'lib_a', new_name: 'lib_new' })
  })

  it('renameLib 空名/未变/取消时不提交', async () => {
    mockApi()
    const w = await mountSettings()
    await w.vm.loadMemoryLibs()
    // 取消（null）
    let p = w.vm.renameLib({ name: 'lib_a' })
    await new Promise((r) => setTimeout(r, 10))
    ;(await import('@/composables/useDialog.js')).resolveDialog(null)
    await p
    // 同名
    p = w.vm.renameLib({ name: 'lib_a' })
    await new Promise((r) => setTimeout(r, 10))
    ;(await import('@/composables/useDialog.js')).resolveDialog('lib_a')
    await p
    expect(writes.some((x) => x.url.includes('/rename'))).toBe(false)
  })

  it('installVoiceDeps 安装依赖成功/失败', async () => {
    const reqs = []
    routeFetch([
      { match: '/api/management/install-voice-deps', data: (u, init) => {
        reqs.push(init?.method || 'GET')
        return { success: true, data: { message: '依赖安装完成' } }
      } },
      { match: '/api/management/orchestration', data: { success: true, data: { agents: [] } } },
      { match: '/api/stream/session/s1/outreach-config', data: { success: true, data: {} } },
      { match: '/api/stream/session/s1/tasks', data: { success: true, data: {} } },
    ])
    const w = await mountSettings()
    await w.vm.installVoiceDeps()
    await new Promise((r) => setTimeout(r, 20))
    expect(w.vm.installResult.message).toBe('依赖安装完成')
    expect(w.vm.installingDeps).toBe(false)
    expect(reqs).toContain('POST')
  })

  it('onVisionBackendChange 本地模式时加载视觉模型列表（副作用验证）', async () => {
    mockApi()
    const w = await mountSettings()
    // 断言真实副作用：loadVisionModels 填充 visionModelList（来自 /api/management/vision-models mock）
    w.vm.visionModelList = []
    w.vm.visionBackend = 'local'
    w.vm.onVisionBackendChange()
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.visionModelList).toEqual(['mlx'])
  })

  it('loadMemoryLibs 全部失败后重试 3 次并 toast', async () => {
    const attempts = []
    routeFetch([
      { match: '/api/config/memory-libs', data: () => { attempts.push(1); throw new Error('down') } },
    ])
    const w = await mountSettings()
    // 重试间隔 400ms + 800ms，等待 3 次尝试完成
    await new Promise((r) => setTimeout(r, 1400))
    expect(attempts.length).toBe(3)
    expect(w.vm.memoryLibs).toHaveLength(0)
  })

  it('switchLib 失败显示错误 toast', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    routeFetch([
      { match: '/api/config/memory-libs/current', data: () => { throw new Error('x') } },
    ])
    const w = await mountSettings(pinia)
    const { useToastStore } = await import('@/stores/toast.js')
    const toast = useToastStore()
    await w.vm.switchLib('lib_b')
    await new Promise((r) => setTimeout(r, 20))
    expect(toast.toasts.some((t) => t.msg.includes('切换失败'))).toBe(true)
  })

  it('createLib 空名不提交', async () => {
    mockApi()
    const w = await mountSettings()
    w.vm.newLibName = '   '
    await w.vm.createLib()
    expect(writes.some((x) => x.url.includes('memory-libs') && x.method === 'POST')).toBe(false)
  })

  it('loadGlobalDefault 解析嵌套规则结构', async () => {
    mockApi({ config: {
      PROACTIVE_OUTREACH_DEFAULT: JSON.stringify({
        enabled: true,
        schedule: { enabled: true, time: '12:00', jitter_minutes: 15 },
        screen: { enabled: true, change_ratio: 0.8, probability: 0.9, check_interval_seconds: 45, cooldown_minutes: 20 },
        idle: { enabled: false, idle_minutes: 10, probability: 0.1, check_interval_seconds: 30 },
        time_windows_enabled: true,
        time_windows: [{ start: '10:00', end: '11:00', probability: 0.7 }],
      }),
    } })
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 40))
    await w.vm.loadGlobalDefault()
    const g = w.vm.globalDefault
    expect(g.enabled).toBe(true)
    expect(g.scheduleTime).toBe('12:00')
    expect(g.scheduleJitter).toBe(15)
    expect(g.screenRatio).toBe(0.8)
    expect(g.screenInterval).toBe(45)
    expect(g.idleOn).toBe(false)
    expect(g.windowsOn).toBe(true)
    expect(g.timeWindowsText).toBe('10:00-11:00@0.7')
  })

  it('saveGlobalDefault 校验钳制边界值', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    const g = w.vm.globalDefault
    g.enabled = true
    g.screenOn = true
    g.screenRatio = 5  // >1 → 钳到 1
    g.screenProb = -3  // <0 → 钳到 0
    g.screenInterval = -5 // <1 且 truthy → 钳到 1（注意 0 是 falsy 会回退默认 30）
    g.idleOn = true
    g.idleProb = 2 // >1 → 钳到 1
    await w.vm.saveGlobalDefault()
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/PROACTIVE_OUTREACH_DEFAULT'))
    const cfg = JSON.parse(put.body.value)
    expect(cfg.screen.change_ratio).toBe(1)
    expect(cfg.screen.probability).toBe(0)
    expect(cfg.screen.check_interval_seconds).toBe(1)
    expect(cfg.idle.probability).toBe(1)
  })

  it('startEditShortcut/saveShortcut 编辑快捷键状态', async () => {
    mockApi()
    const w = await mountSettings()
    w.vm.startEditShortcut()
    expect(w.vm.editingShortcut).toBe(true)
    w.vm.saveShortcut()
    expect(w.vm.editingShortcut).toBe(false)
  })

  it('openLink 打开外链', async () => {
    mockApi()
    const w = await mountSettings()
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => {})
    w.vm.openLink('https://example.com')
    expect(openSpy).toHaveBeenCalledWith('https://example.com', '_blank')
    vi.restoreAllMocks()
  })

  it('clearKey 清除 API Key', async () => {
    mockApi()
    const w = await mountSettings()
    const { getApiKey } = await import('@/api.js')
    setApiKey('temp')
    w.vm.keyInput = 'temp'
    w.vm.clearKey()
    expect(w.vm.keyInput).toBe('')
    expect(getApiKey()).toBe('')
    setApiKey('')
  })

  it('advancedKeys 过滤高级配置键', async () => {
    mockApi({ config: { ATTENTION_FOO: 1, CAUSAL_BAR: 2, INTERRUPT_BAZ: 3, USER_NAME: 'x' } })
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    const keys = w.vm.advancedKeys
    expect(keys).toContain('ATTENTION_FOO')
    expect(keys).toContain('CAUSAL_BAR')
    expect(keys).toContain('INTERRUPT_BAZ')
    expect(keys).not.toContain('USER_NAME')
  })

  it('editConfig 经 prompt 编辑并保存（含类型转换）', async () => {
    mockApi()
    const w = await mountSettings()
    const { useConfigStore } = await import('@/stores/config.js')
    const cfg = useConfigStore()
    // 数字转换
    const p1 = w.vm.editConfig('MAX_WORKERS', '4')
    await new Promise((r) => setTimeout(r, 10))
    ;(await import('@/composables/useDialog.js')).resolveDialog('8')
    await p1
    await new Promise((r) => setTimeout(r, 30))
    expect(cfg.config.MAX_WORKERS).toBe(8)
    // 布尔转换
    const p2 = w.vm.editConfig('DEBUG', 'false')
    await new Promise((r) => setTimeout(r, 10))
    ;(await import('@/composables/useDialog.js')).resolveDialog('true')
    await p2
    await new Promise((r) => setTimeout(r, 30))
    expect(cfg.config.DEBUG).toBe(true)
    // 取消不保存
    const p3 = w.vm.editConfig('CAUSAL_X', '1')
    await new Promise((r) => setTimeout(r, 10))
    ;(await import('@/composables/useDialog.js')).resolveDialog(null)
    await p3
  })

  it('openDiagnostics 后端全失败时返回 error 字段', async () => {
    routeFetch([])
    const w = await mountSettings()
    await w.vm.openDiagnostics()
    await new Promise((r) => setTimeout(r, 20))
    expect(w.vm.diagLoading).toBe(false)
  })

  it('checkUpdates 后端无版本时回退 systemInfo', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    routeFetch([
      { match: '/api/health', data: { success: true, data: {} } },
      { match: '/api/', data: { success: true, data: { version: '7.7.7' } } },
    ])
    // 模拟 GitHub 不可达（只拦 api.github.com，其余透传给 routeFetch）
    const origFetch = global.fetch
    global.fetch = (url, ...args) => String(url).includes('api.github.com')
      ? Promise.reject(new Error('offline'))
      : origFetch(url, ...args)
    try {
      const w = await mountSettings(pinia)
      const { useToastStore } = await import('@/stores/toast.js')
      const toast = useToastStore()
      await w.vm.checkUpdates()
      await new Promise((r) => setTimeout(r, 30))
      expect(toast.toasts.some((t) => t.msg.includes('7.7.7'))).toBe(true)
    } finally {
      global.fetch = origFetch
    }
  })

  it('checkUpdates 无法连接后端', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    routeFetch([
      { match: '/api/health', data: () => { throw new Error('down') } },
    ])
    // 模拟 GitHub 不可达（只拦 api.github.com，其余透传给 routeFetch）
    const origFetch = global.fetch
    global.fetch = (url, ...args) => String(url).includes('api.github.com')
      ? Promise.reject(new Error('offline'))
      : origFetch(url, ...args)
    try {
      const w = await mountSettings(pinia)
      const { useToastStore } = await import('@/stores/toast.js')
      const toast = useToastStore()
      await w.vm.checkUpdates()
      await new Promise((r) => setTimeout(r, 30))
      expect(toast.toasts.some((t) => t.msg.includes('无法连接后端'))).toBe(true)
    } finally {
      global.fetch = origFetch
    }
  })

  it('通用设置 tab：开机启动/防休眠/定位开关触发 saveCfg', async () => {
    mockApi()
    const w = await mountSettings()
    await w.findAll('.settings-tab').find((t) => t.text() === '通用设置').trigger('click')
    // 开机启动（默认 true → 点击变 false）
    await w.findAll('.settings-row').find((r) => r.text().includes('开机启动')).trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/api/config/launch_at_startup'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ value: false })
    // 防休眠（默认 false → true）
    await w.findAll('.settings-row').find((r) => r.text().includes('防休眠')).trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    const put2 = writes.find((x) => x.url.includes('/api/config/prevent_sleep'))
    expect(put2).toBeTruthy()
    expect(put2.body).toEqual({ value: true })
  })

  it('通用设置 tab：快捷键编辑 startEditShortcut/saveShortcut', async () => {
    mockApi()
    const w = await mountSettings()
    await w.findAll('.settings-tab').find((t) => t.text() === '通用设置').trigger('click')
    expect(w.vm.editingShortcut).toBe(false)
    await w.find('.settings-shortcut').trigger('click')
    expect(w.vm.editingShortcut).toBe(true)
    await w.find('input.shortcut-input').trigger('blur')
    expect(w.vm.editingShortcut).toBe(false)
  })

  it('txtCfg setter 持久化用户名（trim 空串回退）', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    // setter → 写后端
    w.vm.userName = '新名字'
    await new Promise((r) => setTimeout(r, 30))
    const puts = writes.filter((x) => x.url.includes('/api/config/USER_NAME'))
    expect(puts.length).toBeGreaterThanOrEqual(1)
    expect(puts[0].body).toEqual({ value: '新名字' })
    // 空白串 → trim 后写空串（第二次写）
    w.vm.userName = '   '
    await new Promise((r) => setTimeout(r, 30))
    const puts2 = writes.filter((x) => x.url.includes('/api/config/USER_NAME'))
    expect(puts2.length).toBe(puts.length + 1)
    expect(puts2[puts2.length - 1].body).toEqual({ value: '' })
  })

  it('saveGlobalDefault 无 @ 概率时段解析（默认省略）', async () => {
    mockApi()
    const w = await mountSettings()
    await new Promise((r) => setTimeout(r, 30))
    const g = w.vm.globalDefault
    g.enabled = true
    g.windowsOn = true
    g.timeWindowsText = '09:00-12:00,14:00-18:00@0.5,坏数据'
    await w.vm.saveGlobalDefault()
    await new Promise((r) => setTimeout(r, 30))
    const put = writes.find((x) => x.url.includes('/PROACTIVE_OUTREACH_DEFAULT'))
    const cfg = JSON.parse(put.body.value)
    expect(cfg.time_windows).toEqual([
      { start: '09:00', end: '12:00' },
      { start: '14:00', end: '18:00', probability: 0.5 },
    ])
    expect(cfg.enabled).toBe(true)
  })
})
