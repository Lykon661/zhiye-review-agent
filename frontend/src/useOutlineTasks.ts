import { computed, ref, type Ref } from 'vue'
import { api, downloadOutline } from './api'
import type { Job, Outline } from './types'

interface Options {
  courseId: Ref<string>
  managementRevision: () => number
  blocked: () => boolean
  beforeSelect: () => void
  clearFeedback: () => void
  error: (reason: unknown) => void
}
const active = (status: string) => ['queued', 'running'].includes(status)
export function useOutlineTasks(options: Options) {
  const outlines = ref<Outline[]>([]), outline = ref<Outline | null>(null)
  const selectedOutlineId = ref(''), job = ref<Job | null>(null), runningJob = ref<Job | null>(null)
  const outlineLoading = ref(false), outlineBusy = ref(false)
  let epoch = 0, selection = 0
  const activeJob = computed(() => !!(runningJob.value && active(runningJob.value.status)) || outlines.value.some(item => active(item.status)))
  // A failed new task remains visible even while an older note is being read.
  const displayedJob = computed(() => runningJob.value && (active(runningJob.value.status) || ['failed', 'interrupted'].includes(runningJob.value.status))
    ? runningJob.value : job.value ?? runningJob.value)
  function current(checkManagement = false) {
    const course = options.courseId.value, version = epoch, management = options.managementRevision()
    return () => options.courseId.value === course && epoch === version && (!checkManagement || options.managementRevision() === management)
  }
  function reset() {
    ++epoch; ++selection
    outlines.value = []; outline.value = null; selectedOutlineId.value = ''
    job.value = null; runningJob.value = null; outlineLoading.value = false; outlineBusy.value = false
  }
  async function selectOutline(id: string) {
    options.beforeSelect()
    const valid = current(), token = ++selection
    selectedOutlineId.value = id; outlineLoading.value = true
    try {
      const result = await api<Outline>(`/outlines/${id}`)
      if (!valid() || token !== selection) return
      const resultJob = await api<Job>(`/jobs/${result.job_id}`)
      if (!valid() || token !== selection) return
      outline.value = result; job.value = resultJob
      // Also note the newest note, so one generated later is not hidden behind this choice.
      try { localStorage.setItem(`review-note-outline:${options.courseId.value}`, JSON.stringify({ id, newest: outlines.value[0]?.id ?? id })) } catch { /* optional history */ }
    } catch (reason) { if (valid() && token === selection) options.error(reason) }
    finally { if (valid() && token === selection) outlineLoading.value = false }
  }
  async function restoreSelection() {
    let saved: { id?: string; newest?: string } | null = null
    try { saved = JSON.parse(localStorage.getItem(`review-note-outline:${options.courseId.value}`) || 'null') } catch { /* Legacy or unavailable history opens the newest note. */ }
    const newest = outlines.value[0]?.id || ''
    const kept = saved?.newest === newest && outlines.value.some(item => item.id === saved?.id)
    selectedOutlineId.value = kept && saved?.id ? saved.id : newest
    if (selectedOutlineId.value) await selectOutline(selectedOutlineId.value)
  }
  async function loadLatestJob() {
    const latest = outlines.value.find(item => active(item.status)) || outlines.value[0]
    if (!latest?.job_id) return
    const valid = current()
    const result = await api<Job>(`/jobs/${latest.job_id}`)
    if (valid()) runningJob.value = result
  }
  async function start(retry: boolean) {
    const target = displayedJob.value
    if (!options.courseId.value || outlineBusy.value || activeJob.value || options.blocked() || (retry && !target)) return
    const course = options.courseId.value, valid = current(true)
    options.clearFeedback(); outlineBusy.value = true
    try {
      const result = await api<{ job_id: string; outline_id: string }>(retry ? `/jobs/${target!.id}/retry` : `/courses/${course}/outlines`, { method: 'POST' })
      if (!valid()) return
      runningJob.value = { id: result.job_id, outline_id: result.outline_id, status: 'queued', progress: 0, stage: '等待生成' }
      const list = await api<Outline[]>(`/courses/${course}/outlines`)
      if (!valid()) return
      outlines.value = list
      await selectOutline(result.outline_id)
    } catch (reason) { if (valid()) options.error(reason) }
    finally { if (valid()) outlineBusy.value = false }
  }
  async function removeOutline(id: string) {
    const valid = current(true)
    await api(`/outlines/${id}`, { method: 'DELETE' })
    if (!valid()) return
    outlines.value = outlines.value.filter(item => item.id !== id)
    if (runningJob.value?.outline_id === id) runningJob.value = null
    if (selectedOutlineId.value === id) {
      ++selection; selectedOutlineId.value = ''; outline.value = null; job.value = null; outlineLoading.value = false
      if (outlines.value[0]) await selectOutline(outlines.value[0].id)
    }
  }
  async function pollTasks() {
    const course = options.courseId.value, valid = current(true)
    const waiting = outlines.value.find(item => active(item.status))
    const id = runningJob.value && active(runningJob.value.status) ? runningJob.value.id : waiting?.job_id
    if (!course || !id) return
    const result = await api<Job>(`/jobs/${id}`)
    if (!valid()) return
    runningJob.value = result
    if (outline.value?.id === result.outline_id) job.value = result
    if (!active(result.status)) {
      const list = await api<Outline[]>(`/courses/${course}/outlines`)
      if (!valid()) return
      outlines.value = list
      // Refresh only the note belonging to this task; don't disturb old reading.
      if (selectedOutlineId.value === result.outline_id) await selectOutline(result.outline_id)
    }
  }
  async function exportOutline() {
    if (outline.value) { try { await downloadOutline(outline.value.id) } catch (reason) { options.error(reason) } }
  }
  return { outlines, outline, selectedOutlineId, job, runningJob, outlineLoading, outlineBusy, activeJob, displayedJob,
    reset, selectOutline, restoreSelection, loadLatestJob, createOutline: () => start(false), retryOutline: () => start(true), removeOutline, pollTasks, exportOutline }
}
