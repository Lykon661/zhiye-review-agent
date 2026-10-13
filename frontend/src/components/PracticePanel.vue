<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { Chapter, Document, PracticeAnswer, PracticeAnswerPairing, PracticeChatContext, PracticeQuestion, PracticeState, PracticeTranslation } from '../types'
import MarkdownContent from './MarkdownContent.vue'
import ManagementDialog from './ManagementDialog.vue'
import PracticeGenerator from './PracticeGenerator.vue'
import { supportsUpload, uploadAccept, uploadFormatsLabel } from '../uploadFormats'
import { usePracticeProgress } from '../usePracticeProgress'
import { normalizePracticeAnswer } from '../practiceAnswers'

const props = defineProps<{ courseId: string; documents: Document[]; chapters: Chapter[]; modelConfigured: boolean; wrongRevision: number; deletionBusy: boolean; deletingDocumentId: string | null; uploadBlocked: boolean }>()
const emit = defineEmits<{ refresh: []; busy: [value: boolean]; modelBusy: [value: boolean]; wrongChange: []; deleteDocument: [document: Document]; renameDocument: [document: Document]; contextChange: [value: PracticeChatContext | null] }>()
const documentId = ref('')
const bank = ref<PracticeState | null>(null)
const loading = ref(false)
const uploading = ref(false)
const generationBusy = ref(false)
const generatedFiles = ref<Document[]>([])
const generatedLoaded = ref(false)
const generatedListError = ref('')
let generatedListSerial = 0
const generatedListController = new AbortController()
const pairingBusy = ref(false)
const pairingSelection = ref('auto')
const busyQuestion = ref('')
const translatingQuestion = ref('')
const markingQuestion = ref('')
const deletingQuestion = ref('')
const deleteTarget = ref<PracticeQuestion | null>(null)
const deleteError = ref('')
const renameTarget = ref<Document | null>(null)
const renameError = ref('')
const renaming = ref(false)
const translationVisible = ref<Record<string, boolean>>({})
const error = ref('')
const uploadYear = ref('')
const uploadKind = ref<'exercise' | 'answer'>('exercise')
const input = ref<HTMLInputElement | null>(null)
const index = ref(0)
const selections = ref<Record<string, string[]>>({})
const drafts = ref<Record<string, string>>({})
const results = ref<Record<string, PracticeAnswer>>({})
const visible = ref<Record<string, boolean>>({})
const explanationBusy = ref<Record<string, boolean>>({})
const explanationErrors = ref<Record<string, string>>({})
const explanationControllers = new Map<string, AbortController>()
const progress = usePracticeProgress()
const { saveStatus } = progress
let restoringProgress = false
watch([index, selections, drafts], () => {
  if (!restoringProgress) progress.changed({ current_question_id: bank.value?.questions[index.value]?.id || null,
    selections: selections.value, drafts: drafts.value })
}, { deep: true, flush: 'sync' })
const files = computed(() => [...generatedFiles.value, ...props.documents.filter(doc => doc.kind === 'exam' || doc.kind === 'exercise')])
const answerFiles = computed(() => props.documents.filter(doc => doc.id !== documentId.value
  && ['answer', 'exam', 'exercise'].includes(doc.kind)).sort((a, b) => Number(b.kind === 'answer') - Number(a.kind === 'answer')))
const savedPairingSelection = computed(() => bank.value?.answer_pairing?.mode === 'manual'
  ? bank.value.answer_pairing.answer_document_id || '' : bank.value?.answer_pairing?.mode || 'auto')
const pairingDirty = computed(() => pairingSelection.value !== savedPairingSelection.value)
const currentFile = computed(() => files.value.find(doc => doc.id === documentId.value))
function practiceName(document: Document) { return document.practice_title || document.filename }
const isGenerated = computed(() => currentFile.value?.kind === 'generated')
const current = computed(() => bank.value?.questions[index.value])
const chatContext = computed<PracticeChatContext | null>(() => {
  const q = current.value
  if (!q || !currentFile.value || currentFile.value.status !== 'ready' || bank.value?.status !== 'ready' || loading.value) return null
  return { question_id: q.id, filename: currentFile.value.filename, number: String(index.value + 1), original_number: q.number,
    stem: q.stem, options: q.options, selected: [...(selections.value[q.id] || [])], own_answer: drafts.value[q.id] || '' }
})
let contextActive = true
watch(chatContext, value => { if (contextActive) emit('contextChange', value) }, { immediate: true, deep: true })
onActivated(() => { contextActive = true; emit('contextChange', chatContext.value) })
onDeactivated(() => { contextActive = false; emit('contextChange', null) })
const hasEnglish = computed(() => current.value && /[A-Za-z]{2,}/.test(
  [current.value.stem, ...current.value.options.map(option => option.text)].join('\n'),
))
const translatedOptions = computed(() => Object.fromEntries(
  (current.value?.translation?.options || []).map(option => [option.key, option.text]),
))
const questionBusy = computed(() => pairingBusy.value || !!busyQuestion.value || !!translatingQuestion.value || !!markingQuestion.value || !!deletingQuestion.value)
const isChoice = computed(() => current.value && current.value.question_type !== 'other')
const currentResult = computed(() => current.value ? results.value[current.value.id] : undefined)
const answered = computed(() => bank.value?.questions.filter(q => results.value[q.id]?.correct !== null && results.value[q.id]?.correct !== undefined).length || 0)
const correct = computed(() => bank.value?.questions.filter(q => results.value[q.id]?.correct === true).length || 0)
const answersPreparing = computed(() => bank.value?.answer_preparation?.status === 'processing')
const working = computed(() => props.deletionBusy || !!renameTarget.value || renaming.value || generationBusy.value || uploading.value || pairingBusy.value || questionBusy.value || loading.value || answersPreparing.value || ['queued', 'processing'].includes(bank.value?.status || ''))
const modelBusy = computed(() => working.value || Object.values(explanationBusy.value).some(Boolean))
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let requestController: AbortController | undefined
let wrongSyncPending = false
let wrongSyncGeneration = 0

