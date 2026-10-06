<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, chatStream, downloadOutline } from './api'
import type { Block, Chapter, ChapterDraft, Course, Document, DocumentKind, Health, Job, Message, Outline, PracticeChatContext, Session, Source } from './types'
import { kindLabels } from './types'
import ChapterEditor from './components/ChapterEditor.vue'
import MarkdownContent from './components/MarkdownContent.vue'
import SourceDrawer from './components/SourceDrawer.vue'
import ManagementDialog from './components/ManagementDialog.vue'
import ModelSettings from './components/ModelSettings.vue'
import PracticePanel from './components/PracticePanel.vue'
import WrongQuestionsPanel from './components/WrongQuestionsPanel.vue'
import { supportsUpload, uploadAccept, uploadFormatsLabel } from './uploadFormats'

type Section = 'library' | 'outline' | 'practice' | 'wrong'
interface VisibleMessage extends Message { pending?: boolean; incomplete?: boolean }
const section = ref<Section>('library')
const courses = ref<Course[]>([])
const courseId = ref('')
const course = computed(() => courses.value.find(value => value.id === courseId.value))
const health = ref<Health | null>(null)
const connectionError = ref('')
const actionError = ref('')
const success = ref('')
const initialLoading = ref(true)
const courseLoading = ref(false)
const newCourseOpen = ref(false)
const newCourseName = ref('')
const creatingCourse = ref(false)
const modelSettingsOpen = ref(false)
const practiceBusy = ref(false)
const practiceModelBusy = ref(false)
const wrongBusy = ref(false)
const wrongRevision = ref(0)
const practiceContext = ref<PracticeChatContext | null>(null)
const currentChatContext = computed(() => section.value === 'practice' ? practiceContext.value : null)
const currentSession = computed(() => sessions.value.find(item => item.id === sessionId.value))
watch(section, () => { void scrollChat() })
type ManagementAction = 'rename-course' | 'delete-course' | 'new-session' | 'rename-session' | 'delete-session' | 'delete-outline'
interface Management { action: ManagementAction; id: string; courseId: string; title: string; description: string; initialName?: string; confirmText: string; danger?: boolean }
const management = ref<Management | null>(null)
const managementBusy = ref(false)
const managementError = ref('')
let managementGeneration = 0
const documents = ref<Document[]>([])
const chapters = ref<Chapter[]>([])
const knowledgeChapters = computed(() => chapters.value.filter(chapter => (chapter.kind || 'knowledge') === 'knowledge'))
const groups = ref<{ document: Document; blocks: Block[] }[]>([])
const chapterBusy = ref(false)
const indexRepairBusy = ref(false)
const uploadKind = ref<DocumentKind>('lecture')
const uploadYear = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const uploadBusy = ref(false)
const uploadLabel = ref('')
const documentBusy = ref<string | null>(null)
const dragOver = ref(false)
const sessions = ref<Session[]>([])
const sessionId = ref('')
const messages = ref<VisibleMessage[]>([])
const messagesLoading = ref(false)
const question = ref('')
const chatting = ref(false)
const newSessionBusy = ref(false)
const chatStatus = ref('')
const chatBox = ref<HTMLElement | null>(null)
const outlines = ref<Outline[]>([])
const outline = ref<Outline | null>(null)
const selectedOutlineId = ref('')
const outlineLoading = ref(false)
const outlineBusy = ref(false)
const job = ref<Job | null>(null)
// Track the course's running task independently of the selected history entry.
const runningJob = ref<Job | null>(null)
const showDrawer = ref(false)
const source = ref<Source | null>(null)
const sourceLoading = ref(false)
const sourceError = ref('')
let activeController: AbortController | null = null
let pollTimer: ReturnType<typeof setInterval> | undefined
let pollInFlight = false
let pollCount = 0
let courseGeneration = 0
let sourceGeneration = 0
let sessionGeneration = 0
let outlineGeneration = 0
const readyCount = computed(() => documents.value.filter(document => document.status === 'ready').length)
const unitCount = computed(() => documents.value.reduce((sum, document) => sum + (document.units_with_text || 0), 0))
const confirmed = computed(() => knowledgeChapters.value.length > 0 && chapters.value.every(chapter => chapter.confirmed))
const processingCount = computed(() => documents.value.filter(document => ['queued', 'processing'].includes(document.status)).length)
const activeJob = computed(() => !!(runningJob.value && ['queued', 'running'].includes(runningJob.value.status))
  || outlines.value.some(item => ['queued', 'running'].includes(item.status)))
