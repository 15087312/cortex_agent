import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Skills from './Skills.vue'
import { routeFetch } from '@/test/helpers.js'
import { dialogState, resolveDialog } from '@/composables/useDialog.js'

const skills = [
  { id: 'code_review', name: '代码审查专家', description: '审查代码质量', enabled: true, keywords: ['review'], trigger: { include: ['审查', 'review'] }, metadata: { type: 'builtin' }, tool_rules: {} },
  { id: 'web_search', name: '联网搜索', description: '搜索互联网', enabled: false, keywords: [], trigger: {}, tool_rules: {} },
]
const agents = [{ role: 'expert_writer', name: '写作专家', tier: 'expert' }]

function m(url, init) {
  return String(url)
}

function mockApi(overrides = {}) {
  const reqs = []
  routeFetch([
    { match: (u, init) => String(u).includes('/forced') && (!init?.method || init.method === 'GET'), data: () => (overrides.forced || { success: true, data: { forced_skill: '' } }) },
    { match: (u, init) => String(u).includes('/forced') && init?.method === 'PUT', data: (u, init) => { reqs.push({ url: String(u), body: JSON.parse(init.body) }); return { success: true, data: { forced_skill: 'code_review', skill: { name: '代码审查专家' } } } } },
    { match: (u) => String(u).includes('/skills/reload'), data: (u, init) => { reqs.push({ url: String(u), method: init?.method }); return { success: true, data: { count: 2 } } } },
    { match: (u, init) => String(u).includes('/skills/') && init?.method === 'DELETE', data: (u) => { reqs.push({ url: String(u), method: 'DELETE' }); return { success: true } } },
    { match: (u, init) => String(u).includes('/enabled') && init?.method === 'PUT', data: (u, init) => { reqs.push({ url: String(u), body: JSON.parse(init.body) }); return { success: true } } },
    { match: (u, init) => String(u).includes('/api/management/skills') && init?.method === 'POST', data: (u, init) => { reqs.push({ url: String(u), body: JSON.parse(init.body) }); return { success: true } } },
    { match: (u, init) => String(u).includes('/api/management/skills/') && init?.method === 'PUT', data: (u, init) => { reqs.push({ url: String(u), body: JSON.parse(init.body) }); return { success: true } } },
    { match: '/api/management/skills', data: { success: true, data: { skills: overrides.skills || skills } } },
    { match: '/api/management/orchestration', data: { success: true, data: { agents } } },
    { match: '/api/management/config/role-skills/', data: (u, init) => {
      if (init?.method === 'PUT') { reqs.push({ url: String(u), method: init.method, body: JSON.parse(init.body) }); return { success: true } }
      return { success: true, data: { skills: ['code_review'] } }
    } },
  ])
  return reqs
}

async function mountSkills() {
  const w = mount(Skills, { global: { plugins: [createPinia()] } })
  await new Promise((r) => setTimeout(r, 40))
  return w
}