watch(working, value => {
  emit('busy', value)
  if (!value && wrongSyncPending) void syncWrongQuestions()
}, { immediate: true })
watch(modelBusy, value => emit('modelBusy', value), { immediate: true })
watch([() => files.value.map(doc => `${doc.id}:${doc.status}`).join('|'), generatedLoaded], () => {
  if (!generatedLoaded.value) return
  if (!files.value.some(doc => doc.id === documentId.value)) {
    let remembered = ''
    try { remembered = localStorage.getItem(`review-practice-document:${props.courseId}`) || '' } catch { /* optional preference */ }
    documentId.value = files.value.find(doc => doc.id === remembered)?.id || files.value.find(doc => doc.status === 'ready')?.id || files.value[0]?.id || ''
  }
  else if (currentFile.value?.status === 'ready' && !loading.value && !questionBusy.value && !['queued', 'processing'].includes(bank.value?.status || '')) void load()
}, { immediate: true })
watch(documentId, () => {
  deleteTarget.value = null; deleteError.value = ''
  renameTarget.value = null; renameError.value = ''
  progress.reset()
  pairingSelection.value = 'auto'
  try { if (documentId.value) localStorage.setItem(`review-practice-document:${props.courseId}`, documentId.value) } catch { /* optional preference */ }
  bank.value = null; index.value = 0; error.value = ''
  selections.value = {}; drafts.value = {}; results.value = {}; visible.value = {}
  translationVisible.value = {}
  void load()
}, { immediate: true })
watch(() => props.modelConfigured, value => { if (value && bank.value?.status === 'new') void prepare() })
watch(() => props.wrongRevision, () => {
  wrongSyncPending = true
  void syncWrongQuestions()
})
watch(() => props.courseId, () => {
  generatedLoaded.value = false; generatedFiles.value = []; documentId.value = ''
  void loadGeneratedSets()
}, { immediate: true })
watch(() => props.deletingDocumentId, (value, previous) => {
  if (previous && !value) void loadGeneratedSets()
})
async function loadGeneratedSets() {
  const course = props.courseId, serial = ++generatedListSerial
  generatedListError.value = ''
  try {
    const value = await api<Document[]>(`/courses/${course}/practice/generated`, { signal: generatedListController.signal })
    if (!disposed && serial === generatedListSerial && course === props.courseId) generatedFiles.value = value
  } catch (reason) {
    if (!disposed && serial === generatedListSerial && course === props.courseId) generatedListError.value = message(reason)
  } finally { if (!disposed && serial === generatedListSerial && course === props.courseId) generatedLoaded.value = true }
}
function useGeneratedSet(document: Document) {
  ++generatedListSerial
  generatedFiles.value = [document, ...generatedFiles.value.filter(item => item.id !== document.id)]
  generatedLoaded.value = true; generatedListError.value = ''
  documentId.value = document.id
}
async function refreshPractice() {
  await loadGeneratedSets()
  if (!disposed) await load()
}
async function syncWrongQuestions() {
  if (!documentId.value || bank.value?.status !== 'ready' || working.value) return
  const token = generation
  const syncToken = ++wrongSyncGeneration
  wrongSyncPending = false
  try {
    const value = await api<PracticeState>(`/documents/${documentId.value}/practice`, { signal: requestController?.signal })
    if (!active(token) || syncToken !== wrongSyncGeneration) return
    const saved = new Map(value.questions.map(q => [q.id, q.wrong_question_id]))
    for (const q of bank.value?.questions || []) q.wrong_question_id = saved.get(q.id) || null
  } catch (reason) { if (active(token) && syncToken === wrongSyncGeneration) error.value = message(reason) }
}

