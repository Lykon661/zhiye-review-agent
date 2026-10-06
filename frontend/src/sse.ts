export interface SSEEvent { event: string; data: string }

/** Streaming UTF-8 decoder; accepts CR, LF, CRLF and multi-line SSE data. */
export async function readSSE(stream: ReadableStream<Uint8Array>, receive: (event: SSEEvent) => void): Promise<void> {
  const reader = stream.getReader()
  const decoder = new TextDecoder('utf-8')
  let pending = ''
  let eventName = ''
  let data: string[] = []
  const line = (value: string) => {
    if (value === '') {
      if (data.length) receive({ event: eventName || 'message', data: data.join('\n') })
      eventName = ''
      data = []
      return
    }
    if (value.startsWith(':')) return
    const colon = value.indexOf(':')
    const field = colon < 0 ? value : value.slice(0, colon)
    let content = colon < 0 ? '' : value.slice(colon + 1)
    if (content.startsWith(' ')) content = content.slice(1)
    if (field === 'event') eventName = content
    if (field === 'data') data.push(content)
  }
  const drain = (final = false) => {
    let index = 0
    while (index < pending.length) {
      const character = pending[index]
      if (character !== '\n' && character !== '\r') { index += 1; continue }
      if (character === '\r' && index === pending.length - 1 && !final) break
      line(pending.slice(0, index))
      const width = character === '\r' && pending[index + 1] === '\n' ? 2 : 1
      pending = pending.slice(index + width)
      index = 0
    }
    if (final && pending) { line(pending); pending = '' }
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      pending += decoder.decode(value, { stream: true })
      drain()
    }
    pending += decoder.decode()
    drain(true)
    // EOF dispatch supports providers omitting the final blank line.
    if (data.length) line('')
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