const displayedJob = computed(() => activeJob.value ? runningJob.value : job.value)
const modelConfigured = computed(() => health.value?.llm_configured === true)
const indexRepairRequired = computed(() => !!courseId.value && (health.value?.index_repair_courses || []).includes(courseId.value))
const courseSwitchBusy = computed(() => initialLoading.value || courseLoading.value || chatting.value || uploadBusy.value || chapterBusy.value || outlineBusy.value || indexRepairBusy.value || !!documentBusy.value || newSessionBusy.value || creatingCourse.value || managementBusy.value || practiceBusy.value || wrongBusy.value)
const chapterControlsBusy = computed(() => chapterBusy.value || courseLoading.value || indexRepairBusy.value || uploadBusy.value || processingCount.value > 0 || !!documentBusy.value || outlineBusy.value || !!activeJob.value || chatting.value || managementBusy.value)
const canChat = computed(() => modelConfigured.value && readyCount.value > 0 && !indexRepairRequired.value && !indexRepairBusy.value && !courseLoading.value && !messagesLoading.value && !managementBusy.value && !chapterBusy.value && !newSessionBusy.value)
const sectionMeta: Record<Section, { title: string; intro: string; label: string }> = {
  library: { title: '每一份资料，都有条理。', intro: '先把课程资料放在一起，再确认你的复习路线。', label: '资料库' },
  outline: { title: '把课程知识，整理成复习笔记。', intro: '按主题串起概念、原理、公式和方法，用比较表格理清区别。', label: '复习笔记' },
  practice: { title: '边做题，边巩固所学。', intro: '按上传答案判题，模型答案仅供参考；作答与进度自动保存。', label: '在线练习' },
  wrong: { title: '再看一遍，直到真正掌握。', intro: '自动收集答错的题目，也能手动加入需要复习的题目。', label: '错题本' },
}
const statusLabels: Record<string, string> = { queued: '等待处理', processing: '解析文字、图片与公式', ready: '可用于复习', failed: '处理失败', running: '正在生成', completed: '已完成', interrupted: '已中断' }
function date(value: string) { const d = new Date(value); return Number.isNaN(d.getTime()) ? value : d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function errorText(error: unknown) { return error instanceof Error ? error.message : String(error) }
function clearFeedback() { actionError.value = ''; success.value = '' }
function setError(error: unknown) { actionError.value = errorText(error) }
async function refreshHealth() {
  try { health.value = await api<Health>('/health'); connectionError.value = '' }
  catch (error) { connectionError.value = errorText(error); health.value = null }
}
async function initialise() {
  initialLoading.value = true
  const results = await Promise.allSettled([refreshHealth(), api<Course[]>('/courses')])
  const courseResult = results[1]
  if (courseResult.status === 'fulfilled') {
    courses.value = courseResult.value as Course[]
    const stored = localStorage.getItem('review-course-id')
    courseId.value = courses.value.some(item => item.id === stored) ? stored! : courses.value[0]?.id || ''
  } else connectionError.value = errorText(courseResult.reason)
  initialLoading.value = false
}
async function createCourse() {
  if (!newCourseName.value.trim() || courseSwitchBusy.value) return
  clearFeedback(); creatingCourse.value = true
  try {
    const result = await api<Course>('/courses', { method: 'POST', body: JSON.stringify({ name: newCourseName.value.trim() }) })
    courses.value.push(result); courseId.value = result.id; newCourseName.value = ''; newCourseOpen.value = false
  } catch (error) { setError(error) }
  finally { creatingCourse.value = false }
}
function openManagement(value: Omit<Management, 'courseId'>) {
  if (managementBusy.value) return
  clearFeedback()
  managementError.value = ''
  management.value = { ...value, courseId: courseId.value }
}
function renameCourse(item: Course) {
  openManagement({ action: 'rename-course', id: item.id, title: '修改课程名称', description: '资料、会话和笔记会继续保存在这门课程中。', initialName: item.name, confirmText: '保存名称' })
}
function deleteCourse(item: Course) {
  openManagement({ action: 'delete-course', id: item.id, title: `删除课程“${item.name}”？`, description: '会同时删除这门课程的资料文件、检索索引、章节、所有复习会话和笔记，无法恢复。', confirmText: '删除课程', danger: true })
}
function nameNewSession() {
  openManagement({ action: 'new-session', id: '', title: '新建复习会话', description: '为这次复习取一个便于查找的名字。', initialName: '新复习对话', confirmText: '创建会话' })
}
function renameSession(item: Session) {
  openManagement({ action: 'rename-session', id: item.id, title: '修改会话名称', description: '修改名称后，可以在会话列表中继续复习。', initialName: item.title, confirmText: '保存名称' })
}
function deleteSession(item: Session) {
  openManagement({ action: 'delete-session', id: item.id, title: `删除会话“${item.title}”？`, description: '会删除这个会话的全部问答记录，无法恢复。课程资料和其他会话不受影响。', confirmText: '删除会话', danger: true })
}
function deleteOldOutline() {
  if (!outline.value?.stale) return
  openManagement({ action: 'delete-outline', id: outline.value.id, title: '删除这份过期笔记？', description: '会删除当前选中的过期笔记及其生成记录，无法恢复。资料和其他笔记不受影响。', confirmText: '删除过期笔记', danger: true })
}
async function submitManagement(name: string) {
  const target = management.value
  if (!target || managementBusy.value) return
  managementBusy.value = true; managementError.value = ''; ++managementGeneration
  try {
    if (target.action === 'rename-course') {
      const result = await api<Course>(`/courses/${target.id}`, { method: 'PATCH', body: JSON.stringify({ name }) })
      courses.value = courses.value.map(item => item.id === result.id ? result : item)
    } else if (target.action === 'delete-course') {
      await api<void>(`/courses/${target.id}`, { method: 'DELETE' })
      courses.value = courses.value.filter(item => item.id !== target.id)
      if (courseId.value === target.id) courseId.value = courses.value[0]?.id || ''
    } else if (target.action === 'new-session') {
      await newSession(name, target.courseId)
    } else if (target.action === 'rename-session') {
      const result = await api<Session>(`/sessions/${target.id}`, { method: 'PATCH', body: JSON.stringify({ title: name }) })
      if (courseId.value === target.courseId) sessions.value = sessions.value.map(item => item.id === result.id ? result : item)
    } else if (target.action === 'delete-session') {
      await api<void>(`/sessions/${target.id}`, { method: 'DELETE' })
      if (courseId.value === target.courseId) {
        sessions.value = sessions.value.filter(item => item.id !== target.id)
        if (sessionId.value === target.id) {
          ++sessionGeneration; messages.value = []; messagesLoading.value = false; sessionId.value = ''; question.value = ''
          if (sessions.value[0]) await selectSession(sessions.value[0].id)
        }
      }
    } else if (target.action === 'delete-outline') {
      await api<void>(`/outlines/${target.id}`, { method: 'DELETE' })
      if (courseId.value === target.courseId) {
        outlines.value = outlines.value.filter(item => item.id !== target.id)
        if (selectedOutlineId.value === target.id) {
          ++outlineGeneration; selectedOutlineId.value = ''; outline.value = null; job.value = null; outlineLoading.value = false
          if (outlines.value[0]) await selectOutline(outlines.value[0].id)
        }
      }
    }
    management.value = null
    success.value = target.action.startsWith('delete') ? '已删除。' : target.action === 'new-session' ? '复习会话已创建。' : '名称已保存。'
  } catch (error) { managementError.value = errorText(error) }
  finally { managementBusy.value = false; ++managementGeneration }
}
async function loadGroups(id = courseId.value) {
  const candidates = documents.value.filter(document => document.status === 'ready')
  const result = await Promise.all(candidates.map(async document => ({ document, blocks: await api<Block[]>(`/documents/${document.id}/blocks`) })))
  if (courseId.value === id) groups.value = result
}
async function loadCourse(id: string) {
  practiceContext.value = null
  const generation = ++courseGeneration
  ++sessionGeneration; ++outlineGeneration
  ++sourceGeneration; showDrawer.value = false; question.value = ''; messagesLoading.value = false; outlineLoading.value = false
  clearFeedback(); documents.value = []; chapters.value = []; groups.value = []; sessions.value = []; messages.value = []; sessionId.value = ''; outlines.value = []; outline.value = null; selectedOutlineId.value = ''; job.value = null; runningJob.value = null
  if (!id) { courseLoading.value = false; return }
  courseLoading.value = true
  try {
    const result = await Promise.all([
      api<Document[]>(`/courses/${id}/documents`), api<Chapter[]>(`/courses/${id}/chapters`),
      api<Session[]>(`/courses/${id}/sessions`), api<Outline[]>(`/courses/${id}/outlines`),
    ])
    if (generation !== courseGeneration) return
    ;[documents.value, chapters.value, sessions.value, outlines.value] = result
    const runningOutline = outlines.value.find(item => ['queued', 'running'].includes(item.status))
    if (runningOutline?.job_id) {
      const resultJob = await api<Job>(`/jobs/${runningOutline.job_id}`)
      if (generation !== courseGeneration) return
      runningJob.value = resultJob
    }
    await loadGroups(id)
    if (generation !== courseGeneration) return
    sessionId.value = sessions.value[0]?.id || ''
    if (sessionId.value) await selectSession(sessionId.value)
    selectedOutlineId.value = outlines.value[0]?.id || ''
    if (selectedOutlineId.value) await selectOutline(selectedOutlineId.value)
  } catch (error) { if (generation === courseGeneration) setError(error) }
  finally { if (generation === courseGeneration) courseLoading.value = false }
}
watch(courseId, id => { if (id) localStorage.setItem('review-course-id', id); else localStorage.removeItem('review-course-id'); void loadCourse(id) })
async function refreshDocuments() {
  const id = courseId.value
  const generation = managementGeneration
  if (!id) return
  const next = await api<Document[]>(`/courses/${id}/documents`)
  if (id !== courseId.value || generation !== managementGeneration) return
  const signature = (items: Document[]) => items.map(item => `${item.id}:${item.status}:${!!item.chapter_excluded}`).join('|')
  const changed = signature(documents.value) !== signature(next)
  documents.value = next
  if (changed) {
    const [updatedChapters, updatedCourses, updatedOutlines] = await Promise.all([api<Chapter[]>(`/courses/${id}/chapters`), api<Course[]>('/courses'), api<Outline[]>(`/courses/${id}/outlines`)])
    if (id !== courseId.value || generation !== managementGeneration) return
    chapters.value = updatedChapters; courses.value = updatedCourses; outlines.value = updatedOutlines
    if (outline.value) outline.value.stale = updatedOutlines.find(value => value.id === outline.value?.id)?.stale ?? outline.value.stale
    await loadGroups(id)
  }
}
async function upload(files: FileList | File[]) {
  if (!courseId.value || uploadBusy.value || chapterBusy.value || !files.length) return
  if (indexRepairRequired.value || indexRepairBusy.value) { actionError.value = '请先重新建立索引，再上传新资料。'; return }
  const id = courseId.value
  if (uploadYear.value && (!/^\d{4}$/.test(uploadYear.value) || Number(uploadYear.value) < 1900 || Number(uploadYear.value) > 2100)) { actionError.value = '年份请填写 1900–2100 范围内的四位数字，未知可以留空。'; return }
  clearFeedback(); uploadBusy.value = true
  const failed: string[] = []; let accepted = 0; let updated = 0
  for (const [index, file] of Array.from(files).entries()) {
    uploadLabel.value = `正在上传 ${index + 1}/${files.length}：${file.name}`
    if (!supportsUpload(file.name)) { failed.push(`${file.name}：${uploadFormatsLabel}`); continue }
    const body = new FormData(); body.append('file', file); body.append('kind', uploadKind.value)
    if (uploadYear.value) body.append('year', uploadYear.value)
    try {
      const result = await api<Document & { metadata_updated?: boolean }>(`/courses/${id}/documents`, { method: 'POST', body })
      accepted += 1
      if (result.metadata_updated) updated += 1
    }
    catch (error) { failed.push(`${file.name}：${errorText(error)}`) }
  }
  uploadBusy.value = false; uploadLabel.value = ''
  if (fileInput.value) fileInput.value.value = ''
  if (id !== courseId.value) return
  if (accepted) success.value = `已接收 ${accepted} 份资料，解析和索引进度会显示在列表中。${updated ? `其中 ${updated} 份已有资料已更新类型或年份，并重新解析。` : ''}`
  if (failed.length) actionError.value = failed.join('\n')
  try { await refreshDocuments() } catch (error) { setError(error) }
}
function drop(event: DragEvent) { dragOver.value = false; if (event.dataTransfer?.files) void upload(event.dataTransfer.files) }
async function deleteDocument(document: Document) {
  if (documentBusy.value || uploadBusy.value || practiceBusy.value || indexRepairBusy.value || managementBusy.value || chapterBusy.value || ['queued', 'processing'].includes(document.status)) return
  const isPaper = document.kind === 'exam' || document.kind === 'exercise'
  const description = isPaper
    ? '会删除这份试卷文件、练习题目和检索内容，无法恢复。已收录的错题会保留，旧笔记将标记为过期。'
    : '对应的检索内容也会删除，旧笔记将标记为过期。'
  if (!window.confirm(`删除“${document.filename}”？${description}`)) return
  clearFeedback(); documentBusy.value = document.id
  try { await api(`/documents/${document.id}`, { method: 'DELETE' }); await refreshDocuments(); success.value = isPaper ? '试卷已删除。' : '资料已删除。' }
  catch (error) { setError(error) }
  finally { documentBusy.value = null }
}
async function retryDocument(id: string) {
  if (chapterBusy.value) return
  clearFeedback(); documentBusy.value = id
  try { await api(`/documents/${id}/retry`, { method: 'POST' }); await refreshDocuments() }
  catch (error) { setError(error) }
  finally { documentBusy.value = null }
}
async function reparseDocument(id: string) {
  if (chapterBusy.value) return
  clearFeedback(); documentBusy.value = id
  try {
    await api(`/documents/${id}/reparse`, { method: 'POST' })
    await refreshDocuments()
    success.value = '已开始重新解析资料，无法识别的内容会自动跳过。'
  } catch (error) { setError(error) }
  finally { documentBusy.value = null }
}
async function preview(document: Document) {
  showDrawer.value = true; sourceLoading.value = true; source.value = null; sourceError.value = ''; const generation = ++sourceGeneration
  try {
    const blocks = await api<Block[]>(`/documents/${document.id}/blocks`)
    if (generation !== sourceGeneration) return
    source.value = { chunk_id: document.id, source_id: '原文提取预览', filename: document.filename, kind: document.kind, year: document.year, chapter_id: null, locator: { label: `${blocks.length} 个内容块 · 图片 OCR 与公式识别结果均标注在正文中` }, text: blocks.map(block => `${block.locator.label}${block.heading ? ` · ${block.heading}` : ''}\n${block.text || '（此位置未提取到文字）'}`).join('\n\n──────────\n\n') || '未提取到可预览的原文。' }
  } catch (error) { if (generation === sourceGeneration) sourceError.value = errorText(error) }
  finally { if (generation === sourceGeneration) sourceLoading.value = false }
}
async function draftChapters() {
  if (!courseId.value || chapterControlsBusy.value) return
  const id = courseId.value
  ++managementGeneration
  clearFeedback(); chapterBusy.value = true
  try {
    chapters.value = await api<Chapter[]>(`/courses/${id}/chapters/draft`, { method: 'POST' })
    await refreshCourseDetails()
    success.value = '已按知识资料重新整理章节，并按文件名序号排序；提纲、试卷等资料不列入目录。'
  }
  catch (error) { setError(error); await refreshCourseDetails() }
  finally { chapterBusy.value = false; ++managementGeneration }
}
async function saveChapters(value: ChapterDraft[]) {
  await persistChapters(value, true)
}
async function editChapters(value: ChapterDraft[]) {
  await persistChapters(value, confirmed.value, true)
}
async function deleteChapter(id: string) {
  if (!courseId.value || chapterControlsBusy.value) return
  const currentCourse = courseId.value
  const version = courses.value.find(item => item.id === currentCourse)?.version
  const query = version === undefined ? '' : `?expected_version=${version}`
  ++managementGeneration
  clearFeedback(); chapterBusy.value = true
  try {
    chapters.value = await api<Chapter[]>(`/courses/${currentCourse}/chapters/${id}${query}`, { method: 'DELETE' })
    await refreshCourseDetails()
    success.value = `章节已删除，原资料仍保留在资料库，可继续用于问答。${outlines.value.length ? '已有复习笔记已标记为过期。' : ''}`
  } catch (error) { setError(error); await refreshCourseDetails() }
  finally { chapterBusy.value = false; ++managementGeneration }
}
async function persistChapters(value: ChapterDraft[], confirm: boolean, editing = false) {
  if (!courseId.value || chapterControlsBusy.value) return
  const id = courseId.value
  const version = courses.value.find(course => course.id === id)?.version
  ++managementGeneration
  clearFeedback(); chapterBusy.value = true
  try {
    chapters.value = await api<Chapter[]>(`/courses/${id}/chapters`, { method: 'PATCH', body: JSON.stringify({ chapters: value, confirm, expected_version: version }) })
    await refreshCourseDetails()
    success.value = editing
      ? `章节调整已保存。${outlines.value.length ? '已有复习笔记已标记为过期，可按新目录重新生成。' : ''}`
      : '章节目录已确认，现在可以生成复习笔记；会参考大纲、提纲等辅助资料中的范围、目标和重点。'
  } catch (error) {
    setError(error)
    // SQL can be committed before an index update fails. Reload persisted IDs
    // instead of showing an unsaved local directory after a 503 response.
    await refreshCourseDetails()
  }
  finally { chapterBusy.value = false; ++managementGeneration }
}
async function refreshCourseDetails() {
  const id = courseId.value
  if (!id) return
  const results = await Promise.allSettled([
    api<Chapter[]>(`/courses/${id}/chapters`), api<Course[]>('/courses'),
    api<Outline[]>(`/courses/${id}/outlines`), api<Document[]>(`/courses/${id}/documents`), refreshHealth(),
  ])
  if (id !== courseId.value) return
  if (results[0].status === 'fulfilled') chapters.value = results[0].value
  if (results[1].status === 'fulfilled') courses.value = results[1].value
  if (results[3].status === 'fulfilled') {
    documents.value = results[3].value
    try { await loadGroups(id) }
    catch (error) { actionError.value += `${actionError.value ? '\n' : ''}刷新原文归属失败：${errorText(error)}` }
  }
  if (results[2].status === 'fulfilled') {
    outlines.value = results[2].value
    if (outline.value) outline.value.stale = outlines.value.find(item => item.id === outline.value?.id)?.stale ?? outline.value.stale
  }
  const failures = results.filter((item): item is PromiseRejectedResult => item.status === 'rejected')
  if (failures.length) actionError.value += `${actionError.value ? '\n' : ''}刷新保存状态失败：${failures.map(item => errorText(item.reason)).join('；')}`
}
async function repairIndex() {
  if (!courseId.value || indexRepairBusy.value || chapterBusy.value) return
  clearFeedback(); indexRepairBusy.value = true
  try {
    const result = await api<{ message: string }>(`/courses/${courseId.value}/index/repair`, { method: 'POST' })
    await refreshCourseDetails()
    success.value = result.message || '检索索引已重新建立，可以再次提问。'
  } catch (error) { setError(error); await refreshCourseDetails() }
  finally { indexRepairBusy.value = false }
}
async function selectSession(id: string) {
  if (chatting.value) return
  sessionId.value = id; const generation = ++sessionGeneration; messagesLoading.value = true; messages.value = []
  try { const result = await api<Message[]>(`/sessions/${id}/messages`); if (generation === sessionGeneration) messages.value = result }
  catch (error) { if (generation === sessionGeneration) setError(error) }
  finally { if (generation === sessionGeneration) { messagesLoading.value = false; await scrollChat() } }
}
async function newSession(title = '新复习对话', id = courseId.value) {
  if (newSessionBusy.value) return
  clearFeedback()
  newSessionBusy.value = true
  try {
    const result = await api<Session>(`/courses/${id}/sessions`, { method: 'POST', body: JSON.stringify({ title }) })
    if (id === courseId.value) { ++sessionGeneration; sessions.value.unshift(result); sessionId.value = result.id; messages.value = []; messagesLoading.value = false }
  } catch (error) { if (management.value?.action === 'new-session') throw error; setError(error) }
  finally { newSessionBusy.value = false }
}
async function scrollChat() { await nextTick(); if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight }
async function sendQuestion() {
  const text = question.value.trim()
  if (!text || chatting.value || !canChat.value) return
  const context = currentChatContext.value ? JSON.parse(JSON.stringify(currentChatContext.value)) as PracticeChatContext : null
  clearFeedback()
  chatting.value = true
  if (!sessionId.value) { await newSession(); if (!sessionId.value) { chatting.value = false; return } }
  const currentSession = sessionId.value
  question.value = ''; chatting.value = true; chatStatus.value = '正在准备检索…'
  const controller = new AbortController(); activeController = controller
  const user: VisibleMessage = { id: `local-user-${Date.now()}`, role: 'user', content: text, citations: [], created_at: new Date().toISOString(), practice_context: context }
  const answer = ref<VisibleMessage>({ id: `local-assistant-${Date.now()}`, role: 'assistant', content: '', citations: [], created_at: new Date().toISOString(), pending: true })
  messages.value.push(user, answer.value)
  // Use the reactive array member so streaming updates render immediately.
  const visibleAnswer = messages.value[messages.value.length - 1]
  let completed = false
  try {
    await chatStream(currentSession, text, controller.signal, event => {
      let data: Record<string, unknown>
      try { data = JSON.parse(event.data) } catch { throw new Error('回答流包含无法解析的数据。请重试。') }
      if (event.event === 'status') chatStatus.value = String(data.message || '正在整理答案…')
      if (event.event === 'delta') { visibleAnswer.content += String(data.text || ''); void scrollChat() }
      if (event.event === 'error') throw new Error(String(data.message || '回答生成失败。'))
      if (event.event === 'done') {
        completed = true; visibleAnswer.id = String(data.message_id || visibleAnswer.id)
        visibleAnswer.content = typeof data.content === 'string' ? data.content : visibleAnswer.content
        visibleAnswer.citations = []
      }
    }, context)
    if (!completed) throw new Error('回答流意外结束，未收到完成确认。')
    sessions.value = await api<Session[]>(`/courses/${courseId.value}/sessions`)
  } catch (error) {
    visibleAnswer.incomplete = !completed
    if ((error as Error).name === 'AbortError') chatStatus.value = '已停止生成'
    else { setError(error); chatStatus.value = '生成失败'; await refreshHealth() }
  } finally {
    visibleAnswer.pending = false; chatting.value = false; activeController = null
    if (completed) chatStatus.value = ''
    await scrollChat()
  }
}
function keyboard(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void sendQuestion() } }
async function selectOutline(id: string) {
  const generation = ++outlineGeneration; selectedOutlineId.value = id; outlineLoading.value = true
  try {
    const result = await api<Outline>(`/outlines/${id}`)
    if (generation !== outlineGeneration) return
    const resultJob = await api<Job>(`/jobs/${result.job_id}`)
    if (generation !== outlineGeneration) return
    outline.value = result; job.value = resultJob
  } catch (error) { if (generation === outlineGeneration) setError(error) }
  finally { if (generation === outlineGeneration) outlineLoading.value = false }
}
async function createOutline() {
  if (outlineBusy.value || activeJob.value || chapterBusy.value) return
  clearFeedback(); outlineBusy.value = true
  try {
    const result = await api<{ job_id: string; outline_id: string }>(`/courses/${courseId.value}/outlines`, { method: 'POST' })
    runningJob.value = { id: result.job_id, outline_id: result.outline_id, status: 'queued', progress: 0, stage: '等待生成' }
    outlines.value = await api<Outline[]>(`/courses/${courseId.value}/outlines`)
    await selectOutline(result.outline_id)
  } catch (error) { setError(error) }
  finally { outlineBusy.value = false }
}
async function retryOutline() {
  if (!job.value || outlineBusy.value || activeJob.value || chapterBusy.value) return
  clearFeedback(); outlineBusy.value = true
  try {
    const result = await api<{ job_id: string; outline_id: string }>(`/jobs/${job.value.id}/retry`, { method: 'POST' })
    runningJob.value = { id: result.job_id, outline_id: result.outline_id, status: 'queued', progress: 0, stage: '等待生成' }
    outlines.value = await api<Outline[]>(`/courses/${courseId.value}/outlines`)
    await selectOutline(result.outline_id)
  } catch (error) { setError(error) }
  finally { outlineBusy.value = false }
}
async function exportOutline() { if (outline.value) { try { await downloadOutline(outline.value.id) } catch (error) { setError(error) } } }
async function poll() {
  if (pollInFlight || initialLoading.value || courseLoading.value || managementBusy.value || chapterBusy.value) return
  const generation = managementGeneration
  const id = courseId.value
  const currentCourseGeneration = courseGeneration
  pollInFlight = true
  try {
    pollCount += 1
    if (pollCount % 4 === 0 || connectionError.value) await refreshHealth()
    if (courseId.value) await refreshDocuments()
    if (id !== courseId.value || currentCourseGeneration !== courseGeneration || generation !== managementGeneration) return
    const runningOutline = outlines.value.find(item => ['queued', 'running'].includes(item.status))
    const currentId = runningJob.value && ['queued', 'running'].includes(runningJob.value.status)
      ? runningJob.value.id : runningOutline?.job_id
    if (currentId) {
      const result = await api<Job>(`/jobs/${currentId}`)
      if (id !== courseId.value || currentCourseGeneration !== courseGeneration || generation !== managementGeneration) return
      runningJob.value = result
      if (outline.value?.id === result.outline_id) job.value = result
      if (!['queued', 'running'].includes(result.status)) {
        const resultOutlines = await api<Outline[]>(`/courses/${id}/outlines`)
        if (id !== courseId.value || currentCourseGeneration !== courseGeneration || generation !== managementGeneration) return
        outlines.value = resultOutlines
        if (selectedOutlineId.value) await selectOutline(selectedOutlineId.value)
      }
    }
  } catch (error) { if (id === courseId.value && currentCourseGeneration === courseGeneration && generation === managementGeneration) connectionError.value = errorText(error) }
  finally { pollInFlight = false }
}
onMounted(async () => { await initialise(); pollTimer = setInterval(() => void poll(), 2500) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); activeController?.abort() })
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar course-sidebar">
      <a class="brand" href="#" @click.prevent="section = 'library'"><div class="brand-mark"><span></span><span></span><span></span></div><div><strong>知页</strong><small>期末复习 Agent</small></div></a>
      <div class="workspace-label">你的复习空间</div>
      <nav class="main-nav" aria-label="主要功能"><button v-for="(item, key) in sectionMeta" :key="key" :class="['nav-item', { active: section === key }]" @click="section = key as Section"><span class="nav-symbol">{{ key === 'library' ? '▤' : key === 'outline' ? '≡' : key === 'practice' ? '✓' : key === 'wrong' ? '☆' : '◌' }}</span><span>{{ item.label }}</span><span v-if="section === key" class="nav-active-dot"></span></button></nav>
      <div class="sidebar-courses">
        <div class="courses-heading"><strong>全部课程 <span class="count-badge">{{ courses.length }}</span></strong><button class="icon-button small" aria-label="新建课程" title="新建课程" :disabled="courseSwitchBusy" @click="newCourseOpen = !newCourseOpen">＋</button></div>
        <form v-if="newCourseOpen" class="sidebar-course-form" @submit.prevent="createCourse"><input v-model="newCourseName" aria-label="新课程名称" placeholder="输入课程名称" maxlength="100" :disabled="courseSwitchBusy" /><div><button class="button primary small" :disabled="courseSwitchBusy || !newCourseName.trim()">{{ creatingCourse ? '创建中…' : '创建' }}</button><button type="button" class="text-button" :disabled="creatingCourse" @click="newCourseOpen = false">取消</button></div></form>
        <div class="course-list" role="navigation" aria-label="课程切换"><div v-for="item in courses" :key="item.id" :class="['course-list-row', { active: courseId === item.id }]"><button class="course-select" :aria-current="courseId === item.id ? 'page' : undefined" :title="item.name" :disabled="courseSwitchBusy" @click="courseId = item.id">{{ item.name }}</button><div class="course-row-actions"><button class="text-button" :disabled="courseSwitchBusy" :aria-label="`修改课程 ${item.name} 的名称`" @click="renameCourse(item)">改名</button><button class="text-button danger" :disabled="courseSwitchBusy" :aria-label="`删除课程 ${item.name}`" @click="deleteCourse(item)">删除</button></div></div><p v-if="!courses.length" class="muted course-list-empty">还没有课程，点击 ＋ 创建。</p></div>
      </div>
      <div class="sidebar-course"><p class="workspace-label">当前课程</p><strong>{{ course?.name || '先创建一门课程' }}</strong><p v-if="course">{{ readyCount }} 份可用资料 · {{ knowledgeChapters.length }} 个章节</p><div class="sidebar-progress"><span :style="{ width: documents.length ? `${readyCount / documents.length * 100}%` : '0%' }"></span></div></div>
      <div class="sidebar-bottom"><span class="leaf-icon">✳</span><p>让复习有出处，<br />让每一页都连起来。</p><div class="local-badge"><span></span> 本机个人工作台</div></div>
    </aside>
    <div class="workspace managed-workspace">
      <header class="topbar"><div class="breadcrumb">复习空间 <span>/</span> {{ sectionMeta[section].label }}</div><div class="topbar-actions"><button class="button subtle small" @click="modelSettingsOpen = true">⚙ 模型配置</button><div :class="['health-pill', { offline: !!connectionError }]" :title="connectionError || '后端服务已连接'"><span></span>{{ initialLoading ? '连接中' : connectionError ? '服务未连接' : '服务已连接' }}</div></div></header>
      <main class="main-content" :class="{ 'with-review-chat': courseId && section !== 'library' }">
        <div class="page-heading"><div><p class="eyebrow">STUDY / {{ course?.name || '从一门课程开始' }}</p><h1>{{ sectionMeta[section].title }}</h1><p>{{ sectionMeta[section].intro }}</p></div><div class="heading-note"><span>一点一点，</span><strong>把知识串起来。</strong></div></div>
        <div v-if="connectionError" class="notice error" role="alert"><strong>无法连接服务</strong><span>{{ connectionError }}</span><button class="text-button" @click="initialise">重新连接</button></div>
        <div v-else-if="health && !modelConfigured" class="notice warning"><strong>聊天模型尚未配置</strong><span>资料上传与目录整理可以使用。点击“模型配置”，选择服务商并填写模型和 API Key，保存后即可生成回答和复习笔记。</span><button class="text-button" @click="modelSettingsOpen = true">配置模型 →</button></div>
        <div v-if="health && !health.embedding_ready && documents.length && !indexRepairRequired" class="notice info"><span>检索模型将在需要时加载。已下载的模型直接从本地读取；尚未下载时需要联网获取模型文件。</span></div>
        <div v-if="indexRepairRequired" class="notice warning"><strong>本地检索索引需要重新建立</strong><span>{{ indexRepairBusy ? '正在用本地向量模型重新处理已保存资料，请等待完成。首次加载模型可能较慢；此操作不会调用聊天模型。' : '资料、章节目录和复习笔记仍保留。点击重新建立索引后再使用问答；复习笔记仍可正常生成。' }}</span><button class="button subtle small" :disabled="indexRepairBusy || chapterBusy || !!activeJob || chatting" @click="repairIndex">{{ indexRepairBusy ? '正在重新建立…' : '重新建立索引' }}</button></div>
        <div v-if="actionError" class="notice error feedback" role="alert"><span>{{ actionError }}</span><button class="icon-button small" aria-label="关闭错误提示" @click="actionError = ''">×</button></div>
        <div v-if="success" class="notice success feedback" role="status"><span>{{ success }}</span><button class="icon-button small" aria-label="关闭成功提示" @click="success = ''">×</button></div>
        <div v-if="initialLoading || courseLoading" class="loading-bar">正在加载复习空间…</div>
        <section v-if="!courseId && !initialLoading" class="panel welcome-panel"><div class="welcome-art"><span>知识</span><span>笔记</span><span>问题</span><i></i></div><p class="eyebrow">START HERE</p><h2>先为一门课程建个空间</h2><p>上传课件、笔记和往年真题，整理成一份按主题组织的复习笔记。</p><form @submit.prevent="createCourse"><input v-model="newCourseName" aria-label="新课程名称" placeholder="输入课程名称" maxlength="100" /><button class="button primary" :disabled="creatingCourse || !newCourseName.trim()">创建复习空间 →</button></form></section>

        <template v-if="courseId && section === 'library'">
          <div class="stats-row"><div class="stat-card"><span>课程资料</span><strong>{{ documents.length }}<small>份</small></strong><p>{{ processingCount ? `${processingCount} 份正在处理` : '按类型组织课程内容' }}</p></div><div class="stat-card"><span>可用于复习</span><strong>{{ readyCount }}<small>份</small></strong><p>解析完成并已建立索引</p></div><div class="stat-card"><span>已提取原文</span><strong>{{ unitCount }}<small>处</small></strong><p>页、幻灯片或文档内容位置</p></div><div class="stat-card accent"><span>章节路线</span><strong>{{ knowledgeChapters.length }}<small>章</small></strong><p>{{ confirmed ? '目录已确认，准备好生成复习笔记' : '下一步：确认章节目录' }}</p></div></div>
          <section class="panel"><div class="section-top"><div><p class="eyebrow">01 / 课程资料</p><h2>给复习准备好材料</h2><p class="muted">自动提取文档正文、图片文字和公式，无法识别的内容会跳过。</p></div><span class="badge neutral">支持批量上传</span></div>
            <div class="upload-options"><label>资料类型<select v-model="uploadKind" :disabled="uploadBusy"><option v-for="(label, key) in kindLabels" :key="key" :value="key">{{ label }}</option></select></label><label v-if="uploadKind === 'exam' || uploadKind === 'exercise' || uploadKind === 'answer'">年份 <span class="muted">（可选）</span><input v-model="uploadYear" type="text" inputmode="numeric" maxlength="4" placeholder="例如 2024" :disabled="uploadBusy" /></label><p>同批文件使用相同类型；不同类型请分批上传。</p></div>
            <div :class="['upload-area', { dragging: dragOver, busy: uploadBusy }]" @dragover.prevent="dragOver = true" @dragleave.prevent="dragOver = false" @drop.prevent="drop"><div class="upload-icon">↑</div><h3>{{ uploadBusy ? '正在接收资料…' : '把资料拖到这里' }}</h3><p>{{ uploadLabel || (indexRepairRequired ? '请先重新建立索引，再加入新资料' : '保留原文位置，让每次回答都有据可查') }}</p><button class="button primary" :disabled="uploadBusy || indexRepairRequired || indexRepairBusy" @click="fileInput?.click()">{{ uploadBusy ? '上传中…' : '选择文件' }}</button><input ref="fileInput" type="file" multiple :accept="uploadAccept" hidden @change="upload(($event.target as HTMLInputElement).files || [])" /><small>{{ uploadFormatsLabel }}</small></div>
            <div class="list-heading"><strong>全部资料 <span class="count-badge">{{ documents.length }}</span></strong><div class="inline-actions"><button v-if="readyCount && !indexRepairRequired" class="text-button" :disabled="indexRepairBusy || chapterBusy || !!activeJob || chatting" @click="repairIndex">{{ indexRepairBusy ? '正在修复…' : '修复索引' }}</button><button class="text-button" @click="refreshDocuments().catch(setError)">刷新状态 ↻</button></div></div>
            <div v-if="!documents.length" class="empty-state compact"><p>还没有课程资料</p><span>建议先上传少量资料，检查解析内容后再逐批加入。</span></div>
            <div v-for="document in documents" :key="document.id" class="document-row"><div :class="['file-type', document.filename.split('.').pop()?.toLowerCase()]">{{ document.filename.split('.').pop()?.toUpperCase() }}</div><div class="document-info"><h3>{{ document.filename }}</h3><div class="document-meta"><span>{{ kindLabels[document.kind] }}</span><span v-if="document.year">{{ document.year }} 年</span><span v-if="document.units_total">{{ document.units_with_text }} / {{ document.units_total }} 处有内容</span></div><p v-if="document.error" class="document-error">{{ document.error }}</p></div><span :class="['badge', document.status]">{{ statusLabels[document.status] || document.status }}</span><div class="document-actions"><button class="text-button" :disabled="['queued', 'processing'].includes(document.status)" @click="preview(document)">原文预览</button><button v-if="document.status === 'ready'" class="text-button" :disabled="!!documentBusy || uploadBusy || indexRepairRequired || indexRepairBusy || !!activeJob || processingCount > 0" @click="reparseDocument(document.id)">重新解析（OCR）</button><button v-if="document.status === 'failed'" class="text-button" :disabled="!!documentBusy || indexRepairRequired || indexRepairBusy || !!activeJob" @click="retryDocument(document.id)">重试</button><button class="text-button danger" :disabled="documentBusy === document.id || uploadBusy || ['queued', 'processing'].includes(document.status)" @click="deleteDocument(document)">删除</button></div></div>
          </section>
          <ChapterEditor :key="courseId" :chapters="chapters" :groups="groups" :busy="chapterControlsBusy" @save="saveChapters" @edit="editChapters" @delete="deleteChapter" @draft="draftChapters" />
          <div v-if="confirmed" class="next-step"><span>复习路线已经准备好了。</span><button class="text-button" @click="section = 'outline'">去生成复习笔记 →</button></div>
        </template>

        <div v-if="courseId" v-show="section !== 'library'" class="study-workspace">
          <div class="study-content">
        <template v-if="section === 'outline'">
          <section class="outline-intro panel"><div class="outline-illustration"><span>01</span><span>02</span><span>03</span></div><div><p class="eyebrow">YOUR REVIEW NOTES</p><h2>一门课，一份连贯的复习笔记</h2><p>围绕核心主题整理概念、原理、公式和算法，用表格比较容易混淆的内容。</p><div class="outline-checks"><span :class="{ checked: readyCount > 0 }">{{ readyCount > 0 ? '✓' : '○' }} {{ readyCount }} 份可用资料</span><span :class="{ checked: confirmed }">{{ confirmed ? '✓' : '○' }} {{ confirmed ? '章节已确认' : '章节待确认' }}</span></div></div><button class="button primary" :disabled="!confirmed || !readyCount || !modelConfigured || indexRepairBusy || outlineBusy || !!activeJob || !!processingCount" @click="createOutline">{{ activeJob ? '正在生成…' : outlineBusy ? '创建任务…' : '生成复习笔记 →' }}</button></section>
          <div v-if="!confirmed" class="notice info"><span>生成前，请先在资料库确认章节目录与资料归属。</span><button class="text-button" @click="section = 'library'">去确认目录 →</button></div>
          <div v-if="processingCount" class="notice info">还有 {{ processingCount }} 份资料正在处理，请待入库完成后再生成复习笔记。</div>
          <section v-if="displayedJob" class="panel job-panel"><div class="section-top"><div><h3>{{ statusLabels[displayedJob.status] || displayedJob.status }}</h3><p class="muted">{{ displayedJob.stage || '准备整理课程资料' }}</p></div><strong>{{ Math.round(displayedJob.progress || 0) }}%</strong></div><div class="progress-track"><span :style="{ width: `${Math.min(100, Math.max(0, displayedJob.progress || 0))}%` }"></span></div><p v-if="displayedJob.error" class="document-error">{{ displayedJob.error }}</p><button v-if="['failed', 'interrupted'].includes(displayedJob.status)" class="button subtle" :disabled="outlineBusy || !!activeJob || !modelConfigured || indexRepairRequired || indexRepairBusy" @click="retryOutline">重试未完成章节</button></section>
          <section class="panel outline-content-panel"><div class="section-top"><div><p class="eyebrow">MARKDOWN / 复习笔记</p><h2>你的复习笔记</h2></div><div class="inline-actions"><select v-if="outlines.length" :value="selectedOutlineId" aria-label="选择历史笔记" :disabled="outlineBusy || outlineLoading || managementBusy" @change="selectOutline(($event.target as HTMLSelectElement).value)"><option v-for="item in outlines" :key="item.id" :value="item.id">{{ date(item.created_at) }} · {{ statusLabels[item.status] || item.status }}{{ item.stale ? ' · 已过期' : '' }}</option></select><button v-if="outline?.stale" class="button subtle danger" :disabled="outlineLoading || outlineBusy || !!activeJob || managementBusy" @click="deleteOldOutline">删除过期笔记</button><button class="button subtle" :disabled="!outline?.markdown" @click="exportOutline">↓ 下载 .md</button></div></div>
            <div v-if="outline?.stale" class="notice warning"><strong>这份笔记已过期</strong><span>资料或章节已经变化。可重新生成，也可点击“删除过期笔记”清除当前选中的旧版本。</span></div>
            <div v-if="outline?.status === 'completed' && outline.current_style && outline.style !== outline.current_style" class="notice info">这是之前生成的笔记。点击上方生成按钮，可重新整理；英文资料中的关键名词会同时保留英文。</div>
            <p v-if="outlineLoading" class="loading-bar">正在读取复习笔记…</p><MarkdownContent v-else-if="outline?.markdown" :content="outline.markdown" /><div v-else class="empty-state outline-empty"><div class="empty-symbol">≡</div><h3>{{ activeJob ? '正在把资料整理成复习笔记' : '复习笔记从这里开始' }}</h3><p>{{ activeJob ? '正在筛选重点、合并主题，章节完成后会保存。' : '上传资料、确认目录，再按主题生成复习笔记。' }}</p><div class="outline-tags"><span>概念与原理</span><span>公式与条件</span><span>算法步骤</span><span>比较表格</span></div></div>
          </section>
        </template>

        <KeepAlive v-if="courseId" :key="courseId">
          <PracticePanel v-if="section === 'practice'" :course-id="courseId" :documents="documents" :model-configured="modelConfigured" :wrong-revision="wrongRevision" :deletion-busy="!!documentBusy || uploadBusy || indexRepairBusy || managementBusy || chapterBusy" :deleting-document-id="documentBusy" :upload-blocked="indexRepairRequired || indexRepairBusy" @delete-document="deleteDocument" @refresh="refreshDocuments().catch(setError)" @busy="practiceBusy = $event" @model-busy="practiceModelBusy = $event" @wrong-change="wrongRevision++" @context-change="practiceContext = $event" />
        </KeepAlive>

        <WrongQuestionsPanel v-if="courseId && section === 'wrong'" :key="courseId" :course-id="courseId" :revision="wrongRevision" @busy="wrongBusy = $event" @change="wrongRevision++" />

          </div>
          <aside class="review-chat panel review-chat-floating" aria-label="复习对话">
            <div class="chat-main">
              <header class="chat-top"><div><span class="chat-avatar">知</span><strong>复习对话</strong></div><button class="text-button" :disabled="chatting || newSessionBusy || courseLoading || managementBusy" @click="nameNewSession">＋ 新会话</button></header>
              <div class="chat-session-bar"><select :value="sessionId" aria-label="选择复习会话" :disabled="chatting || newSessionBusy || messagesLoading || managementBusy" @change="selectSession(($event.target as HTMLSelectElement).value)"><option v-if="!sessionId" value="">开始新的复习对话</option><option v-for="item in sessions" :key="item.id" :value="item.id">{{ item.title }}</option></select><button v-if="currentSession" class="text-button" :disabled="chatting || newSessionBusy || managementBusy" @click="renameSession(currentSession)">改名</button><button v-if="currentSession" class="text-button danger" :disabled="chatting || newSessionBusy || managementBusy" @click="deleteSession(currentSession)">删除</button></div>
              <div v-if="currentChatContext" class="chat-question-context" role="status"><strong>正在讨论第 {{ currentChatContext.number }} 题</strong><span :title="currentChatContext.filename">{{ currentChatContext.filename }}</span></div>
              <div ref="chatBox" class="message-list" aria-live="polite"><p v-if="messagesLoading" class="muted">正在读取历史会话…</p><div v-else-if="!messages.length" class="chat-welcome"><div class="chat-welcome-mark">✳</div><p class="eyebrow">LET'S CONNECT THE DOTS</p><h2>{{ currentChatContext ? '这道题，哪里还没弄懂？' : '有疑问，随时问。' }}</h2><p>{{ currentChatContext ? '已自动带上当前题目和你的作答，可以直接提问或追问。' : '边看笔记、边做题，随时解释概念和梳理思路。' }}</p></div>
                <article v-for="message in messages" :key="message.id" :class="['message', message.role]"><div class="message-avatar">{{ message.role === 'user' ? '我' : '知' }}</div><div class="message-body"><div class="message-label">{{ message.role === 'user' ? '你' : '复习助手' }}<span v-if="message.pending" class="streaming-label">正在整理…</span><span v-if="message.incomplete" class="incomplete-label">未完成 · 仅本地保留</span></div><div v-if="message.role === 'user'" class="user-text"><small v-if="message.practice_context" class="message-question-label">第 {{ message.practice_context.number }} 题 · {{ message.practice_context.filename }}</small>{{ message.content }}</div><MarkdownContent v-else-if="message.content" :content="message.content" hide-citations /><div v-else-if="message.pending" class="thinking"><span></span><span></span><span></span><small>{{ chatStatus }}</small></div><p v-else-if="message.incomplete" class="muted">本次没有收到完整回答，可以重新提问。</p></div></article>
              </div>
              <form class="chat-composer" @submit.prevent="sendQuestion"><div v-if="chatting" class="chat-status"><span class="pulse-dot"></span>{{ chatStatus || '正在生成回答…' }}<button type="button" class="text-button" @click="activeController?.abort()">停止生成</button></div><div v-if="indexRepairRequired" class="composer-notice">课程检索索引需要修复，请点击上方“重新建立索引”。</div><div v-else-if="!modelConfigured" class="composer-notice">配置聊天模型后，即可使用复习问答。</div><div v-else-if="!readyCount" class="composer-notice">先上传并处理至少一份资料，再开始提问。</div><div class="composer-input"><textarea v-model="question" rows="2" :disabled="!canChat || chatting" :placeholder="currentChatContext ? '直接问这道题，也可以结合你的作答追问…' : '围绕课程资料提问，或继续追问…'" aria-label="复习问题" @keydown="keyboard"></textarea><button class="send-button" aria-label="发送问题" :disabled="!question.trim() || !canChat || chatting">↑</button></div><div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><span>基于当前课程资料回答</span></div></form>
            </div></aside>
        </div>
        <footer class="page-footer"><span>知页 · 把资料连成知识</span><span>PDF / PPTX / DOCX → 章节 → 复习笔记 → 复习</span></footer>
      </main>
    </div>
    <SourceDrawer v-if="showDrawer" :source="source" :loading="sourceLoading" :error="sourceError" @close="showDrawer = false; ++sourceGeneration" />
    <ModelSettings v-if="modelSettingsOpen" :active="chatting || !!activeJob || practiceModelBusy" @close="modelSettingsOpen = false" @saved="refreshHealth" />
    <ManagementDialog v-if="management" :key="`${management.action}:${management.id}`" :title="management.title" :description="management.description" :initial-name="management.initialName" :confirm-text="management.confirmText" :danger="management.danger" :busy="managementBusy" :error="managementError" @close="management = null" @submit="submitManagement" />
  </div>
</template>
