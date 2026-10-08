import MarkdownIt from 'markdown-it'
import katex from 'katex'
import 'katex/dist/katex.min.css'
import './math.css'
import type { Source } from './types'
import { API_BASE } from './api'

interface RenderEnvironment { citations: Map<string, string> }
const formulaCache = new Map<string, string>()
let formulaCacheChars = 0
const MAX_FORMULA_CACHE_CHARS = 500000

function addMath(md: MarkdownIt) {
  const closingAt = (src: string, close: string, start: number) => {
    let end = start
    while ((end = src.indexOf(close, end)) >= 0) {
      let escapes = 0
      for (let index = end - 1; index >= 0 && src[index] === '\\'; index -= 1) escapes += 1
      if (escapes % 2 === 0 && !(close === '$' && (src[end - 1] === '$' || src[end + 1] === '$'))) return end
      end += close.length
    }
    return -1
  }
  const render = (text: string, displayMode: boolean) => {
    const key = `${displayMode ? 'block' : 'inline'}:${text}`
    const cached = formulaCache.get(key)
    if (cached !== undefined) {
      formulaCache.delete(key); formulaCache.set(key, cached)
      return cached
    }
    let html: string
    try {
      html = katex.renderToString(text, { displayMode, throwOnError: false, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20 })
    } catch {
      html = `<code>${md.utils.escapeHtml(text)}</code>`
    }
    const size = key.length + html.length
    if (size <= MAX_FORMULA_CACHE_CHARS) {
      while (formulaCache.size >= 256 || formulaCacheChars + size > MAX_FORMULA_CACHE_CHARS) {
        const oldest = formulaCache.keys().next().value
        if (oldest === undefined) break
        formulaCacheChars -= oldest.length + formulaCache.get(oldest)!.length
        formulaCache.delete(oldest)
      }
      formulaCache.set(key, html); formulaCacheChars += size
    }
    return html
  }
  md.block.ruler.before('fence', 'math_block', (state, startLine, endLine, silent) => {
    if (state.sCount[startLine] - state.blkIndent >= 4) return false
    const first = state.src.slice(state.bMarks[startLine] + state.tShift[startLine], state.eMarks[startLine]).trim()
    const open = first.startsWith('$$') ? '$$' : first.startsWith('\\[') ? '\\[' : ''
    if (!open) return false
    const close = open === '$$' ? '$$' : '\\]'
    const lines: string[] = []
    let next = startLine + 1
    let content = first.slice(open.length)
    const firstEnd = closingAt(content, close, 0)
    if (firstEnd >= 0) {
      // Formulas followed by prose belong to the inline rule; don't consume
      // the surrounding paragraph while looking for another closing delimiter.
      if (content.slice(firstEnd + close.length).trim()) return false
      lines.push(content.slice(0, firstEnd))
    } else {
      lines.push(content)
      let closed = false
      for (; next < endLine; next += 1) {
        content = state.src.slice(state.bMarks[next] + state.tShift[next], state.eMarks[next]).trim()
        const end = closingAt(content, close, 0)
        if (end >= 0) {
          if (content.slice(end + close.length).trim()) return false
          lines.push(content.slice(0, end)); next += 1; closed = true; break
        }
        lines.push(content)
      }
      if (!closed) return false
    }
    if (silent) return true
    const token = state.push('math_block', '', 0)
    token.block = true; token.content = lines.join('\n').trim(); token.map = [startLine, next]
    state.line = next
    return true
  }, { alt: ['paragraph', 'reference', 'blockquote', 'list'] })
  md.inline.ruler.before('escape', 'math_inline', (state, silent) => {
    const pos = state.pos
    const open = state.src.startsWith('\\(', pos) ? '\\('
      : state.src.startsWith('\\[', pos) ? '\\['
        : state.src.startsWith('$$', pos) ? '$$'
          : state.src[pos] === '$' ? '$' : ''
    if (!open || (open === '$' && /\s/.test(state.src[pos + 1] || ' '))) return false
    const close = open === '\\(' ? '\\)' : open === '\\[' ? '\\]' : open
    const end = closingAt(state.src, close, pos + open.length)
    if (end < 0 || end === pos + open.length || (open === '$' && (/\s/.test(state.src[end - 1]) || /\d/.test(state.src[end + 1] || '')))) return false
    const content = state.src.slice(pos + open.length, end)
    const display = open === '$$' || open === '\\['
    if (!display && content.includes('\n')) return false
    if (!silent) {
      const token = state.push('math_inline', '', 0)
      token.content = content.trim(); token.meta = { display }
    }
    state.pos = end + close.length
    return true
  })
  md.renderer.rules.math_inline = (tokens, index) => {
    const token = tokens[index]
    return token.meta?.display
      ? `<span class="math-inline-display">${render(token.content, true)}</span>`
      : render(token.content, false)
  }
  md.renderer.rules.math_block = (tokens, index) => `<div class="math-block">${render(tokens[index].content, true)}</div>\n`
  // Only accept plain sup/sub contents, with no attributes or nested HTML.
  // Keep Markdown's general HTML handling disabled for all other input.
  md.inline.ruler.before('html_inline', 'source_script', (state, silent) => {
    if (state.src[state.pos] !== '<') return false
    const match = /^<(sup|sub)>([^<>\n]+)<\/\1>/i.exec(state.src.slice(state.pos))
    if (!match) return false
    if (!silent) {
      const token = state.push('source_script', '', 0)
      token.content = md.utils.unescapeAll(match[2]); token.tag = match[1].toLowerCase()
    }
    state.pos += match[0].length
    return true
  })
  md.renderer.rules.source_script = (tokens, index) => {
    const token = tokens[index]
    return `<${token.tag}>${md.utils.escapeHtml(token.content)}</${token.tag}>`
  }
}

