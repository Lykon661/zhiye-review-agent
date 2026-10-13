import { ref } from 'vue'
import { api, ApiError } from './api'
import { createDraftPersistence, type DraftSnapshot } from './draftPersistence'
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

export function usePracticeProgress() {
  const saveStatus = ref('')
  let documentId = '', signature = '', questionIds = '', revision = 0
  const storage = createDraftPersistence<Snapshot>({
    key: id => `review-practice-draft:${id}`,
    decode(value, scope) {
      if (!value || typeof value !== 'object') return null
      const envelope = value as DraftSnapshot<Snapshot>
      const payload = envelope.value || value as Snapshot
      if (typeof payload.signature !== 'string' || !payload.selections || !payload.drafts) return null
      // Accept journals written before the shared envelope was introduced.
      return { scope, revision: payload.revision, value: payload }
    },
    async send(item, keepalive) {
      const result = await api<{ revision: number }>(`/documents/${item.scope}/practice/progress`, {
        method: 'PUT', body: JSON.stringify(item.value), keepalive,
      })
      if (result.revision > item.revision) throw new ApiError('另一页面已更新作答，本地草稿已保留。修改后将保存本页作答。', 409)
      if (result.revision !== item.revision) throw new Error('进度尚未同步')
    },
    retry: reason => !(reason instanceof ApiError) || reason.status >= 500 || reason.status === 429,
    state(item, state, reason) {
      if (documentId !== item.scope || signature !== item.value.signature || revision !== item.revision) return
      saveStatus.value = state === 'saved' ? '进度已保存' : state === 'saving' ? '自动保存中…'
        : reason instanceof ApiError && reason.status === 409 ? reason.message : '进度尚未同步，恢复连接后自动重试'
    },
  })
  function flush(keepalive = false) { return storage.flush(undefined, keepalive) }
  function reset() {
    void flush()
    documentId = ''; signature = ''; questionIds = ''; saveStatus.value = ''
  }
  function ready(id: string, questions: PracticeQuestion[]) {
    return documentId === id && questionIds === questions.map(q => q.id).join('|') && !!signature
  }
  async function restore(id: string, questions: PracticeQuestion[], active: () => boolean) {
    const saved = await api<SavedProgress>(`/documents/${id}/practice/progress`)
    if (!active()) return null
    // Include already-acknowledged edits: a delayed GET may predate their PUT.
    const local = storage.read(id)?.value
    const pending = storage.read(id, true)?.value
    const newer = local && local.signature === saved.signature && local.revision > saved.revision ? local : null
    const conflicted = pending && pending.signature === saved.signature && pending.revision <= saved.revision
      && (pending.current_question_id !== (saved.state.current_question_id ?? null)
        || JSON.stringify(pending.selections) !== JSON.stringify(saved.state.selections || {})
        || JSON.stringify(pending.drafts) !== JSON.stringify(saved.state.drafts || {})) ? pending : null
    const state = conflicted || newer || saved.state
    const ids = new Set(questions.map(q => q.id))
    const selections = Object.fromEntries(Object.entries(state.selections || {}).filter(([qid]) => ids.has(qid)))
    const drafts = Object.fromEntries(Object.entries(state.drafts || {}).filter(([qid]) => ids.has(qid)))
    const results = Object.fromEntries(Object.entries(saved.results).map(([qid, result]) => [qid, normalizePracticeAnswer(result)] as const).filter(([qid, result]) => ids.has(qid)
      && (result.selected === null || [...result.selected].sort().join('|') === [...(selections[qid] || [])].sort().join('|'))))
    documentId = id; signature = saved.signature; questionIds = questions.map(q => q.id).join('|')
    revision = Math.max(saved.revision, newer?.revision || 0)
    saveStatus.value = saved.updated_at ? '进度已保存' : '作答自动保存'
    if (conflicted) saveStatus.value = '另一页面已更新作答，本地草稿已保留。修改后将保存本页作答。'
    else if (newer) { storage.stage(id, newer.revision, newer); void flush() }
    else storage.forget(id)
    return { selections, drafts, results, index: Math.max(0, questions.findIndex(q => q.id === state.current_question_id)) }
  }
  function changed(state: DraftState) {
    if (!signature || !documentId) return
    revision = Math.max(revision + 1, Date.now() * 1000)
    storage.stage(documentId, revision, { ...state, signature, revision })
  }
  return { saveStatus, ready, restore, changed, reset, flush, dispose: storage.dispose }
}
