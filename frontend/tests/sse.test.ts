import { describe, expect, it } from 'vitest'
import { readSSE } from '../src/sse'
import { renderMarkdown } from '../src/markdown'
import type { Source } from '../src/types'

describe('SSE transport', () => {
  it('retains UTF-8 and CRLF under every-byte splitting with multi-line data', async () => {
    const input = new TextEncoder().encode(': heartbeat\r\nevent: delta\r\ndata: {"text":\r\ndata: "复习资料"}\r\n\r\nevent: done\ndata: {"content":"完成"}\n\n')
    const stream = new ReadableStream<Uint8Array>({ start(controller) { for (const byte of input) controller.enqueue(new Uint8Array([byte])); controller.close() } })
    const result: unknown[] = []
    await readSSE(stream, event => result.push({ event: event.event, payload: JSON.parse(event.data) }))
    expect(result).toEqual([{ event: 'delta', payload: { text: '复习资料' } }, { event: 'done', payload: { content: '完成' } }])
  })
  it('stops and cancels the reader when the receiver reports an error', async () => {
    let cancelled = false
    const stream = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new TextEncoder().encode('event: error\ndata: {"message":"失败"}\n\n')) }, cancel() { cancelled = true } })
    await expect(readSSE(stream, () => { throw new Error('失败') })).rejects.toThrow('失败')
    expect(cancelled).toBe(true)
  })
  it('flushes a final event and supports bare CR', async () => {
    const stream = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new TextEncoder().encode('event: delta\rdata: 末尾')); c.close() } })
    const events: unknown[] = []
    await readSSE(stream, event => events.push(event))
    expect(events).toEqual([{ event: 'delta', data: '末尾' }])
  })
})

describe('safe source rendering', () => {
  it('renders verified references only and escapes raw HTML', () => {
    const source = { chunk_id: 'abc-1', source_id: 'S1' } as Source
    const html = renderMarkdown('<script>alert(1)</script> [S1] [Sfake] [x](javascript:alert(1))', [source])
    expect(html).not.toContain('<script>')
    expect(html).toContain('data-source-id="abc-1"')
    expect(html).toContain('[Sfake]')
    expect(html).not.toContain('href="javascript:')
  })
  it('does not load remote images embedded in generated Markdown', () => {
    expect(renderMarkdown('![课程图片](https://example.com/track.png)')).not.toContain('<img')
  })
})
