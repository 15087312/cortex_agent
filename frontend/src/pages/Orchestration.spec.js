import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Orchestration from './Orchestration.vue'
import { routeFetch } from '@/test/helpers.js'
import { dialogState, resolveDialog } from '@/composables/useDialog.js'

const agents = [
  { role: 'chief', name: '总指挥一号', tier: 'large', active: true, model_id: 'm1', custom_persona: '我是总指挥' },
  { role: 'supervisor_a', name: '主管甲', tier: 'supervisor', active: true },
  { role: 'expert_writer', name: '写作专家', tier: 'expert', active: false, role_tools: { whitelist: ['calculator'], blacklist: [] } },
]

function mockApi() {
  const reqs = []
  routeFetch([
    { match: '/api/management/orchestration/agents/', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: init?.body ? JSON.parse(init.body) : null }); return { success: true } } },
    { match: '/api/management/orchestration/agents', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: init?.body ? JSON.parse(init.body) : null }); return { success: true } } },
    { match: '/api/management/orchestration/preview', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true, data: { prompt: '预览提示词内容' } } } },
    { match: '/api/management/orchestration', data: { success: true, data: { agents } } },
    { match: '/api/management/persona-presets/', data: (u, init) => { reqs.push({ url: String(u), method: init?.method }); return { success: true } } },
    { match: '/api/management/persona-presets', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: init?.body ? JSON.parse(init.body) : null }); return { success: true, data: { presets: [{ id: 'p1', name: '预设一' }] } } } },
    { match: '/api/config/persona/', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/config/tools/', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/config/model-params/', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/tools/enabled/', data: (u, init) => { reqs.push({ url: String(u), method: init?.method, body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/tools/source/', data: { success: true, data: { source: 'def tool(): pass', editable: true } } },
    { match: '/api/tools/ai', data: (u, init) => {
      if (init?.method && init.method !== 'GET') { reqs.push({ url: String(u), method: init.method, body: init.body ? JSON.parse(init.body) : null }); return { success: true } }
      return { success: true, data: { tools: { custom1: { description: '自定义工具' } } } }
    } },
    { match: '/api/tools/', data: { success: true, data: { tools: { calculator: { enabled: true, description: '计算器' }, web: { enabled: false, description: '搜索' } } } } },
  ])
  return reqs
}

async function mountOrch() {
  const w = mount(Orchestration, { global: { plugins: [createPinia()] } })
  await new Promise((r) => setTimeout(r, 50))
  return w
}

