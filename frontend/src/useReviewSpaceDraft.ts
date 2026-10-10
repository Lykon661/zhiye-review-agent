import { ref, type Ref } from 'vue'
import { api, ApiError } from './api'
import { createDraftPersistence } from './draftPersistence'
import type { ReviewSpace, ReviewSpaceDraft } from './types'

interface Fields {
  current: Ref<ReviewSpace | null>
  selected: Ref<string[]>
  ownAnswer: Ref<string>
  usedHint: Ref<boolean>
  seenAnswer: Ref<boolean>
}
export function useReviewSpaceDraft(fields: Fields) {
  const status = ref(''), error = ref('')
  let revision = 0, restoring = false
  const scope = (value: ReviewSpace) => `${value.id}:${value.step?.id || ''}`
  const storage = createDraftPersistence<ReviewSpaceDraft>({
    key: id => `review-space-draft:${id}`,
    decode(value, id) {
      if (!value || typeof value !== 'object') return null
      const item = value as { scope: string; revision: number; value: ReviewSpaceDraft }
      const draft = item.value
      if (item.scope !== id || !draft || !Array.isArray(draft.selected) || !draft.selected.every(v => typeof v === 'string')
        || typeof draft.own_answer !== 'string' || draft.own_answer.length > 4000
        || typeof draft.used_hint !== 'boolean' || typeof draft.seen_answer !== 'boolean'
        || typeof draft.step_id !== 'string' || item.revision !== draft.draft_revision) return null
      return item
    },
    async send(item, keepalive) {
      const id = item.scope.split(':')[0]
      const result = await api<{ draft: ReviewSpaceDraft }>(`/review-spaces/${id}/draft`, {
        method: 'PUT', body: JSON.stringify(item.value), keepalive,
      })
      const saved = result.draft, draft = item.value
      if (saved.draft_revision !== draft.draft_revision || JSON.stringify(saved.selected) !== JSON.stringify(draft.selected)
        || saved.own_answer !== draft.own_answer || saved.used_hint !== draft.used_hint || saved.seen_answer !== draft.seen_answer) {
        throw new ApiError('作答已在另一处更新，请刷新后继续。', 409)
      }
    },
    retry: reason => !(reason instanceof ApiError) || reason.status >= 500 || reason.status === 429,
    state(item, state, reason) {
      if (!fields.current.value || scope(fields.current.value) !== item.scope || item.revision !== revision) return
      status.value = state === 'saved' ? '已保存' : state === 'saving' ? '保存中…' : '尚未同步'
      error.value = state === 'failed' ? (reason instanceof Error ? reason.message : String(reason)) : ''
    },
  })
  function restore(value: ReviewSpace) {
    const id = scope(value), local = storage.read(id)
    const useLocal = ['waiting_answer', 'paused'].includes(value.status) && local !== null && local.value.step_id === value.step?.id
      && local.revision > (value.draft.draft_revision || 0)
    const draft = useLocal ? local.value : value.draft
    restoring = true
    try {
      fields.selected.value = [...(draft.selected || [])]
      fields.ownAnswer.value = draft.own_answer || ''
      fields.usedHint.value = !!draft.used_hint; fields.seenAnswer.value = !!draft.seen_answer
      revision = draft.draft_revision || 0
      status.value = ''; error.value = ''
    } finally { restoring = false }
    if (useLocal && value.status === 'waiting_answer') { storage.stage(id, revision, draft); void storage.flush(id) }
    else if (!useLocal) storage.forget(id)
  }
  function changed() {
    const value = fields.current.value
    if (restoring || value?.status !== 'waiting_answer' || !value.step) return
    revision = Math.max(revision + 1, Date.now() * 1000)
    storage.stage(scope(value), revision, { step_id: value.step.id, selected: [...fields.selected.value],
      own_answer: fields.ownAnswer.value, used_hint: fields.usedHint.value, seen_answer: fields.seenAnswer.value, draft_revision: revision })
  }
  function settled(previous: ReviewSpace | null, next: ReviewSpace) {
    if (previous?.step && previous.id === next.id && (previous.step.id !== next.step?.id || ['feedback', 'completed'].includes(next.status))) {
      storage.forget(scope(previous))
    }
  }
  return { status, error, restore, changed, settled, flush: () => storage.flush(), dispose: storage.dispose }
}
