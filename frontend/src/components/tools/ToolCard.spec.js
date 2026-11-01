import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ToolCard from './ToolCard.vue'

function mountTrace(trace) {
  return mount(ToolCard, { props: { trace } })
}

describe('ToolCard', () => {
  it('渲染头部：名称 / 状态 / 耗时 / 参数', () => {
    const w = mountTrace({
      name: 'read_text_file', args: { path: 'a.py' }, status: 'ok', durationMs: 12,
    })
    expect(w.text()).toContain('read_text_file')
    expect(w.text()).toContain('成功')
    expect(w.text()).toContain('12ms')
    expect(w.find('.trace-args').text()).toContain('a.py')
  })

  it('terminal 卡片：命令 + cwd + exit code + 输出', () => {
    const w = mountTrace({
      name: 'exec_command',
      args: { command: 'pytest -q' },
      status: 'ok',
      view: {
        card: 'terminal', title: 'pytest -q', command: 'pytest -q',
        cwd: '/repo', exit_code: 0, output: '3 passed',
      },
    })
    expect(w.find('.tool-card-terminal').exists()).toBe(true)
    expect(w.text()).toContain('pytest -q')
    expect(w.text()).toContain('/repo')
    expect(w.text()).toContain('exit 0')
    expect(w.find('.tool-card-output').text()).toContain('3 passed')
  })

  it('terminal 卡片：运行中无输出时显示等待', () => {
    const w = mountTrace({
      name: 'exec_command', status: 'running',
      view: { card: 'terminal', title: 'sleep 5', command: 'sleep 5', output: '' },
    })
    expect(w.find('.tool-card-empty').text()).toContain('等待结果')
  })

  it('diff 卡片：+N / -M 与着色行', () => {
    const w = mountTrace({
      name: 'write_text_file', status: 'ok',
      view: {
        card: 'diff', title: 'x.py', action: 'updated', additions: 1, deletions: 1,
        diffs: [{ path: 'x.py', lines: ['@@ -1 +1 @@', '-old', '+new'] }],
      },
    })
    expect(w.text()).toContain('+1')
    expect(w.text()).toContain('-1')
    expect(w.findAll('.diff-line.diff-add')).toHaveLength(1)
    expect(w.findAll('.diff-line.diff-del')).toHaveLength(1)
    expect(w.find('.diff-line.diff-hunk').text()).toBe('@@ -1 +1 @@')
  })

  it('read 卡片：带行号的文件片段', () => {
    const w = mountTrace({
      name: 'read_text_file', status: 'ok',
      view: {
        card: 'read', title: 'a.py', path: '/repo/a.py', total_lines: 2,
        lines: [[1, 'import os'], [2, 'print(1)']], truncated: false,
      },
    })
    const nums = w.findAll('.tool-card-ln').map(n => n.text())
    expect(nums).toEqual(['1', '2'])
    expect(w.text()).toContain('import os')
    expect(w.text()).toContain('2 行')
  })

  it('search 卡片：命中路径与片段', () => {
    const w = mountTrace({
      name: 'knowledge_search', status: 'ok',
      view: {
        card: 'search', title: '事件表', total: 1,
        matches: [{ path: 'docs/A.md', line: 12, text: '4.1 事件表' }],
      },
    })
    expect(w.text()).toContain('docs/A.md:12')
    expect(w.text()).toContain('4.1 事件表')
    expect(w.text()).toContain('1 处命中')
  })

  it('web 卡片：搜索来源列表用可点链接', () => {
    const w = mountTrace({
      name: 'web_search', status: 'ok',
      view: {
        card: 'web', kind: 'search', title: 'vue 3.5', total: 1,
        sources: [{ title: 'Vue 3.5', url: 'https://vuejs.org', snippet: 'release' }],
      },
    })
    const link = w.find('a.tool-card-link')
    expect(link.attributes('href')).toBe('https://vuejs.org')
    expect(link.attributes('rel')).toContain('noopener')
    expect(link.text()).toBe('Vue 3.5')
  })

  it('web 卡片：单页抓取显示 URL 与摘要', () => {
    const w = mountTrace({
      name: 'web_fetch', status: 'ok',
      view: {
        card: 'web', kind: 'page', title: 'https://e.com/a', url: 'https://e.com/a',
        status_code: 200, excerpt: '正文摘要', sources: [], total: 1,
      },
    })
    expect(w.text()).toContain('HTTP 200')
    expect(w.text()).toContain('正文摘要')
    expect(w.find('a.tool-card-link').attributes('href')).toBe('https://e.com/a')
  })

  it('未知工具/无 view → generic 卡片显示结果预览', () => {
    const w = mountTrace({
      name: 'calc', args: { expression: '1+1' }, status: 'ok', preview: '2',
    })
    expect(w.find('.tool-card-generic').exists()).toBe(true)
    expect(w.find('.tool-card-output').text()).toBe('2')
  })

  it('错误行统一渲染', () => {
    const w = mountTrace({
      name: 'exec_command', status: 'error', error: 'exit 1',
      view: { card: 'terminal', title: 'false', output: '' },
    })
    expect(w.find('.trace-preview.is-error').text()).toBe('exit 1')
  })
})
