import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.min.css'
import { escapeHtml } from './escape.js'

const md = new MarkdownIt({
  breaks: true,
  html: false,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return hljs.highlight(code, { language: lang }).value } catch {}
    }
    try { return hljs.highlightAuto(code).value } catch {}
    return escapeHtml(code)
  }
})

// ── 行内代码样式 ──

function styleInlineCode(html) {
  return html.replace(/<code>([^<]+)<\/code>/g, '<code class="inlines-code">$1</code>')
}

// ── 完整渲染（向后兼容，非代码块场景使用）──

export function renderMarkdown(text) {
  if (!text) return ''
  return styleInlineCode(md.render(text))
}

// ── 结构化解析（拆分 text / code 片段）──

/**
 * 将 Markdown 文本解析为结构化片段数组
 *
 * 返回 [{ type: 'text', html }, { type: 'code', language, code, highlightedHtml }]
 *
 * 原理：使用 markdown-it 的 tokenizer 遍历 AST，代码块作为独立 code 片段，
 * 其余内容渲染为 HTML 归入 text 片段。
 */
export function parseMarkdownSegments(text) {
  if (!text) return [{ type: 'text', html: '' }]

  const tokens = md.parse(text, {})
  const rawSegments = []

  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i]

    // ── 围栏代码块 (```js ... ```) ──
    if (token.type === 'fence' && token.tag === 'code') {
      const lang = token.info || ''
      const rawCode = token.content
      // 渲染高亮 HTML
      const highlightedHtml = md.renderer.render([token], md.options, {})
      rawSegments.push({
        type: 'code',
        language: lang,
        code: rawCode,
        highlightedHtml,
      })
      continue
    }

    // ── 行内文本（段落、标题里的文字）──
    if (token.type === 'inline') {
      const html = md.renderer.renderInline(token.children, md.options, {})
      if (html.trim()) {
        rawSegments.push({ type: 'text', html: styleInlineCode(html) })
      }
      continue
    }

    // ── HTML 块 ──
    if (token.type === 'html_block') {
      // html:false 下通常不会出现该 token，但保守起见转义，避免 XSS
      rawSegments.push({ type: 'text', html: escapeHtml(token.content) })
      continue
    }

    // ── 分割线 ──
    if (token.type === 'hr') {
      rawSegments.push({ type: 'text', html: '<hr>' })
      continue
    }

    // ── 块级容器（p / h1-h6 / ul / ol / blockquote / table）──
    // 遇到 _open token，收集到对应的 _close，整体渲染
    if (token.type.endsWith('_open')) {
      const closeType = token.type.replace('_open', '_close')
      const block = [token]
      let depth = 1
      i++
      while (i < tokens.length && depth > 0) {
        block.push(tokens[i])
        if (tokens[i].type === token.type) depth++
        if (tokens[i].type === closeType) depth--
        if (depth > 0) i++
      }
      const html = md.renderer.render(block, md.options, {})
      if (html.trim()) {
        rawSegments.push({ type: 'text', html })
      }
    }
    // _close 和纯结构 token 跳过
  }

  // ── 合并相邻 text 片段（避免碎块）──
  const merged = []
  for (const seg of rawSegments) {
    const last = merged[merged.length - 1]
    if (last && last.type === 'text' && seg.type === 'text') {
      last.html += seg.html
    } else {
      merged.push(seg)
    }
  }

  return merged.length ? merged : [{ type: 'text', html: '' }]
}

// ── 复制功能（兼容 PyQt6）──

export function copyCodeBlock(text) {
  if (navigator.clipboard?.writeText) {
    return navigator.clipboard.writeText(text)
  }
  // 桌面壳 fallback：textarea + execCommand
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.left = '-9999px'
    ta.style.top = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok ? Promise.resolve() : Promise.reject(new Error('execCommand failed'))
  } catch (e) {
    return Promise.reject(e)
  }
}