describe('Skills.vue', () => {
  beforeEach(() => {
    dialogState().visible = false
  })

  it('加载并渲染技能列表（含内置/禁用徽章）', async () => {
    mockApi()
    const w = await mountSkills()
    expect(w.text()).toContain('技能列表（2）')
    expect(w.text()).toContain('代码审查专家')
    expect(w.text()).toContain('内置')
    expect(w.text()).toContain('已禁用')
    // 选中技能后显示触发词
    await w.findAll('.skill-item')[0].trigger('click')
    await w.vm.$nextTick()
    expect(w.text()).toContain('审查, review')
  })

  it('渲染全局强制技能状态', async () => {
    mockApi({ forced: { success: true, data: { forced_skill: 'code_review' } } })
    const w = await mountSkills()
    expect(w.text()).toContain('当前强制')
    expect(w.text()).toContain('代码审查专家')
  })

  it('saveForced 设置强制技能', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    await w.find('select').setValue('code_review')
    await w.vm.$nextTick()
    await w.findAll('button').find((b) => b.text().includes('设为强制')).trigger('click')
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/forced'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ skill_id: 'code_review' })
  })

  it('clearForced 解除强制技能', async () => {
    const reqs = mockApi({ forced: { success: true, data: { forced_skill: 'code_review' } } })
    const w = await mountSkills()
    await w.findAll('button').find((b) => b.text().includes('解除强制')).trigger('click')
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/forced'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ skill_id: '' })
    expect(w.text()).not.toContain('当前强制')
  })

  it('toggleEnabled 切换技能启用状态', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    const cb = w.find('.skill-item input[type=checkbox]')
    await cb.setValue(false)
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/enabled'))
    expect(put).toBeTruthy()
    expect(put.body).toEqual({ enabled: false })
  })

  it('removeSkill 删除技能（confirm 通过）', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    w.vm.removeSkill(skills[0])
    await new Promise((r) => setTimeout(r, 10))
    expect(dialogState().visible).toBe(true)
    resolveDialog(true)
    await new Promise((r) => setTimeout(r, 30))
    const del = reqs.find((r) => r.method === 'DELETE')
    expect(del).toBeTruthy()
    expect(del.url).toContain('/api/management/skills/code_review')
  })

  it('reloadSkills 重载技能', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    await w.findAll('button').find((b) => b.text().includes('重载')).trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    expect(reqs.some((r) => r.url.includes('/skills/reload'))).toBe(true)
  })

  it('新建技能：表单提交 POST', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    await w.findAll('button').find((b) => b.text().includes('新建技能')).trigger('click')
    expect(w.text()).toContain('新建技能')
    await w.find('input[placeholder*="id（小写"]').setValue('my_skill')
    await w.find('input[placeholder="名称（如：代码审查专家）"]').setValue('我的技能')
    await w.findAll('button').find((b) => b.text().includes('保存')).trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    const post = reqs.find((r) => r.url.includes('/api/management/skills') && !r.url.includes('/reload') && !r.url.includes('/forced'))
    expect(post).toBeTruthy()
    expect(post.body.id).toBe('my_skill')
    expect(post.body.name).toBe('我的技能')
  })

  it('编辑技能：表单提交 PUT', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    w.vm.openEdit('code_review')
    await w.vm.$nextTick()
    expect(w.text()).toContain('编辑技能：code_review')
    await w.findAll('button').find((b) => b.text().includes('保存')).trigger('click')
    await new Promise((r) => setTimeout(r, 30))
    const put = reqs.find((r) => r.url.includes('/api/management/skills/') && !r.url.includes('/enabled') && !r.url.includes('/forced'))
    expect(put).toBeTruthy()
    expect(put.body.name).toBe('代码审查专家')
    expect(put.body.keywords).toEqual(['review'])
  })

  it('无效 JSON 触发规则时提交被拦截', async () => {
    mockApi()
    const w = await mountSkills()
    await w.findAll('button').find((b) => b.text().includes('新建技能')).trigger('click')
    await w.find('input[placeholder*="id（小写"]').setValue('x')
    await w.find('input[placeholder="名称（如：代码审查专家）"]').setValue('X')
    await w.find('textarea').setValue('desc')
    // trigger 字段填无效 JSON
    const trig = w.findAll('input').find((i) => (i.attributes('placeholder') || '').includes('触发'))
    await trig.setValue('{bad json')
    await w.findAll('button').find((b) => b.text().includes('保存')).trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.showForm).toBe(true)
  })

  it('loadRoleSkills 加载角色技能白名单', async () => {
    mockApi()
    const w = await mountSkills()
    w.vm.roleSel = 'expert_writer'
    await w.vm.loadRoleSkills()
    await new Promise((r) => setTimeout(r, 20))
    expect(w.vm.roleSkills).toEqual(['code_review'])
    // 未选角色 → 清空
    w.vm.roleSel = ''
    await w.vm.loadRoleSkills()
    expect(w.vm.roleSkills).toEqual([])
  })

  it('saveRoleSkills 保存角色技能白名单', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    w.vm.roleSel = 'expert_writer'
    w.vm.roleSkills = ['code_review']
    await w.vm.saveRoleSkills()
    await new Promise((r) => setTimeout(r, 20))
    const put = reqs.find((r) => r.url.includes('/role-skills/') && r.method === 'PUT')
    expect(put).toBeTruthy()
    expect(put.url).toContain('expert_writer')
    expect(put.body.skills).toEqual(['code_review'])
  })

  it('saveRoleSkills 未选角色不提交', async () => {
    const reqs = mockApi()
    const w = await mountSkills()
    w.vm.roleSel = ''
    await w.vm.saveRoleSkills()
    // 未选角色 → 不发任何 PUT 请求
    expect(reqs.filter((r) => r.url.includes('/role-skills/') && r.method === 'PUT')).toHaveLength(0)
  })
})
