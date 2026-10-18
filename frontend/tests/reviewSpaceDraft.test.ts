import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ref, watch } from 'vue'
import { api } from '../src/api'
import { useReviewSpaceDraft } from '../src/useReviewSpaceDraft'
import type { ReviewSpace } from '../src/types'

vi.mock('../src/api', () => ({ api: vi.fn(), ApiError: class ApiError extends Error { constructor(message: string, public status: number) { super(message) } } }))
const request = vi.mocked(api)
let values: Map<string, string>
const adapters: ReturnType<typeof useReviewSpaceDraft>[] = []
beforeEach(() => {
  vi.useFakeTimers()
  values = new Map()
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) } })
  vi.stubGlobal('window', new EventTarget())
  request.mockReset()
  request.mockImplementation(async (_path, options) => ({ draft: JSON.parse(options!.body as string) }))
})
afterEach(async () => {
  for (const adapter of adapters.splice(0)) adapter.dispose()
  await Promise.resolve(); vi.useRealTimers(); vi.unstubAllGlobals()
})
function setup() {
  const value = { id: 'space', status: 'waiting_answer', step: { id: 'step' }, draft: {} } as ReviewSpace
  const fields = { current: ref<ReviewSpace | null>(value), selected: ref<string[]>([]),
    ownAnswer: ref(''), usedHint: ref(false), seenAnswer: ref(false) }
  const adapter = useReviewSpaceDraft(fields)
  adapters.push(adapter)
  return { fields, adapter, value }
}

it('restores unsynchronized input from a previous page and synchronizes it', async () => {
  const { fields, adapter, value } = setup()
  const payload = { step_id: 'step', selected: ['A'], own_answer: '未同步的作答',
    used_hint: true, seen_answer: false, draft_revision: 20 }
  values.set('review-space-draft:space:step', JSON.stringify({ scope: 'space:step', revision: 20, value: payload }))
  adapter.restore(value)
  expect(fields.ownAnswer.value).toBe('未同步的作答')
  expect(fields.selected.value).toEqual(['A'])
  await adapter.flush()
  expect(request).toHaveBeenCalledWith('/review-spaces/space/draft',
    expect.objectContaining({ method: 'PUT', body: JSON.stringify(payload) }))
})

it('journals edited input immediately and does not replace it with an old server draft', async () => {
  const { fields, adapter, value } = setup()
  adapter.restore(value)
  fields.ownAnswer.value = '最新文字'
  adapter.changed()
  expect(JSON.parse(values.get('review-space-draft:space:step')!).value.own_answer).toBe('最新文字')
  await vi.advanceTimersByTimeAsync(350)
  adapter.restore(value)
  expect(fields.ownAnswer.value).toBe('最新文字')
  await adapter.flush()
})

it('restoring fields cannot create a new revision through synchronous watchers', () => {
  const { fields, adapter, value } = setup()
  const stop = watch([fields.selected, fields.ownAnswer, fields.usedHint, fields.seenAnswer],
    adapter.changed, { deep: true, flush: 'sync' })
  try {
    adapter.restore({ ...value, draft: { selected: ['B'], own_answer: 'saved', draft_revision: 12 } })
    expect(fields.ownAnswer.value).toBe('saved')
    expect(request).not.toHaveBeenCalled()
    expect(values.size).toBe(0)
  } finally { stop() }
})