function createMarkdown(notePositions: boolean): MarkdownIt {
  const md = new MarkdownIt({ html: false, linkify: false, breaks: true })
  addMath(md)
  if (notePositions) {
    md.core.ruler.push('note_positions', state => {
      for (const token of state.tokens) {
        if (token.block && token.map && (token.nesting === 1 || ['fence', 'code_block', 'math_block'].includes(token.type))) {
          token.attrSet('data-note-start', String(token.map[0]))
          token.attrSet('data-note-end', String(token.map[1]))
        }
      }
    })
    // Custom renderers need to forward the same source-line information.
    for (const type of ['fence', 'code_block', 'math_block']) {
      const renderer = md.renderer.rules[type]
      if (renderer) md.renderer.rules[type] = (tokens, index, options, env, self) => {
        const token = tokens[index]
        const body = renderer(tokens, index, options, env, self)
        return token.map ? `<div data-note-start="${token.map[0]}" data-note-end="${token.map[1]}">${body}</div>` : body
      }
    }
  }
  md.inline.ruler.before('link', 'verified_citation', (state, silent) => {
    if (state.src[state.pos] !== '[') return false
    const allowed = (state.env as RenderEnvironment).citations
    const match = /^\[(S[a-zA-Z0-9_-]+)\]/.exec(state.src.slice(state.pos))
    if (!match || !allowed.has(match[1])) return false
    if (!silent) {
      const token = state.push('verified_citation', '', 0)
      token.content = match[1]
      token.meta = { id: allowed.get(match[1]) }
    }
    state.pos += match[0].length
    return true
  })
  md.renderer.rules.verified_citation = (tokens, index) => {
    const token = tokens[index]
    return `<button type="button" class="inline-citation" data-source-id="${md.utils.escapeHtml(token.meta.id)}" title="查看引用原文">${md.utils.escapeHtml(token.content.length > 10 ? `${token.content.slice(0, 7)}…` : token.content)}</button>`
  }
  const original = md.renderer.rules.link_open
  md.renderer.rules.link_open = (tokens, index, options, env, self) => {
    tokens[index].attrSet('target', '_blank')
    tokens[index].attrSet('rel', 'noopener noreferrer')
    return original ? original(tokens, index, options, env, self) : self.renderToken(tokens, index, options)
  }
  // Only serve immutable figures extracted locally; remote image URLs stay inert.
  md.renderer.rules.image = (tokens, index) => {
    const token = tokens[index]
    const src = token.attrGet('src') || ''
    const alt = md.utils.escapeHtml(token.content || '原题图片')
    if (/^\/question-images\/[a-f0-9]{64}\.png$/.test(src)) {
      if (token.content.includes('行内公式')) {
        return `<img class="question-figure question-formula" src="${md.utils.escapeHtml(API_BASE + src)}" alt="${alt}" loading="lazy" style="height:1.6em;max-width:100%;width:auto;display:inline-block;vertical-align:middle;margin:0 4px;border-radius:0" />`
      }
      return `<img class="question-figure" src="${md.utils.escapeHtml(API_BASE + src)}" alt="${alt}" loading="lazy" style="max-width:100%;height:auto;display:block;margin:12px 0" />`
    }
    return `<span class="muted">[图片：${alt}]</span>`
  }
  return md
}

const parsers = new Map<boolean, MarkdownIt>()

export function renderMarkdown(content: string, sources: Source[] = [], notePositions = false): string {
  let md = parsers.get(notePositions)
  if (!md) { md = createMarkdown(notePositions); parsers.set(notePositions, md) }
  // Per-render evidence belongs in env, never in shared parser closures.
  const citations = new Map<string, string>()
  for (const source of sources) {
    citations.set(`S${source.chunk_id}`, source.chunk_id)
    if (source.source_id) {
      citations.set(source.source_id.replace(/^\[|\]$/g, ''), source.chunk_id)
      citations.set(`S${source.source_id.replace(/^S/, '')}`, source.chunk_id)
    }
  }
  return md.render(content, { citations } satisfies RenderEnvironment)
}
