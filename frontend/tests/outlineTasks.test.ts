import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { api } from '../src/api'
import { useOutlineTasks } from '../src/useOutlineTasks'
import type { Job, Outline } from '../src/types'

vi.mock('../src/api', () => ({ api: vi.fn(), downloadOutline: vi.fn() }))
const request = vi.mocked(api)
beforeEach(() => { request.mockReset(); vi.stubGlobal('localStorage', { setItem: vi.fn() }) })
afterEach(() => vi.unstubAllGlobals())
function workspace() {
  const courseId = ref('course'), error = vi.fn()
  return { courseId, error, tasks: useOutlineTasks({ courseId, managementRevision: () => 0,
    blocked: () => false, beforeSelect: vi.fn(), clearFeedback: vi.fn(), error }) }
}

it('keeps a failed new task visible while reading an old completed note and retries that task', async () => {
  const { tasks } = workspace()
  tasks.runningJob.value = { id: 'new-job', outline_id: 'new-note', status: 'running', progress: 20 } as Job
  tasks.job.value = { id: 'old-job', outline_id: 'old-note', status: 'completed' } as Job
  tasks.outline.value = { id: 'old-note', status: 'completed' } as Outline
  tasks.selectedOutlineId.value = 'old-note'
  request.mockResolvedValueOnce({ id: 'new-job', outline_id: 'new-note', status: 'failed', error: 'timeout' })
    .mockResolvedValueOnce([{ id: 'new-note', status: 'failed', job_id: 'new-job' }, { id: 'old-note', status: 'completed', job_id: 'old-job' }])
  await tasks.pollTasks()
  expect(tasks.activeJob.value).toBe(false)
  expect(tasks.displayedJob.value?.error).toBe('timeout')
  expect(tasks.outline.value?.id).toBe('old-note')
  request.mockResolvedValueOnce({ job_id: 'retry-job', outline_id: 'new-note' })
    .mockResolvedValueOnce([{ id: 'new-note', status: 'queued', job_id: 'retry-job' }])
    .mockResolvedValueOnce({ id: 'new-note', job_id: 'retry-job', status: 'queued' })
    .mockResolvedValueOnce({ id: 'retry-job', outline_id: 'new-note', status: 'queued' })
  await tasks.retryOutline()
  expect(request).toHaveBeenCalledWith('/jobs/new-job/retry', { method: 'POST' })
  expect(tasks.selectedOutlineId.value).toBe('new-note')
})

it('opens a note generated after the remembered one, but keeps a deliberate older choice', async () => {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => { store.set(key, value) } })
  const { tasks } = workspace()
  const note = (id: string) => ({ id, status: 'completed', job_id: `${id}-job` }) as Outline
  request.mockImplementation(async (path: string) => path.startsWith('/jobs/')
    ? { id: path.slice(6), status: 'completed' } : note(path.slice(10)))
  // A plain id saved before this change opens the newest note.
  store.set('review-note-outline:course', 'old-note')
  tasks.outlines.value = [note('new-note'), note('old-note')]
  await tasks.restoreSelection()
  expect(tasks.outline.value?.id).toBe('new-note')
  // An older note chosen on purpose survives a reload while nothing newer exists.
  await tasks.selectOutline('old-note')
  await tasks.restoreSelection()
  expect(tasks.outline.value?.id).toBe('old-note')
  // A note generated afterwards takes precedence over the remembered older one.
  tasks.outlines.value = [note('newest-note'), note('new-note'), note('old-note')]
  await tasks.restoreSelection()
  expect(tasks.outline.value?.id).toBe('newest-note')
})

it('ignores a late task response after switching courses', async () => {
  const { tasks, courseId } = workspace()
  let complete!: (value: Job) => void
  request.mockReturnValueOnce(new Promise<Job>(resolve => { complete = resolve }))
  tasks.runningJob.value = { id: 'old-job', outline_id: 'old-note', status: 'running' } as Job
  const polling = tasks.pollTasks()
  courseId.value = 'new-course'; tasks.reset()
  complete({ id: 'old-job', outline_id: 'old-note', status: 'failed' } as Job)
  await polling
  expect(tasks.runningJob.value).toBeNull()
  expect(tasks.outlines.value).toEqual([])
})