function cancel() {
  ++generation
  if (timer) clearTimeout(timer)
  requestController?.abort()
  for (const controller of explanationControllers.values()) controller.abort()
  explanationControllers.clear(); explanationBusy.value = {}; explanationErrors.value = {}
  translatingQuestion.value = ''
}
function active(token: number) { return !disposed && token === generation }
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason) }
function schedule(token: number) {
  if (active(token)) timer = setTimeout(() => { void read(token) }, 1800)
}
async function read(token: number, restoreProgress = false) {
  let restoring = false
  try {
    const value = await api<PracticeState>(`/documents/${documentId.value}/practice`, { signal: requestController?.signal })
    if (!active(token)) return
    const keepPairingSelection = pairingDirty.value && !restoreProgress
    restoring = value.status === 'ready' && (restoreProgress || !progress.ready(documentId.value, value.questions))
    // Do not expose editable questions until the first saved draft is restored.
    if (restoring) loading.value = true
    bank.value = value
    if (!keepPairingSelection) pairingSelection.value = savedPairingSelection.value
    if (restoring) {
      const saved = await progress.restore(documentId.value, value.questions, () => active(token))
      if (!saved || !active(token)) return
      restoringProgress = true
      try {
        selections.value = saved.selections; drafts.value = saved.drafts; index.value = saved.index
        results.value = saved.results
      } finally { restoringProgress = false }
    }
    progress.flush()
    if (['queued', 'processing'].includes(value.status)
        || ['processing', 'waiting'].includes(value.answer_preparation?.status || '')) schedule(token)
  } catch (reason) { if (active(token)) { error.value = message(reason); if (restoring) bank.value = null } }
  finally { if (restoring && active(token)) loading.value = false }
}
async function load() {
  cancel()
  const token = generation
  loading.value = false; error.value = ''; results.value = {}; visible.value = {}
  if (!documentId.value || currentFile.value?.status !== 'ready') return
  requestController = new AbortController()
  loading.value = true
  try {
    await read(token, true)
    if (active(token) && bank.value?.status === 'new') {
      results.value = {}; visible.value = {}; selections.value = {}; drafts.value = {}
      if (props.modelConfigured) await prepare()
    }
  } finally { if (active(token)) loading.value = false }
}
async function prepare() {
  if (!documentId.value || !props.modelConfigured) return
  cancel()
  const token = generation
  requestController = new AbortController()
  loading.value = true; error.value = ''
  try {
    await api(`/documents/${documentId.value}/practice`, { method: 'POST', signal: requestController.signal })
    if (active(token)) await read(token)
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) loading.value = false }
}
async function saveAnswerPairing() {
  if (!documentId.value || currentFile.value?.status !== 'ready' || modelBusy.value || !pairingDirty.value) return
  const token = generation
  const selected = pairingSelection.value
  const mode = selected === 'auto' || selected === 'none' ? selected : 'manual'
  pairingBusy.value = true; error.value = ''
  progress.flush()
  try {
    const pairing = await api<PracticeAnswerPairing>(`/documents/${documentId.value}/practice/answer-pairing`, {
      method: 'PUT', body: JSON.stringify({ mode, answer_document_id: mode === 'manual' ? selected : null }),
      signal: requestController?.signal,
    })
    if (!active(token)) return
    if (bank.value) bank.value.answer_pairing = pairing
    results.value = {}; visible.value = {}
    emit('wrongChange')
    await read(token, true)
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { pairingBusy.value = false }
}
function choose(question: PracticeQuestion, key: string) {
  if (questionBusy.value) return
  const value = selections.value[question.id] || []
  selections.value[question.id] = question.question_type === 'single_choice' ? [key] : value.includes(key) ? value.filter(label => label !== key) : [...value, key].sort()
  delete results.value[question.id]; visible.value[question.id] = false
  error.value = ''
}
function move(position: number) {
  index.value = position; error.value = ''
}
async function resolve(submit: boolean) {
  const question = current.value
  if (!question || questionBusy.value) return
  if (!submit && results.value[question.id]) { toggleAnswer(question.id); return }
  const token = generation
  busyQuestion.value = question.id; error.value = ''
  try {
    const value = await api<PracticeAnswer>(`/practice/questions/${question.id}/${submit ? 'submit' : 'answer'}`, {
      method: 'POST', body: submit ? JSON.stringify({ selected: selections.value[question.id] || [] }) : undefined,
      signal: requestController?.signal,
    })
    if (active(token)) {
      results.value[question.id] = normalizePracticeAnswer(value); visible.value[question.id] = true
      const displayed = bank.value?.questions.find(q => q.id === question.id)
      if (displayed) displayed.wrong_question_id = value.wrong_question_id
      if (value.wrong_question_id) emit('wrongChange')
      if (value.explanation_pending) void loadExplanation(question.id)
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { busyQuestion.value = '' }
}
function toggleAnswer(questionId: string) {
  visible.value[questionId] = !visible.value[questionId]
  if (visible.value[questionId] && results.value[questionId]?.explanation_pending) void loadExplanation(questionId)
}
async function loadExplanation(questionId: string) {
  if (!results.value[questionId]?.explanation_pending || explanationControllers.has(questionId)) return
  const token = generation
  const controller = new AbortController()
  explanationControllers.set(questionId, controller)
  explanationBusy.value[questionId] = true; delete explanationErrors.value[questionId]
  try {
    const value = await api<Pick<PracticeAnswer, 'answer' | 'choices' | 'origin' | 'source' | 'sources' | 'explanation_pending'>>(
      `/practice/questions/${questionId}/explanation`, { method: 'POST', signal: controller.signal },
    )
    const latest = results.value[questionId]
    if (active(token) && latest?.explanation_pending && latest.origin === value.origin
        && [...latest.choices].sort().join('|') === [...value.choices].sort().join('|')) {
      // A new submission may have different selected options. Keep its grade;
      // the explanation only fills in text for the same reference answer.
      results.value[questionId] = { ...latest, answer: value.answer, explanation_pending: value.explanation_pending,
        source: value.source, sources: value.sources }
      const displayed = bank.value?.questions.find(q => q.id === questionId)
      if (displayed?.wrong_question_id) emit('wrongChange')
    }
  } catch (reason) {
    if (active(token) && !controller.signal.aborted) explanationErrors.value[questionId] = message(reason)
  } finally {
    if (explanationControllers.get(questionId) === controller) {
      explanationControllers.delete(questionId)
      explanationBusy.value[questionId] = false
    }
  }
}
async function toggleWrong() {
  const question = current.value
  if (!question || questionBusy.value) return
  const token = generation
  markingQuestion.value = question.id; error.value = ''
  try {
    if (question.wrong_question_id) {
      await api(`/courses/${props.courseId}/wrong-questions/${question.wrong_question_id}`, {
        method: 'DELETE', signal: requestController?.signal,
      })
      if (active(token)) question.wrong_question_id = null
    } else {
      const value = await api<{ id: string }>(`/practice/questions/${question.id}/wrong`, {
        method: 'POST', body: JSON.stringify({ own_answer: drafts.value[question.id] ?? null,
                                             selected: selections.value[question.id] || [] }),
        signal: requestController?.signal,
      })
      if (active(token)) question.wrong_question_id = value.id
    }
    if (active(token)) emit('wrongChange')
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { markingQuestion.value = '' }
}
async function toggleTranslation() {
  const question = current.value
  if (!question || questionBusy.value) return
  if (question.translation) {
    translationVisible.value[question.id] = !translationVisible.value[question.id]
    return
  }
  if (!props.modelConfigured) return
  const token = generation
  translatingQuestion.value = question.id; error.value = ''
  try {
    const value = await api<PracticeTranslation>(`/practice/questions/${question.id}/translation`, {
      method: 'POST', signal: requestController?.signal,
    })
    if (active(token)) {
      question.translation = value
      translationVisible.value[question.id] = true
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) translatingQuestion.value = '' }
}
function pick(kind: 'exercise' | 'answer') { if (!props.uploadBlocked) { uploadKind.value = kind; input.value?.click() } }
function openRenamePractice() {
  const file = currentFile.value
  if (!file || modelBusy.value || ['queued', 'processing'].includes(file.status)) return
  renameError.value = ''; renameTarget.value = { ...file }
}
async function renamePractice(name: string) {
  const file = renameTarget.value
  if (!file || renaming.value) return
  name = name.trim()
  if (!name) { renameError.value = '练习名称不能为空'; return }
  if (name === practiceName(file)) { renameTarget.value = null; return }
  const token = generation
  renaming.value = true; renameError.value = ''
  try {
    const value = await api<{ id: string; practice_title: string }>(`/documents/${file.id}/practice/name`, {
      method: 'PATCH', body: JSON.stringify({ name }), signal: generatedListController.signal,
    })
    if (!active(token)) return
    const updated = { ...file, practice_title: value.practice_title }
    ++generatedListSerial
    generatedFiles.value = generatedFiles.value.map(item => item.id === file.id ? { ...item, practice_title: value.practice_title } : item)
    emit('renameDocument', updated)
    renameTarget.value = null
  } catch (reason) { if (active(token)) renameError.value = message(reason) }
  finally { renaming.value = false }
}
function deleteCurrentFile() {
  const file = currentFile.value
  if (!file || working.value || ['queued', 'processing'].includes(file.status)) return
  emit('deleteDocument', file)
}
function confirmDeleteQuestion() {
  if (!current.value || modelBusy.value) return
  deleteError.value = ''
  deleteTarget.value = current.value
}
async function deleteQuestion() {
  const question = deleteTarget.value
  if (!question || modelBusy.value || !bank.value?.questions.some(q => q.id === question.id)) return
  cancel()
  const token = generation
  requestController = new AbortController()
  deletingQuestion.value = question.id; deleteError.value = ''; error.value = ''
  try {
    if (!await progress.flush()) throw new Error('作答进度尚未同步，请稍后再删除题目。')
    if (!active(token)) return
    await api(`/practice/questions/${question.id}`, { method: 'DELETE', signal: requestController.signal })
    if (!active(token) || !bank.value) return
    restoringProgress = true
    try {
      bank.value.questions = bank.value.questions.filter(q => q.id !== question.id)
      delete selections.value[question.id]; delete drafts.value[question.id]
      delete results.value[question.id]; delete visible.value[question.id]
      delete translationVisible.value[question.id]
      delete explanationBusy.value[question.id]; delete explanationErrors.value[question.id]
      index.value = Math.min(index.value, Math.max(0, bank.value.questions.length - 1))
    } finally { restoringProgress = false }
    deleteTarget.value = null
    await read(token, true)
  } catch (reason) { if (active(token)) deleteError.value = message(reason) }
  finally { deletingQuestion.value = '' }
}
async function upload(list: FileList | null) {
  if (!list?.length || uploading.value) return
  if (props.uploadBlocked) { error.value = '请先重新建立索引，再上传新资料。'; if (input.value) input.value.value = ''; return }
  const year = uploadYear.value.trim()
  if (year && (!/^\d{4}$/.test(year) || Number(year) < 1900 || Number(year) > 2100)) {
    error.value = '年份请填写 1900–2100 之间的四位数字'; if (input.value) input.value.value = ''; return
  }
  uploading.value = true; error.value = ''
  let uploadedId = ''
  try {
    for (const file of Array.from(list)) {
      if (!supportsUpload(file.name)) throw new Error(`${file.name}：${uploadFormatsLabel}`)
      const form = new FormData()
      form.append('file', file); form.append('kind', uploadKind.value)
      if (year) form.append('year', year)
      const doc = await api<Document>(`/courses/${props.courseId}/documents`, { method: 'POST', body: form })
      if (uploadKind.value === 'exercise') uploadedId ||= doc.id
    }
  } catch (reason) { error.value = message(reason) }
  finally {
    uploading.value = false; if (input.value) input.value.value = ''
    emit('refresh')
    if (uploadedId) documentId.value = uploadedId
  }
}
const saveBeforeLeave = () => progress.flush(true)
onMounted(() => window.addEventListener('pagehide', saveBeforeLeave))
onDeactivated(() => progress.flush())
onUnmounted(() => { progress.dispose(); window.removeEventListener('pagehide', saveBeforeLeave); disposed = true; generatedListController.abort(); cancel(); emit('busy', false); emit('modelBusy', false) })
</script>

<template>
  <PracticeGenerator :course-id="courseId" :chapters="chapters" :model-configured="modelConfigured" :working="modelBusy" :wrong-revision="wrongRevision" @generated="useGeneratedSet" @busy="generationBusy = $event" />
  <section class="panel practice-upload">
    <div class="section-top"><div><h2>上传练习材料</h2><p class="muted">选择题按上传答案判题；模型答案仅供参考。其他题型自行作答，按需显示答案。</p></div></div>
    <div class="practice-upload-actions">
      <button class="button primary" :disabled="working || uploadBlocked" @click="pick('exercise')">{{ uploading ? '上传中…' : '上传题目' }}</button>
      <span class="practice-upload-hint">提示：格式混乱的试卷中，非选择题有可能解析错误。</span>
      <button class="button subtle" :disabled="working || uploadBlocked" @click="pick('answer')">上传答案</button>
      <label>年份（可选）<input v-model="uploadYear" inputmode="numeric" maxlength="4" placeholder="例如 2024" :disabled="working" /></label>
      <span class="muted">题目和答案在同一文件时，点击“上传题目”，会自动匹配原文答案；分开的文件分别上传，按文件名自动关联。{{ uploadFormatsLabel }}。</span>
      <input ref="input" type="file" :accept="uploadAccept" multiple hidden @change="upload(($event.target as HTMLInputElement).files)" />
    </div>
    <div v-if="currentFile && !isGenerated" class="practice-answer-pairing" :class="{ 'needs-confirmation': bank?.answer_pairing?.needs_confirmation }">
      <strong>试卷答案关联</strong>
      <div class="practice-pairing-controls">
        <label>对应答案<select id="practice-answer-file" v-model="pairingSelection" :disabled="modelBusy || currentFile?.status !== 'ready'" aria-label="选择这份试卷对应的答案文件">
          <option value="auto">自动匹配</option>
          <option value="none">不使用上传答案</option>
          <option v-if="bank?.answer_pairing?.missing" :value="bank.answer_pairing.answer_document_id || ''">原关联答案已删除，请重新选择</option>
          <option v-for="file in answerFiles" :key="file.id" :value="file.id" :disabled="file.status !== 'ready'">{{ file.filename }}{{ file.year ? ` · ${file.year} 年` : '' }}{{ file.status !== 'ready' ? ' · 待解析完成' : '' }}</option>
        </select></label>
        <button class="button subtle small" :disabled="modelBusy || currentFile?.status !== 'ready' || !pairingDirty" @click="saveAnswerPairing">{{ pairingBusy ? '保存中…' : '保存关联' }}</button>
      </div>
      <p v-if="currentFile?.status !== 'ready'" class="muted">试卷解析完成后，会自动确认对应答案，也可在这里手动选择。</p>
      <p v-else-if="bank?.answer_pairing?.needs_confirmation" class="practice-pairing-notice">无法确认对应答案，请在上方选择答案文件并保存。没有对应答案时，选择“不使用上传答案”。</p>
      <p v-else-if="bank?.answer_pairing?.mode === 'manual'" class="muted">已关联你指定的答案文件；仍会核对大题、题号和题目内容。</p>
      <p v-else-if="bank?.answer_pairing?.mode === 'none'" class="muted">不使用其他上传答案；原题自带的明确答案仍会自动匹配。</p>
      <p v-else-if="bank?.answer_pairing?.embedded" class="muted">已识别同一文件中的答案，会自动匹配原题，无需另外上传答案。</p>
      <p v-else-if="bank?.answer_pairing?.matched_filename" class="muted">已按文件名自动关联：{{ bank.answer_pairing.matched_filename }}。仍会核对大题、题号和题目内容。</p>
      <p v-else class="muted">还没有可关联的答案。可点击“上传答案”；例如“数据结构2024试卷A”和“数据结构2024试卷A答案”会自动关联。</p>
    </div>
  </section>
  <section class="panel practice-panel">
    <div class="section-top"><div><h2>我的练习</h2><p class="muted">{{ currentFile ? `正在练习：${practiceName(currentFile)}` : 'AI 生成、上传的练习题和往年真题都可以在这里作答。' }}</p></div><div v-if="files.length" class="inline-actions"><button class="text-button" :disabled="modelBusy || !currentFile || ['queued', 'processing'].includes(currentFile.status)" @click="openRenamePractice">改名</button><button class="text-button" :disabled="questionBusy || generationBusy || deletionBusy || !!renameTarget" @click="refreshPractice">刷新题目 ↻</button><button class="text-button danger" :disabled="working || !currentFile || ['queued', 'processing'].includes(currentFile.status)" :aria-label="currentFile ? `删除练习 ${practiceName(currentFile)}` : '删除练习'" @click="deleteCurrentFile">{{ deletingDocumentId === documentId ? '删除中…' : isGenerated ? '删除 AI 练习' : '删除试卷' }}</button></div></div>
    <label v-if="files.length" class="practice-set-selector">选择练习<select v-model="documentId" aria-label="选择练习" :disabled="working"><option v-for="file in files" :key="file.id" :value="file.id">{{ practiceName(file) }}{{ file.kind === 'generated' ? ` · ${new Date(file.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}` : file.status !== 'ready' ? ' · 正在解析或待重试' : '' }}</option></select></label>
    <div v-if="generatedListError" class="notice error" role="alert"><span>读取 AI 练习失败：{{ generatedListError }}</span><button class="text-button" @click="loadGeneratedSets">重试</button></div>
    <div v-if="error" class="notice error" role="alert"><span>{{ error }}</span><button v-if="!bank || bank.status === 'queued' || bank.status === 'processing'" class="text-button" @click="load">重新连接</button></div>
    <div v-if="!generatedLoaded" class="empty-state compact" role="status"><p>正在读取练习列表…</p></div>
    <div v-else-if="!files.length" class="empty-state compact"><h3>还没有练习题</h3><p>使用上方“AI 生题”生成练习，或点击“上传题目”加入试卷。</p></div>
    <div v-else-if="currentFile?.status !== 'ready'" class="empty-state compact"><h3>{{ currentFile?.status === 'failed' ? '题目资料解析失败' : '正在解析题目资料…' }}</h3><p>{{ currentFile?.error || '解析完成后会自动整理成在线练习。可在资料库查看处理状态或重试。' }}</p></div>
    <div v-else-if="loading || bank?.status === 'queued' || bank?.status === 'processing'" class="practice-loading" role="status"><h3>{{ isGenerated ? '正在生成 AI 练习…' : '正在整理上传题目…' }}</h3><p class="muted">{{ isGenerated ? '正在生成题目与参考解析，完成后即可作答。' : '提取题干与选项，保留原题内容。' }}</p><div class="progress-track"><span :style="{ width: `${Math.round((bank?.progress || 0) * 100)}%` }"></span></div></div>
    <div v-else-if="bank?.status === 'new'" class="empty-state compact"><p>{{ modelConfigured ? '题目尚未整理' : '请先配置模型，用于整理题目及生成参考答案。' }}</p><button class="button primary" :disabled="!modelConfigured" @click="prepare">整理题目</button></div>
    <div v-else-if="bank?.status === 'failed'" class="empty-state compact"><h3>{{ isGenerated ? 'AI 生题未完成' : '题目整理未完成' }}</h3><p>{{ bank.error }}</p><p v-if="isGenerated && bank.progress > 0">已保存 {{ Math.round(bank.progress * 100) }}% 的生题进度，重试将继续生成剩余题目；课程资料变化后会重新生成。</p><button class="button primary" :disabled="!modelConfigured" @click="prepare">{{ isGenerated ? '重试生题' : '重新整理' }}</button></div>
    <div v-else-if="bank?.status === 'ready' && !bank.questions.length" class="empty-state compact"><h3>当前练习没有题目</h3><p>{{ isGenerated ? '题目已全部删除，可在上方重新生成一组练习。' : '可查看原文，或重新整理原文件中的题目。重新整理也会恢复之前删除的题目。' }}</p><button v-if="!isGenerated" class="button primary" :disabled="!modelConfigured || working" @click="prepare">重新整理原文件</button></div>
    <template v-else-if="current && bank?.status === 'ready'">
      <div v-if="answersPreparing" class="practice-answer-preparation" role="status"><span>正在准备上传答案 · {{ Math.round((bank.answer_preparation?.progress || 0) * 100) }}%</span><span>可以先作答，准备完成后提交更快。</span></div>
      <div class="practice-summary"><span>第 {{ index + 1 }} / {{ bank.questions.length }} 题 · {{ saveStatus }}</span><span v-if="answered">已判 {{ answered }} 题 · 答对 {{ correct }} 题</span></div>
      <div class="practice-layout">
        <aside class="practice-navigation" aria-label="题目导航"><button v-for="(item, position) in bank.questions" :key="item.id" :class="['practice-number', { active: index === position, correct: results[item.id]?.correct === true, incorrect: results[item.id]?.correct === false, viewed: visible[item.id] && item.question_type === 'other' }]" :aria-label="`第 ${position + 1} 题${results[item.id]?.correct === true ? '，已答对' : results[item.id]?.correct === false ? '，已答错' : visible[item.id] ? '，已查看答案' : '，未判题'}`" :aria-current="index === position ? 'step' : undefined" :disabled="questionBusy" @click="move(position)">{{ position + 1 }}<span v-if="results[item.id]?.correct === true || results[item.id]?.correct === false" class="practice-state" aria-hidden="true">{{ results[item.id]?.correct ? '✓' : '×' }}</span></button></aside>
        <article class="practice-question" :key="current.id">
          <div class="practice-question-meta"><span class="badge neutral">{{ current.question_type === 'single_choice' ? '单选题' : current.question_type === 'multiple_choice' ? '多选题' : '其他题型 · 自行对照' }}</span><span v-if="current.locator.external_knowledge" class="badge neutral">含模型自行搜索的知识</span><span v-if="current.locator.model_knowledge" class="badge neutral">含模型已有知识的补充</span><span v-if="current.section">{{ current.section }}</span><span v-if="current.number && !isGenerated">原题 {{ current.number }}</span><span v-if="current.locator.label">{{ current.locator.label }}</span><button class="text-button danger practice-delete-question" :disabled="modelBusy" @click="confirmDeleteQuestion">{{ deletingQuestion === current.id ? '删除中…' : '删除题目' }}</button></div>
          <div v-if="hasEnglish || current.translation" class="practice-translation-actions">
            <button class="button subtle" :disabled="questionBusy || (!current.translation && !modelConfigured)" :aria-expanded="!!translationVisible[current.id]" @click="toggleTranslation">
              {{ translatingQuestion === current.id ? '正在翻译…' : translationVisible[current.id] ? '隐藏中文翻译' : current.translation ? '显示中文翻译' : '翻译题目和选项' }}
            </button>
            <span class="muted">{{ current.translation ? '译文已保存' : modelConfigured ? '译文自动保存，下次直接使用' : '请先配置模型以翻译题目' }}</span>
          </div>
          <MarkdownContent :content="current.stem" />
          <div v-if="current.translation && translationVisible[current.id]" class="practice-translation" aria-live="polite"><p class="practice-translation-label">题干中文翻译</p><MarkdownContent :content="current.translation.stem" /></div>
          <fieldset v-if="isChoice" class="practice-options">
            <legend class="sr-only">{{ current.question_type === 'multiple_choice' ? '选择所有正确选项' : '选择一个正确选项' }}</legend>
            <label v-for="option in current.options" :key="option.key" :class="['practice-option', { selected: selections[current.id]?.includes(option.key) }]">
              <input :type="current.question_type === 'single_choice' ? 'radio' : 'checkbox'" :name="`question-${current.id}`" :checked="selections[current.id]?.includes(option.key) || false" :disabled="questionBusy" @change="choose(current, option.key)" />
              <strong>{{ option.key }}</strong>
              <div class="practice-option-content">
                <MarkdownContent :content="option.text" />
                <div v-if="translationVisible[current.id] && translatedOptions[option.key]" class="practice-option-translation"><MarkdownContent :content="translatedOptions[option.key]" /></div>
              </div>
            </label>
          </fieldset>
          <div v-else class="practice-draft"><label :for="`draft-${current.id}`">你的作答（可选）</label><textarea :id="`draft-${current.id}`" v-model="drafts[current.id]" :disabled="!!deletingQuestion" rows="6" placeholder="在这里记录思路或答案，也可以在纸上作答。此题不自动判分。"></textarea></div>
          <div class="practice-controls"><button v-if="isChoice" class="button primary" :disabled="questionBusy || !selections[current.id]?.length" @click="resolve(true)">{{ busyQuestion ? '正在判题…' : '提交答案' }}</button><button v-else class="button primary" :disabled="questionBusy" @click="resolve(false)">{{ busyQuestion ? '正在获取答案…' : visible[current.id] ? '隐藏答案' : '显示答案' }}</button><button v-if="isChoice && currentResult" class="text-button" :disabled="questionBusy" @click="toggleAnswer(current.id)">{{ visible[current.id] ? '隐藏解析' : '显示解析' }}</button><button class="button subtle" :disabled="questionBusy" :aria-pressed="!!current.wrong_question_id" @click="toggleWrong">{{ markingQuestion ? '正在保存…' : current.wrong_question_id ? '移出错题本' : '＋ 加入错题本' }}</button></div>
          <p v-if="current.wrong_question_id" class="muted practice-saved" role="status">{{ currentResult?.correct === false ? '本次答错已自动收录到错题本' : '已收录到错题本' }} · 可在左侧“错题本”查看和删除。</p>
          <div v-if="currentResult" aria-live="polite">
            <div v-if="currentResult.origin !== 'model' && currentResult.correct !== null" :class="['notice', currentResult.correct ? 'success' : 'error']"><strong>{{ currentResult.correct ? '回答正确' : '回答错误' }}</strong><span>{{ currentResult.origin === 'embedded' ? '按原题自带答案判题' : '按上传答案判题' }} · 你的答案：{{ currentResult.selected?.join('、') }}　原文答案：{{ currentResult.choices.join('、') }}</span></div>
            <div v-else-if="currentResult.origin === 'model' && currentResult.correct !== null" :class="['notice', currentResult.correct ? 'success' : 'error']"><strong>{{ currentResult.correct ? '回答正确' : '回答错误' }}</strong><span>按模型参考答案判题 · 你的答案：{{ currentResult.selected?.join('、') }}　模型参考选项：{{ currentResult.choices.join('、') }}。答错时自动收录到错题本。</span></div>
            <div v-else-if="currentResult.resolved === false" class="notice info"><strong>暂无法判题</strong><span>尚未得到明确的参考选项，作答已保留，不计分、不自动收录错题；可以稍后重新提交。</span></div>
            <div v-if="visible[current.id]" class="practice-answer">
              <p class="practice-answer-origin">{{ currentResult.origin === 'uploaded' ? '上传答案' : currentResult.origin === 'embedded' ? '原题自带答案' : '模型参考答案' }} · {{ currentResult.source }}</p>
              <template v-if="currentResult.explanation_pending">
                <p v-if="explanationBusy[current.id]" class="muted" role="status">判题结果已显示，解析正在加载，可以继续下一题。</p>
                <p v-else-if="explanationErrors[current.id]" class="muted">解析暂未加载：{{ explanationErrors[current.id] }}</p>
                <p v-else class="muted">解析尚未加载。</p>
                <button v-if="!explanationBusy[current.id]" class="text-button" @click="loadExplanation(current.id)">{{ explanationErrors[current.id] ? '重试解析' : '加载解析' }}</button>
              </template>
              <MarkdownContent v-else :content="currentResult.answer" hide-citations />
            </div>
          </div>
          <div class="practice-paging"><button class="button subtle" :disabled="index === 0 || questionBusy" @click="move(index - 1)">← 上一题</button><button class="button subtle" :disabled="index === bank.questions.length - 1 || questionBusy" @click="move(index + 1)">下一题 →</button></div>
        </article>
      </div>
    </template>
  </section>
  <ManagementDialog v-if="renameTarget" title="修改练习名称" description="修改这份练习在列表中的名称，题目与作答进度会保留。" :initial-name="practiceName(renameTarget)" confirm-text="保存名称" :busy="renaming" :error="renameError" @close="renameTarget = null" @submit="renamePractice" />
  <ManagementDialog v-if="deleteTarget" title="删除题目" :description="`确定删除${deleteTarget.number ? `原题 ${deleteTarget.number}` : '这道题目'}吗？删除后无法撤销，其他题目的作答和进度会保留。原试卷文件和已收录的错题仍会保留。`" confirm-text="删除题目" danger :busy="!!deletingQuestion" :error="deleteError" @close="deleteTarget = null" @submit="deleteQuestion" />
</template>

<style scoped>
.practice-set-selector { display: grid; gap: 9px; font-size: 13px; color: var(--muted); margin: 18px 0; }
.practice-set-selector select { width: 100%; min-width: 0; }
.practice-upload-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.practice-upload-actions label { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.practice-upload-actions input:not([type=file]) { width: 100px; }
.practice-upload-actions > .muted { flex-basis: 100%; margin-top: 4px; }
.practice-upload-hint { font-size: 12px; color: var(--warning); line-height: 1.8; }
.practice-answer-pairing { display: grid; gap: 12px; margin-top: 22px; padding: 18px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface-alt); }
.practice-answer-pairing > strong { font-size: 13px; font-weight: 600; color: var(--ink); }
.practice-pairing-controls { display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }
.practice-pairing-controls label { display: grid; gap: 8px; flex: 1; min-width: min(240px, 100%); font-size: 13px; color: #53617b; }
.practice-pairing-controls select { width: 100%; min-width: 0; max-width: 100%; }
.practice-answer-pairing p { line-height: 1.8; margin: 0; }
.practice-answer-pairing.needs-confirmation { border-color: #e7d4b1; background: var(--warning-soft); }
.practice-pairing-notice { font-size: 13px; color: var(--warning); }
.practice-answer-preparation { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; font-size: 12px; color: var(--accent-dark); background: var(--soft); border-radius: 8px; padding: 12px 15px; margin-top: 18px; }
.practice-summary { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; margin: 20px 0; padding-bottom: 14px; border-bottom: 1px solid var(--line); font-size: 13px; color: var(--muted); }
.practice-layout { display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 26px; }
.practice-navigation { display: grid; grid-template-columns: repeat(4, 1fr); align-content: start; gap: 8px; max-height: 540px; overflow: auto; padding: 4px; scrollbar-width: thin; }
.practice-number { position: relative; border: 1px solid #d4ddeb; border-radius: 6px; background: var(--surface); color: #53617b; min-height: 36px; font-size: 13px; font-variant-numeric: tabular-nums; }
.practice-number:not(:disabled):hover { border-color: var(--accent); }
.practice-number.correct { background: var(--success-soft); border-color: #9bceba; color: var(--success); }
.practice-number.incorrect { background: var(--danger-soft); border-color: #e0aeb5; color: var(--danger); }
.practice-number.viewed { background: #eef1f8; }
.practice-number.active { box-shadow: 0 0 0 2px var(--accent); border-color: var(--surface); font-weight: 700; }
.practice-state { position: absolute; right: 2px; top: -3px; font-size: 9px; font-weight: 700; }
.practice-question { min-width: 0; padding-top: 6px; }
.practice-question > .markdown-content { font-size: 16px; line-height: 2; }
.practice-question-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; font-size: 12px; color: var(--muted); margin-bottom: 20px; }
.practice-delete-question { margin-left: auto; }
.practice-options { border: 0; padding: 0; display: grid; gap: 12px; margin: 24px 0; min-width: 0; }
.practice-option { display: flex; align-items: flex-start; gap: 12px; border: 1px solid #d6dfed; border-radius: 9px; padding: 15px 16px; cursor: pointer; background: var(--surface); transition: background-color .15s, border-color .15s; }
.practice-option:hover { background: var(--surface-alt); border-color: #a7b8d5; }
.practice-option.selected { border-color: var(--accent); background: #f0f4ff; box-shadow: inset 3px 0 var(--accent); }
.practice-option:focus-within { outline: 2px solid var(--accent); outline-offset: 3px; }
.practice-option input { margin: 7px 0 0; accent-color: var(--accent); width: 16px; height: 16px; flex-shrink: 0; }
.practice-option strong { font-size: 14px; color: var(--accent); padding-top: 2px; font-weight: 650; }
.practice-option-content { flex: 1; min-width: 0; }
.practice-option-content .markdown-content { font-size: 15px; }
.practice-translation-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.practice-translation { border-left: 3px solid #9ab0e5; background: var(--surface-alt); padding: 14px 18px; margin: 16px 0; }
.practice-translation-label { font-size: 12px; color: var(--muted); margin: 0 0 9px; }
.practice-option-translation { border-top: 1px dashed #cdd7ea; margin-top: 12px; padding-top: 12px; color: #53617b; }
.practice-draft { display: grid; gap: 10px; margin: 24px 0; }
.practice-draft label { font-size: 13px; color: #53617b; }
.practice-draft textarea { width: 100%; resize: vertical; line-height: 1.9; }
.practice-controls { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 24px 0; }
.practice-saved { font-size: 12px; margin: -8px 0 18px; }
.practice-answer { padding: 22px; border: 1px solid #d9e2ef; border-radius: 10px; background: var(--surface-alt); }
.practice-answer-origin { font-size: 12px; color: var(--muted); margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
.practice-paging { display: flex; justify-content: space-between; gap: 12px; margin-top: 28px; padding-top: 20px; border-top: 1px solid var(--line); }
.practice-loading { padding: 25px 0; }
.practice-loading h3 { font-size: 16px; margin-bottom: 8px; }
.practice-loading .progress-track { margin-top: 20px; }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
@media (max-width: 850px) {
  .practice-layout { grid-template-columns: 1fr; }
  .practice-navigation { grid-template-columns: repeat(auto-fill, minmax(36px, 1fr)); max-height: 150px; }
  .practice-answer { padding: 18px; }
}
@media (max-width: 600px) {
  .practice-answer-pairing { padding: 14px; }
  .practice-option { padding: 14px 12px; gap: 10px; }
  .practice-controls { gap: 10px; }
  .practice-answer { padding: 16px; }
  .practice-draft textarea { font-size: 16px; }
}
</style>
