import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { api } from '../src/api'
import { usePracticeProgress } from '../src/usePracticeProgress'
import type { PracticeQuestion } from '../src/types'

vi.mock('../src/api', async importOriginal => ({ ...await importOriginal<typeof import('../src/api')>(), api: vi.fn() }))
const request = vi.mocked(api)
let values: Map<string, string>
let progress: ReturnType<typeof usePracticeProgress>
const questions = [{ id: 'question' }] as PracticeQuestion[]
const saved = { signature: 'same-paper', revision: 1, state: { drafts: { question: 'old' } }, results: {}, updated_at: 'saved' }

beforeEach(() => {
  vi.useFakeTimers()
  values = new Map()
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) }, removeItem: (key: string) => { values.delete(key) } })
  vi.stubGlobal('window', new EventTarget())
  request.mockReset()
  progress = usePracticeProgress()
})
afterEach(async () => {
  progress.dispose(); await Promise.resolve()
  vi.useRealTimers(); vi.unstubAllGlobals()
})

it('a delayed GET cannot restore text older than an acknowledged edit', async () => {
  request.mockResolvedValueOnce(saved)
  await progress.restore('paper', questions, () => true)
  let returnOld!: (value: typeof saved) => void
  const late = new Promise<typeof saved>(resolve => { returnOld = resolve })
  request.mockReturnValueOnce(late)
  const restoring = progress.restore('paper', questions, () => true)
  request.mockImplementation(async (_path, options) => ({ revision: JSON.parse(options!.body as string).revision }))
  progress.changed({ current_question_id: 'question', selections: { question: ['A'] }, drafts: { question: 'latest text' } })
  await vi.advanceTimersByTimeAsync(350)
  expect(values.size).toBe(0)
  returnOld(saved)
  const restored = await restoring
  expect(restored?.drafts.question).toBe('latest text')
  expect(restored?.selections.question).toEqual(['A'])
})

it('restores an existing journal written by the previous implementation', async () => {
  values.set('review-practice-draft:paper', JSON.stringify({ signature: 'same-paper', revision: 2,
    current_question_id: 'question', selections: { question: ['B'] }, drafts: { question: 'legacy draft' } }))
  request.mockResolvedValueOnce(saved).mockResolvedValue({ revision: 2 })
  const restored = await progress.restore('paper', questions, () => true)
  expect(restored?.drafts.question).toBe('legacy draft')
  expect(restored?.selections.question).toEqual(['B'])
})

it('keeps a rejected draft and does not retry a version conflict', async () => {
  request.mockResolvedValueOnce(saved)
  await progress.restore('paper', questions, () => true)
  let serverRevision = 0
  request.mockImplementation(async (_path, options) => {
    const payload = JSON.parse(options!.body as string)
    serverRevision = payload.revision + 1
    return { revision: serverRevision }
  })
  progress.changed({ current_question_id: 'question', selections: { question: ['B'] }, drafts: { question: 'local answer' } })
  await vi.advanceTimersByTimeAsync(350)
  expect(values.has('review-practice-draft:paper')).toBe(true)
  expect(progress.saveStatus.value).toContain('本地草稿已保留')
  const calls = request.mock.calls.length
  await vi.advanceTimersByTimeAsync(5000)
  expect(request).toHaveBeenCalledTimes(calls)
  request.mockResolvedValueOnce({ ...saved, revision: serverRevision })
  const restored = await progress.restore('paper', questions, () => true)
  expect(restored?.drafts.question).toBe('local answer')
  expect(values.has('review-practice-draft:paper')).toBe(true)
})
