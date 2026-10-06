import { ref } from 'vue'
import { api } from './api'
import type { PracticeAnswer, PracticeQuestion } from './types'
import { normalizePracticeAnswer } from './practiceAnswers'

interface DraftState {
  current_question_id: string | null
  selections: Record<string, string[]>
  drafts: Record<string, string>
}
interface Snapshot extends DraftState { signature: string; revision: number }
interface SavedProgress {
  signature: string; revision: number; state: Partial<DraftState>
  results: Record<string, PracticeAnswer>; updated_at: string | null
}

// The browser journal preserves the last keystroke even if a refresh interrupts
// the debounced database request. Progress is also saved to the local database.
export function usePracticeProgress() {
  const saveStatus = ref('')
  let documentId = '', signature = '', questionIds = '', revision = 0
  let pending: { id: string; payload: Snapshot } | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  const key = (id: string) => `review-practice-draft:${id}`
  function journal(id: string): Snapshot | null {
    try { return JSON.parse(localStorage.getItem(key(id)) || 'null') as Snapshot | null }
    catch { return null }
  }
  async function send(item: { id: string; payload: Snapshot }, keepalive = false) {
    try {
      const value = await api<{ revision: number }>(`/documents/${item.id}/practice/progress`, {
        method: 'PUT', body: JSON.stringify(item.payload), keepalive,
      })
      if (journal(item.id)?.revision === item.payload.revision && value.revision >= item.payload.revision) {
        try { localStorage.removeItem(key(item.id)) } catch { /* database copy is saved */ }
      }
      if (pending?.id === item.id && pending.payload.revision === item.payload.revision) pending = null
      if (documentId === item.id && signature === item.payload.signature && revision === item.payload.revision) saveStatus.value = '进度已保存'
      return true
    } catch {
      if (documentId === item.id && signature === item.payload.signature && revision === item.payload.revision) saveStatus.value = '进度尚未同步，恢复连接后自动重试'
      if (!disposed && pending?.id === item.id && pending.payload.revision === item.payload.revision) {
        if (retryTimer) clearTimeout(retryTimer)
        retryTimer = setTimeout(() => { retryTimer = undefined; if (pending) void send(pending) }, 5000)
      }
      return false
    }
  }
  function flush(keepalive = false) {
    if (timer) clearTimeout(timer)
    timer = undefined
    return pending ? send(pending, keepalive) : Promise.resolve(true)
  }
  function reset() {
    flush()
    documentId = ''; signature = ''; questionIds = ''; pending = null; saveStatus.value = ''
    if (retryTimer) clearTimeout(retryTimer)
  }
  function ready(id: string, questions: PracticeQuestion[]) {
    return documentId === id && questionIds === questions.map(q => q.id).join('|') && !!signature
  }
  async function restore(id: string, questions: PracticeQuestion[], active: () => boolean) {
    const saved = await api<SavedProgress>(`/documents/${id}/practice/progress`)
    if (!active()) return null
    const local = journal(id)
    const newer = local && local.signature === saved.signature && local.revision > saved.revision ? local : null
    const state = newer || saved.state
    const ids = new Set(questions.map(q => q.id))
    const selections = Object.fromEntries(Object.entries(state.selections || {}).filter(([qid]) => ids.has(qid)))
    const drafts = Object.fromEntries(Object.entries(state.drafts || {}).filter(([qid]) => ids.has(qid)))
    const results = Object.fromEntries(Object.entries(saved.results).map(([qid, result]) => [qid, normalizePracticeAnswer(result)] as const).filter(([qid, result]) => ids.has(qid)
      && (result.selected === null || [...result.selected].sort().join('|') === [...(selections[qid] || [])].sort().join('|'))))
    documentId = id; signature = saved.signature; questionIds = questions.map(q => q.id).join('|')
    revision = Math.max(saved.revision, newer?.revision || 0)
    saveStatus.value = saved.updated_at ? '进度已保存' : '作答自动保存'
    if (newer) { pending = { id, payload: newer }; flush() }
    else {
      pending = null
      try { localStorage.removeItem(key(id)) } catch { /* optional browser journal */ }
    }
    return { selections, drafts, results, index: Math.max(0, questions.findIndex(q => q.id === state.current_question_id)) }
  }
  function changed(state: DraftState) {
    if (!signature || !documentId) return
    revision = Math.max(revision + 1, Date.now() * 1000)
    const payload: Snapshot = JSON.parse(JSON.stringify({ ...state, signature, revision }))
    pending = { id: documentId, payload }
    try { localStorage.setItem(key(documentId), JSON.stringify(payload)); saveStatus.value = '自动保存中…' }
    catch { saveStatus.value = '自动保存中…' }
    if (timer) clearTimeout(timer)
    const item = pending
    timer = setTimeout(() => { timer = undefined; void send(item) }, 350)
  }
  function dispose() {
    disposed = true; flush()
    if (retryTimer) clearTimeout(retryTimer)
  }
  return { saveStatus, ready, restore, changed, reset, flush, dispose }
}