describe('Orchestration.vue', () => {
  beforeEach(() => {
    dialogState().visible = false
  })

  it('加载并分组渲染 agents（large/supervisor/expert 标题 + 名称 + 徽章）', async () => {
    mockApi()
    const w = await mountOrch()
    const txt = w.text()
    expect(txt).toContain('总指挥（1）')
    expect(txt).toContain('主管（1）')
    expect(txt).toContain('专家（1）')
    expect(txt).toContain('总指挥一号')
    expect(txt).toContain('主管甲')
    expect(txt).toContain('写作专家')
    expect(txt).toContain('自定义人设')
  })

  it('切换 tab：技能/编排图/权限/工具', async () => {
    mockApi()
    const w = await mountOrch()
    const tabs = w.findAll('.seg button')
    await tabs.find((b) => b.text().includes('技能')).trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    expect(w.vm.activeTab).toBe('skills')
    await tabs.find((b) => b.text().includes('编排图')).trigger('click')
    expect(w.vm.activeTab).toBe('graph')
    await tabs.find((b) => b.text().includes('权限管理')).trigger('click')
    expect(w.vm.activeTab).toBe('permission')
    await tabs.find((b) => b.text().includes('工具管理')).trigger('click')
    expect(w.vm.activeTab).toBe('tools')
  })

  it('toggleAgentActive 发送 active PUT 并刷新', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    const expert = w.vm.agents.find((a) => a.role === 'expert_writer')
    await w.vm.toggleAgentActive(expert)
    await new Promise((r) => setTimeout(r, 30))
    const put = reqs.find((r) => r.url.includes('/active'))
    expect(put).toBeTruthy()
    expect(put.method).toBe('PUT')
    expect(put.body).toEqual({ active: true })
  })

  it('savePersona 保存人设', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.drafts['chief'] = '新的人设'
    await w.vm.savePersona(w.vm.agents[0])
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/api/config/persona/'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ value: '新的人设' })
  })

  it('saveOverride 保存完整提示词覆盖', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.overrides['chief'] = '完整覆盖文本'
    await w.vm.saveOverride(w.vm.agents[0])
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/api/config/persona/'))
    // value 是 loadData 加载的 custom_persona，system_override 是测试设置的覆盖文本
    expect(put.body).toEqual({ value: '我是总指挥', system_override: '完整覆盖文本' })
  })

  it('saveRoleTools 保存角色工具白名单', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    await w.vm.saveRoleTools(w.vm.agents[2])
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/api/config/tools/'))
    expect(put).toBeTruthy()
    expect(put.body.tools.whitelist).toEqual(['calculator'])
  })

  it('saveModelParams 保存模型参数（只含非空字段）', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.modelParams['chief'] = { temperature: '0.7', max_tokens: '' }
    await w.vm.saveModelParams(w.vm.agents[0])
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/api/config/model-params/'))
    expect(put).toBeTruthy()
    expect(put.body.params).toEqual({ temperature: 0.7 })
  })

  it('previewPrompt 预览提示词', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    await w.vm.previewPrompt(w.vm.agents[0])
    await new Promise((r) => setTimeout(r, 20))
    const post = reqs.find((r) => r.url.includes('/preview'))
    expect(post).toBeTruthy()
    expect(post.body).toEqual({ role: 'chief', tier: 'large' })
    expect(w.vm.promptPreview.text).toBe('预览提示词内容')
  })

  it('toggleTool 切换工具启用状态', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    const tool = { name: 'web', enabled: false }
    await w.vm.toggleTool(tool)
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/api/tools/enabled/'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ enabled: true })
    expect(tool.enabled).toBe(true)
  })

  it('新建 Agent：表单提交 POST /agents', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.openAgentForm('expert')
    await w.vm.$nextTick()
    w.vm.agentForm.role = 'new_expert'
    w.vm.agentForm.name = '新专家'
    await w.vm.submitAgentForm()
    await new Promise((r) => setTimeout(r, 30))
    const post = reqs.find((r) => r.url.includes('/api/management/orchestration/agents') && r.method === 'POST')
    expect(post).toBeTruthy()
    expect(post.body.role).toBe('new_expert')
    expect(post.body.name).toBe('新专家')
    expect(w.vm.showAgentForm).toBe(false)
  })

  it('删除 Agent：confirm 通过后 DELETE', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.deleteAgent(w.vm.agents[1])
    await new Promise((r) => setTimeout(r, 10))
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await new Promise((r) => setTimeout(r, 40))
    const del = reqs.find((r) => r.url.includes('/api/management/orchestration/agents/') && r.method === 'DELETE')
    expect(del).toBeTruthy()
    expect(del.url).toContain('supervisor_a')
  })

  it('保存当前人设为预设', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.drafts['chief'] = '人设内容'
    w.vm.presetName = '我的预设'
    await w.vm.saveCurrentAsPreset()
    await new Promise((r) => setTimeout(r, 30))
    const post = reqs.find((r) => r.url.includes('/api/management/persona-presets') && r.method === 'POST')
    expect(post).toBeTruthy()
    expect(post.body.name).toBe('我的预设')
    expect(post.body.personas).toEqual({ chief: '人设内容' })
  })

  it('应用预设 PUT /apply', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    await w.vm.applyPreset('p1')
    await new Promise((r) => setTimeout(r, 30))
    expect(reqs.some((r) => r.url.includes('/persona-presets/p1/apply') && r.method === 'PUT')).toBe(true)
  })

  it('删除预设：confirm 通过后 DELETE', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.deletePreset('p1')
    await new Promise((r) => setTimeout(r, 10))
    resolveDialog(true)
    await new Promise((r) => setTimeout(r, 30))
    expect(reqs.some((r) => r.url.includes('/persona-presets/p1') && r.method === 'DELETE')).toBe(true)
  })

  it('viewSource 查看工具源码', async () => {
    mockApi()
    const w = await mountOrch()
    await w.vm.viewSource('calculator')
    await new Promise((r) => setTimeout(r, 20))
    expect(w.vm.srcModal.open).toBe(true)
    expect(w.vm.srcModal.source).toBe('def tool(): pass')
    expect(w.vm.srcModal.editable).toBe(true)
  })

  it('openAiForm 新建/编辑 AI 工具表单', async () => {
    mockApi()
    const w = await mountOrch()
    w.vm.openAiForm(null)
    expect(w.vm.showAiForm).toBe(true)
    expect(w.vm.editingAi).toBeNull()
    expect(w.vm.aiForm.tool_name).toBe('')
    w.vm.showAiForm = false
    w.vm.openAiForm('custom1')
    expect(w.vm.editingAi).toBe('custom1')
    expect(w.vm.aiForm.description).toBe('自定义工具')
  })

  it('submitAiForm 新建 AI 工具 POST', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.openAiForm(null)
    w.vm.aiForm.tool_name = 'my_tool'
    w.vm.aiForm.description = '我的工具'
    w.vm.aiForm.code = 'def run(): pass'
    await w.vm.submitAiForm()
    await new Promise((r) => setTimeout(r, 30))
    const post = reqs.find((r) => r.url.includes('/api/tools/ai') && r.method === 'POST')
    expect(post).toBeTruthy()
    expect(post.body.tool_name).toBe('my_tool')
    expect(w.vm.showAiForm).toBe(false)
  })

  it('submitAiForm 编辑 AI 工具 PUT', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.openAiForm('custom1')
    w.vm.aiForm.code = 'def run(): return 1'
    await w.vm.submitAiForm()
    await new Promise((r) => setTimeout(r, 30))
    const put = reqs.find((r) => r.url.includes('/api/tools/ai/') && r.method === 'PUT')
    expect(put).toBeTruthy()
    expect(put.url).toContain('custom1')
  })

  it('deleteAiTool 删除 AI 工具（confirm 通过）', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.deleteAiTool('custom1')
    await new Promise((r) => setTimeout(r, 10))
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await new Promise((r) => setTimeout(r, 30))
    const del = reqs.find((r) => r.url.includes('/api/tools/ai/') && r.method === 'DELETE')
    expect(del).toBeTruthy()
    expect(del.url).toContain('custom1')
  })

  it('toggleRoleTool / toggleRoleAll 角色工具权限列表操作', async () => {
    mockApi()
    const w = await mountOrch()
    w.vm.roleToolCfg = { whitelist: ['calc'], blacklist: [] }
    w.vm.toggleRoleTool('whitelist', 'web')
    expect(w.vm.roleToolCfg.whitelist).toEqual(['calc', 'web'])
    w.vm.toggleRoleTool('whitelist', 'calc')
    expect(w.vm.roleToolCfg.whitelist).toEqual(['web'])
    w.vm.toggleRoleAll('whitelist')
    expect(w.vm.roleToolCfg.whitelist).toContain('*')
    w.vm.toggleRoleAll('whitelist')
    expect(w.vm.roleToolCfg.whitelist).not.toContain('*')
  })

  it('toggleToolList / hasAll / toggleAll 单个 agent 工具列表', async () => {
    mockApi()
    const w = await mountOrch()
    const agent = w.vm.agents[2]
    w.vm.toggleToolList(agent, 'whitelist', 'web')
    expect(w.vm.toolsCfg[agent.role].whitelist).toEqual(['calculator', 'web'])
    w.vm.toggleAll(agent, 'blacklist')
    expect(w.vm.hasAll(agent, 'blacklist')).toBe(true)
    w.vm.toggleAll(agent, 'blacklist')
    expect(w.vm.hasAll(agent, 'blacklist')).toBe(false)
  })

  it('clearCustom / clearOverride 清空并保存', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.drafts['chief'] = '要清除的人设'
    w.vm.overrides['chief'] = '要清除的覆盖'
    await w.vm.clearCustom(w.vm.agents[0])
    await w.vm.clearOverride(w.vm.agents[0])
    await new Promise((r) => setTimeout(r, 20))
    expect(w.vm.drafts['chief']).toBe('')
    expect(w.vm.overrides['chief']).toBe('')
    const puts = reqs.filter((r) => r.url.includes('/api/config/persona/'))
    expect(puts.length).toBeGreaterThanOrEqual(2)
  })

  it('editAiFromSource 从源码打开编辑', async () => {
    mockApi()
    const w = await mountOrch()
    w.vm.srcModal = { open: true, name: 'custom1', source: 'x', editable: true }
    w.vm.editAiFromSource()
    expect(w.vm.showAiForm).toBe(true)
    expect(w.vm.editingAi).toBe('custom1')
  })

  it('submitAgentForm 缺 role 或 name 时校验失败不关闭', async () => {
    mockApi()
    const w = await mountOrch()
    w.vm.openAgentForm('expert')
    expect(w.vm.showAgentForm).toBe(true)
    w.vm.agentForm.role = ''
    w.vm.agentForm.name = ''
    await w.vm.submitAgentForm()
    // 校验失败 → 表单保持打开
    expect(w.vm.showAgentForm).toBe(true)
  })

  it('onRoleSel 加载选中角色的工具权限', async () => {
    mockApi()
    const w = await mountOrch()
    w.vm.roleToolSel = 'expert_writer'
    w.vm.onRoleSel()
    expect(w.vm.roleToolCfg.whitelist).toEqual(['calculator'])
    // 未知角色 → 空配置
    w.vm.roleToolSel = 'ghost'
    w.vm.onRoleSel()
    expect(w.vm.roleToolCfg.whitelist).toEqual([])
  })

  it('saveRoleToolsSel 保存选中角色权限', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.roleToolSel = 'expert_writer'
    w.vm.roleToolCfg = { whitelist: ['calculator'], blacklist: [] }
    await w.vm.saveRoleToolsSel()
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/api/config/tools/') && r.method === 'PUT')
    expect(put).toBeTruthy()
    expect(put.url).toContain('expert_writer')
    expect(put.body.tools.whitelist).toEqual(['calculator'])
  })

  it('DOM 交互：更多设置展开/收起面板', async () => {
    mockApi()
    const w = await mountOrch()
    expect(w.text()).not.toContain('完整系统提示词覆盖')
    const moreBtn = w.findAll('button').find((b) => b.text().includes('更多设置'))
    await moreBtn.trigger('click')
    await w.vm.$nextTick()
    expect(w.text()).toContain('完整系统提示词覆盖')
    expect(w.text()).toContain('模型参数')
    expect(w.text()).toContain('工具权限')
    const collapseBtn = w.findAll('button').find((b) => b.text().includes('收起'))
    await collapseBtn.trigger('click')
    await w.vm.$nextTick()
    expect(w.text()).not.toContain('完整系统提示词覆盖')
  })

  it('DOM 交互：large 层 radio 切换触发 toggleAgentActive', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    const radio = w.find('input[type=radio]')
    await radio.trigger('change')
    await new Promise((r) => setTimeout(r, 30))
    const put = reqs.find((r) => r.url.includes('/active'))
    expect(put).toBeTruthy()
    expect(put.method).toBe('PUT')
    // 依赖 mock 夹具：chief 初始 active=true → next=false（若改 fixtures 需同步）
    expect(put.body).toEqual({ active: false })
  })

  it('DOM 交互：人设 textarea + 保存按钮触发 savePersona', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    const ta = w.find('textarea.persona-textarea')
    await ta.setValue('DOM 输入的人设')
    const saveBtn = w.findAll('button').find((b) => b.text() === '保存')
    await saveBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    const put = reqs.find((r) => r.url.includes('/api/config/persona/'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ value: 'DOM 输入的人设' })
  })

  it('loadData 容错：tools 数组形式 + 部分请求失败', async () => {
    routeFetch([
      { match: '/api/management/orchestration', data: () => { throw new Error('net') } },
      { match: '/api/tools/ai', data: () => { throw new Error('net') } },
      { match: '/api/tools/', data: { success: true, data: { tools: ['toolA', 'toolB'] } } },
      { match: '/api/management/persona-presets', data: { success: true, data: { presets: [] } } },
    ])
    const w = mount(Orchestration, { global: { plugins: [createPinia()] } })
    await new Promise((r) => setTimeout(r, 50))
    // o 失败 → agents 空；tools 数组 → 直接透传（Array.isArray 分支）；ai 失败 → aiTools 空
    expect(w.vm.agents).toHaveLength(0)
    expect(w.vm.tools).toEqual(['toolA', 'toolB'])
    expect(w.vm.aiTools).toEqual({})
    expect(w.vm.loading).toBe(false)
  })

  it('saveRoleToolsSel 未选角色不提交', async () => {
    const reqs = mockApi()
    const w = await mountOrch()
    w.vm.roleToolSel = ''
    await w.vm.saveRoleToolsSel()
    expect(reqs.filter((r) => r.url.includes('/api/config/tools/') && r.method === 'PUT')).toHaveLength(0)
  })
})
