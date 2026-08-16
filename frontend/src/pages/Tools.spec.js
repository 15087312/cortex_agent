import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import Tools from './Tools.vue'
import { routeFetch } from '@/test/helpers.js'
import { endpoints } from '@/api.js'

let wrapper = null
function mountPage() {
  wrapper = mount(Tools, {
    global: { plugins: [createPinia()] },
  })
  return wrapper
}

describe('Tools 页面', () => {
  afterEach(() => {
    // 卸载组件 → onBeforeUnmount 清理 30s 轮询定时器
    if (wrapper) { wrapper.unmount(); wrapper = null }
  })
  it('加载工具列表（对象与数组两种形态）并支持过滤', async () => {
    routeFetch([
      {
        match: '/tools',
        data: { data: { tools: { exec: { description: '执行命令' }, calc: { description: '计算' } } } },
      },
      { match: '/tools/events?limit=20', data: { data: { events: [] } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.tools.length).toBe(2)
    expect(w.vm.filteredTools.length).toBe(2)
    w.vm.query = 'exec'
    await w.vm.$nextTick()
    expect(w.vm.filteredTools.length).toBe(1)
    expect(w.vm.filteredTools[0].name).toBe('exec')
  })

  it('handleSelect 加载工具参数定义并生成表单字段', async () => {
    routeFetch([
      {
        match: '/tools/info/exec',
        data: {
          data: {
            spec: {
              params: {
                command: { type: 'string', required: true, description: '命令' },
                timeout: { type: 'integer', description: '超时' },
              },
            },
          },
        },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    await w.vm.handleSelect('exec')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.toolInfo).not.toBeNull()
    expect(w.vm.formFields.length).toBe(2)
    expect(w.vm.formFields[0].type).toBe('string')
    expect(w.vm.formFields[1].type).toBe('number')
    expect(w.vm.formFields[0].required).toBe(true)
  })

  it('buildFields 数组形态与布尔类型', () => {
    const w = mountPage()
    const fields = w.vm.buildFields([{ type: 'boolean' }, 'plain'])
    expect(fields[0].type).toBe('boolean')
    expect(fields[1].type).toBe('string')
    expect(fields[1].description).toBe('plain')
  })

  it('buildParamsFromForm 类型转换', () => {
    const w = mountPage()
    w.vm.formFields = [
      { key: 'n', type: 'number' },
      { key: 'b', type: 'boolean' },
      { key: 's', type: 'string' },
      { key: 'empty', type: 'string' },
    ]
    w.vm.formValues = { n: '42', b: true, s: 'hi', empty: '' }
    const p = w.vm.buildParamsFromForm()
    expect(p).toEqual({ n: 42, b: true, s: 'hi' })
  })

  it('handleCall 调用工具并展示结果', async () => {
    let body = null
    routeFetch([
      { match: '/tools/call', data: (u, init) => { body = JSON.parse(init.body); return { data: { ok: true } } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.selected = 'exec'
    w.vm.formFields = [{ key: 'command', type: 'string' }]
    w.vm.formValues = { command: 'ls' }
    await w.vm.handleCall()
    await new Promise((r) => setTimeout(r, 10))
    expect(body.tool_name).toBe('exec')
    expect(body.params.command).toBe('ls')
    expect(w.vm.toolResult).toContain('ok')
  })

  it('handleCall JSON 模式解析失败给提示', async () => {
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 20))
    await w.vm.$nextTick()
    w.vm.selected = 'exec'
    w.vm.showJson = true
    w.vm.jsonText = '{bad json'
    await w.vm.handleCall()
    expect(w.vm.toolResult).toBeNull() // JSON.parse 失败提前 return
  })

  it('工具列表数组形态（rawTools.map 箭头）', async () => {
    routeFetch([
      { match: '/tools', data: { data: { tools: ['exec', { name: 'calc', description: '计算' }] } } },
      { match: '/tools/events?limit=20', data: { data: { events: [] } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.tools.map(t => t.name)).toEqual(['exec', 'calc'])
  })

  it('加载失败容错（tools/toolEvents reject 的 catch 回退）', async () => {
    vi.spyOn(endpoints, 'tools').mockRejectedValue(new Error('x'))
    vi.spyOn(endpoints, 'toolEvents').mockRejectedValue(new Error('y'))
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.vm.tools).toEqual([])
    expect(w.vm.events).toEqual([])
  })

  it('DOM 交互：搜索过滤 + 点击工具项 + JSON 模式切换 + 表单输入执行', async () => {
    let body = null
    routeFetch([
      {
        match: '/tools/info/exec',
        data: { data: { spec: { params: { command: { type: 'string', required: true }, timeout: { type: 'integer' } } } } },
      },
      { match: '/tools/call', data: (u, init) => { body = JSON.parse(init.body); return { data: { ok: true } } } },
      { match: '/tools/events?limit=20', data: { data: { events: [] } } },
      {
        match: '/tools',
        data: { data: { tools: { exec: { description: '执行命令' }, calc: { description: '计算' } } } },
      },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()

    // 搜索框 v-model
    const search = w.find('.tool-search input')
    await search.setValue('exec')
    expect(w.vm.filteredTools.length).toBe(1)

    // 点击工具项
    const item = w.find('.tool-item')
    await item.trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(w.vm.selected).toBe('exec')
    expect(w.vm.formFields.length).toBe(2)

    // 表单输入（string + number 参数）
    const inputs = w.findAll('.tool-param-row input')
    await inputs[0].setValue('ls -la')
    await inputs[1].setValue('5')
    await w.vm.$nextTick()
    expect(w.vm.formValues.command).toBe('ls -la')
    expect(w.vm.formValues.timeout).toBe(5)

    // JSON 模式切换
    const toggleBtn = w.find('.tool-call-section button')
    await toggleBtn.trigger('click')
    expect(w.vm.showJson).toBe(true)
    await w.vm.$nextTick()
    // JSON 文本域编辑 + 执行
    const ta = w.find('.tool-json-textarea')
    await ta.setValue('{"command":"pwd"}')
    await w.find('.tool-exec-btn button').trigger('click')
    await new Promise((r) => setTimeout(r, 10))
    expect(body.tool_name).toBe('exec')
    expect(body.params).toEqual({ command: 'pwd' })
    expect(w.vm.toolResult).toContain('ok')
  })

  it('调用历史非空渲染表格行（v-for events）', async () => {
    routeFetch([
      {
        match: '/tools/events?limit=20',
        data: { data: { events: [{ id: 'e1', tool_name: 'exec', timestamp: 1700000000 }] } },
      },
      { match: '/tools', data: { data: { tools: {} } } },
    ])
    const w = mountPage()
    await new Promise((r) => setTimeout(r, 30))
    await w.vm.$nextTick()
    expect(w.find('.data-table').exists()).toBe(true)
    expect(w.text()).toContain('exec')
  })
})
