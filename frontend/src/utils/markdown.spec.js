import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderMarkdown, parseMarkdownSegments, copyCodeBlock } from './markdown.js'

describe('renderMarkdown', () => {
  it('空输入返回空串', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
  })

  it('渲染普通文本', () => {
    expect(renderMarkdown('hello')).toContain('hello')
  })

  it('渲染标题与加粗', () => {
    const html = renderMarkdown('# Title\n\n**bold**')
    expect(html).toContain('<h1>Title</h1>')
    expect(html).toContain('<strong>bold</strong>')
  })

  it('行内代码带样式类', () => {
    const html = renderMarkdown('use `code` here')
    expect(html).toContain('class="inlines-code"')
  })

  it('代码块语法高亮', () => {
    const html = renderMarkdown('```js\nconst x = 1\n```')
    expect(html).toContain('<pre')
    expect(html).toContain('<code')
  })
})

describe('parseMarkdownSegments', () => {
  it('空输入返回空 text 片段', () => {
    const segs = parseMarkdownSegments('')
    expect(segs).toHaveLength(1)
    expect(segs[0].type).toBe('text')
    expect(segs[0].html).toBe('')
  })

  it('纯文本返回 text 片段', () => {
    const segs = parseMarkdownSegments('just text')
    expect(segs.some(s => s.type === 'text' && s.html.includes('just text'))).toBe(true)
  })

  it('围栏代码块拆为 code 片段', () => {
    const segs = parseMarkdownSegments('before\n```python\nprint(1)\n```\nafter')
    const code = segs.find(s => s.type === 'code')
    expect(code).toBeTruthy()
    expect(code.language).toBe('python')
    expect(code.code).toBe('print(1)\n')
    expect(code.highlightedHtml).toContain('<pre')
  })

  it('相邻 text 片段合并', () => {
    const segs = parseMarkdownSegments('# H\n\npara')
    const texts = segs.filter(s => s.type === 'text')
    expect(texts).toHaveLength(1)
    expect(texts[0].html).toContain('<h1>H</h1>')
    expect(texts[0].html).toContain('<p>para</p>')
  })

  it('分割线渲染为 hr', () => {
    const segs = parseMarkdownSegments('---')
    expect(segs.some(s => s.type === 'text' && s.html.includes('<hr>'))).toBe(true)
  })
})

describe('copyCodeBlock', () => {
  const originalClipboard = navigator.clipboard
  const originalExec = document.execCommand

  afterEach(() => {
    vi.unstubAllGlobals()
    document.execCommand = originalExec
  })

  it('优先使用 navigator.clipboard', async () => {
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true, writable: true })
    await copyCodeBlock('code')
    expect(writeText).toHaveBeenCalledWith('code')
    Object.defineProperty(navigator, 'clipboard', { value: originalClipboard, configurable: true, writable: true })
  })

  it('clipboard 不可用时回退 execCommand', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true, writable: true })
    document.execCommand = vi.fn(() => true)
    await expect(copyCodeBlock('x')).resolves.toBeUndefined()
    Object.defineProperty(navigator, 'clipboard', { value: originalClipboard, configurable: true, writable: true })
  })

  it('execCommand 失败时 reject', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true, writable: true })
    document.execCommand = vi.fn(() => false)
    await expect(copyCodeBlock('x')).rejects.toThrow()
    Object.defineProperty(navigator, 'clipboard', { value: originalClipboard, configurable: true, writable: true })
  })
})
