import type { SSEEvent } from './sse'
import { readSSE } from './sse'
import type { NoteChatContext, PracticeChatContext } from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

async function errorMessage(response: Response): Promise<string> {
  const text = await response.text()
  try {
    const payload = JSON.parse(text)
    if (typeof payload.detail === 'string') return payload.detail
    if (Array.isArray(payload.detail)) return payload.detail.map((item: { msg: string }) => item.msg).join('；')
    return payload.message || `请求失败（${response.status}）`
  } catch { return text.slice(0, 300) || `请求失败（${response.status}）` }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  let response: Response
  try { response = await fetch(`${API_BASE}${path}`, { ...options, headers }) }
  catch (error) {
    if (['AbortError', 'TimeoutError'].includes((error as Error).name)) throw error
    throw new Error('无法连接后端。请确认后端已启动，并检查 API 地址。')
  }
  if (!response.ok) throw new ApiError(await errorMessage(response), response.status)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function chatStream(sessionId: string, message: string, signal: AbortSignal, receive: (event: SSEEvent) => void, context?: PracticeChatContext | null, noteContext?: NoteChatContext | null): Promise<void> {
  const response = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/chat`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, practice_question_id: context?.wrong_question_id ? undefined : context?.question_id,
      wrong_question_id: context?.wrong_question_id, selected: context?.selected, own_answer: context?.own_answer,
      note_context: noteContext ? { outline_id: noteContext.outline_id, selected_text: noteContext.selected_text,
        source_text: noteContext.source_text, start_line: noteContext.start_line, end_line: noteContext.end_line } : undefined }), signal,
  })
  if (!response.ok) throw new Error(await errorMessage(response))
  if (!response.body) throw new Error('服务未返回可读取的回答流。')
  if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new Error('服务未返回 SSE 回答流。')
  await readSSE(response.body, receive)
}

export async function downloadOutline(id: string): Promise<void> {
  const response = await fetch(`${API_BASE}/outlines/${encodeURIComponent(id)}/export`)
  if (!response.ok) throw new Error(await errorMessage(response))
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = '复习笔记.md'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
