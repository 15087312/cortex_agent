import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import TrajectoryTimeline from './TrajectoryTimeline.vue'

function mountTl(props = {}) {
  return mount(TrajectoryTimeline, { props })
}

describe('TrajectoryTimeline', () => {
  it('默认为折叠态：只显示头部与步数', () => {
    const w = mountTl({ entries: [{ kind: 'thinking', title: '思考中', ts: 1 }] })
    expect(w.find('.trajectory-head').exists()).toBe(true)
    expect(w.text()).toContain('思考轨迹')
    expect(w.text()).toContain('1 步')
    expect(w.find('.trajectory-body').exists()).toBe(false)
  })

  it('点击头部展开/收起', async () => {
    const w = mountTl({ entries: [{ kind: 'thinking', title: '思考中', ts: 1 }], open: true })
    expect(w.find('.trajectory-body').exists()).toBe(true)
    await w.find('.trajectory-head').trigger('click')
    expect(w.find('.trajectory-body').exists()).toBe(false)
    await w.find('.trajectory-head').trigger('click')
    expect(w.find('.trajectory-body').exists()).toBe(true)
  })

  it('空列表显示 emptyText', () => {
    const w = mountTl({ entries: [], open: true, emptyText: '暂无轨迹' })
    expect(w.find('.trajectory-empty').text()).toBe('暂无轨迹')
    expect(w.find('.timeline').exists()).toBe(false)
  })

  it('按 ts 升序排序', () => {
    const w = mountTl({
      open: true,
      entries: [
        { kind: 'done', title: '第三', ts: 30 },
        { kind: 'thinking', title: '第一', ts: 10 },
        { kind: 'command', title: '第二', ts: 20 },
      ],
    })
    const titles = w.findAll('.tl-title').map(n => n.text())
    expect(titles).toEqual(['第一', '第二', '第三'])
  })

  it('各类 kind 渲染对应中文标签（未知 kind 回退为工具）', () => {
    const kinds = ['skill', 'thinking', 'command', 'read', 'search', 'web', 'diff', 'tool', 'done', 'weird']
    const w = mountTl({ open: true, entries: kinds.map((kind, i) => ({ kind, title: `t${i}`, ts: i })) })
    const tags = w.findAll('.tl-tag').map(n => n.text())
    expect(tags).toEqual(['技能', '思考', '命令', '读取', '搜索', '查看', '修改', '工具', '完成', '工具'])
  })

  it('done 条目加 tl-done 且不显示状态徽标', () => {
    const w = mountTl({ open: true, entries: [{ kind: 'done', title: '完成', status: 'ok', ts: 1 }] })
    expect(w.find('.tl-item').classes()).toContain('tl-done')
    expect(w.find('.tl-status').exists()).toBe(false)
  })

  it('状态徽标映射（含未知状态回退为完成）', () => {
    const items = [
      { kind: 'tool', title: 'a', status: 'running', ts: 1 },
      { kind: 'tool', title: 'b', status: 'ok', ts: 2 },
      { kind: 'tool', title: 'c', status: 'error', ts: 3 },
      { kind: 'tool', title: 'd', status: 'timeout', ts: 4 },
      { kind: 'tool', title: 'e', status: 'denied', ts: 5 },
      { kind: 'tool', title: 'f', status: 'invalid', ts: 6 },
      { kind: 'tool', title: 'g', status: 'interrupted', ts: 7 },
      { kind: 'tool', title: 'h', status: 'unknown', ts: 8 },
    ]
    const w = mountTl({ open: true, entries: items })
    const labels = w.findAll('.tl-status').map(n => n.text())
    expect(labels).toEqual(['运行中', '完成', '失败', '超时', '被拒', '参数错误', '中断', '完成'])
    const classes = w.findAll('.tl-status').map(n => n.classes())
    expect(classes[0]).toContain('st-running')
    expect(classes[1]).toContain('st-ok')
    expect(classes[2]).toContain('st-error')
    expect(classes[4]).toContain('st-denied')
  })

  it('展示耗时', () => {
    const w = mountTl({ open: true, entries: [{ kind: 'command', title: 'x', ts: 1, durationMs: 1200 }] })
    expect(w.text()).toContain('1200ms')
  })

  it('thinking 条目可展开为标题正文，再次点击收起', async () => {
    const w = mountTl({ open: true, entries: [{ kind: 'thinking', title: '分析问题', ts: 1 }] })
    const row = w.find('.tl-row')
    expect(row.classes()).toContain('tl-clickable')
    await row.trigger('click')
    expect(w.find('.tl-detail').text()).toBe('分析问题')
    await row.trigger('click')
    expect(w.find('.tl-detail').exists()).toBe(false)
  })

  it('terminal 卡片细节包含命令/cwd/exit/输出', async () => {
    const entries = [{
      kind: 'command', title: 'pytest', ts: 1,
      view: { card: 'terminal', command: 'pytest -q', cwd: '/repo', exit_code: 0, output: '2 passed' },
    }]
    const w = mountTl({ open: true, entries })
    await w.find('.tl-row').trigger('click')
    const text = w.find('.tl-detail').text()
    expect(text).toContain('$ pytest -q')
    expect(text).toContain('cwd: /repo')
    expect(text).toContain('exit: 0')
    expect(text).toContain('2 passed')
  })

  it('read 卡片细节包含路径/行数/截断标记', async () => {
    const entries = [{ kind: 'read', title: 'a.py', ts: 1, view: { card: 'read', path: 'a.py', total_lines: 12, truncated: true } }]
    const w = mountTl({ open: true, entries })
    await w.find('.tl-row').trigger('click')
    const text = w.find('.tl-detail').text()
    expect(text).toContain('a.py')
    expect(text).toContain('共 12 行（已截断）')
  })

  it('search / web / diff 卡片细节', async () => {
    const entries = [
      { kind: 'search', title: 'TODO', ts: 1, view: { card: 'search', title: 'TODO', total: 3 } },
      { kind: 'web', title: 'docs', ts: 2, view: { card: 'web', url: 'https://a' } },
      { kind: 'web', title: 'src', ts: 3, view: { card: 'web', sources: [{ title: 'S1' }, { url: 'u2' }] } },
      { kind: 'diff', title: 'edit', ts: 4, view: { card: 'diff', path: 'b.py', additions: 5, deletions: 2 } },
    ]
    const w = mountTl({ open: true, entries })
    const rows = w.findAll('.tl-row')
    await rows[0].trigger('click')
    expect(w.findAll('.tl-detail')[0].text()).toBe('TODO — 3 条匹配')
    await rows[1].trigger('click')
    expect(w.findAll('.tl-detail')[1].text()).toBe('https://a')
    await rows[2].trigger('click')
    expect(w.findAll('.tl-detail')[2].text()).toBe('• S1\n• u2')
    await rows[3].trigger('click')
    expect(w.findAll('.tl-detail')[3].text()).toBe('b.py (+5 / -2)')
  })

  it('无 view 时回退到 preview/error', async () => {
    const w = mountTl({ open: true, entries: [{ kind: 'tool', title: 'x', ts: 1, error: 'boom' }] })
    await w.find('.tl-row').trigger('click')
    expect(w.find('.tl-detail').text()).toBe('boom')
  })

  it('done 条目不可展开', async () => {
    const w = mountTl({ open: true, entries: [{ kind: 'done', title: '完成', ts: 1 }] })
    const row = w.find('.tl-row')
    expect(row.classes()).not.toContain('tl-clickable')
    await row.trigger('click')
    expect(w.find('.tl-detail').exists()).toBe(false)
  })
})
