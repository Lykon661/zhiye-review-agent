<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from './api'
import { useReviewChat } from './useReviewChat'
import { useOutlineTasks } from './useOutlineTasks'
import { useCourseMaterials } from './useCourseMaterials'
import type { Chapter, ChapterDraft, Course, Document, Health, NoteChatContext, Outline, PracticeChatContext, Session } from './types'
import { kindLabels, uploadKindLabels } from './types'
import ChapterEditor from './components/ChapterEditor.vue'
import MarkdownContent from './components/MarkdownContent.vue'
import SourceDrawer from './components/SourceDrawer.vue'
import ManagementDialog from './components/ManagementDialog.vue'
import ModelSettings from './components/ModelSettings.vue'
import MemorySettings from './components/MemorySettings.vue'
import PracticePanel from './components/PracticePanel.vue'
import ReviewSpacePanel from './components/ReviewSpacePanel.vue'
import WrongQuestionsPanel from './components/WrongQuestionsPanel.vue'
import NotesReader from './components/NotesReader.vue'
import UiIcon from './components/UiIcon.vue'
import TokenUsage from './components/TokenUsage.vue'
import { uploadAccept, uploadFormatsLabel } from './uploadFormats'

type Section = 'library' | 'outline' | 'space' | 'practice' | 'wrong'
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
const memoryOpen = ref(false)
const memoryBusy = ref(false)
const practiceBusy = ref(false)
const practiceModelBusy = ref(false)
const spaceBusy = ref(false)
const wrongBusy = ref(false)
const wrongRevision = ref(0)
const practiceContext = ref<PracticeChatContext | null>(null)
const wrongContext = ref<PracticeChatContext | null>(null)
const noteContext = ref<NoteChatContext | null>(null)
const currentChatContext = computed(() => section.value === 'practice' ? practiceContext.value : section.value === 'wrong' ? wrongContext.value : null)
const currentNoteContext = computed(() => section.value === 'outline' ? noteContext.value : null)
const chat = useReviewChat({
  courseId, canChat: () => canChat.value, practiceContext: () => currentChatContext.value,
  noteContext: () => currentNoteContext.value, namingNewSession: () => management.value?.action === 'new-session',
  clearFeedback, error: setError, refreshHealth,
})
const { sessions, sessionId, messages, messagesLoading, question, chatting, newSessionBusy, chatStatus, chatBox,
  selectSession, newSession, scrollChat, sendQuestion, keyboard, stopChat } = chat
const currentSession = computed(() => sessions.value.find(item => item.id === sessionId.value))
watch(section, () => { void scrollChat() })
type ManagementAction = 'rename-course' | 'delete-course' | 'new-session' | 'rename-session' | 'delete-session' | 'delete-outline'
interface Management { action: ManagementAction; id: string; courseId: string; title: string; description: string; initialName?: string; confirmText: string; danger?: boolean }
const management = ref<Management | null>(null)
const managementBusy = ref(false)
const managementError = ref('')
let managementGeneration = 0
const chapters = ref<Chapter[]>([])
const knowledgeChapters = computed(() => chapters.value.filter(chapter => (chapter.kind || 'knowledge') === 'knowledge'))
const chapterBusy = ref(false)
const indexRepairBusy = ref(false)
const outlineTasks = useOutlineTasks({
  courseId, managementRevision: () => managementGeneration, blocked: () => chapterBusy.value,
  beforeSelect: () => { noteContext.value = null }, clearFeedback, error: setError,
})
const { outlines, outline, selectedOutlineId, outlineLoading, outlineBusy, activeJob, displayedJob,
  selectOutline, createOutline, retryOutline, exportOutline } = outlineTasks
const materials = useCourseMaterials({
  courseId, courses, chapters, outlines, outline, actionError, success,
  managementRevision: () => managementGeneration, chapterBusy: () => chapterBusy.value,
  indexRepairRequired: () => indexRepairRequired.value, indexRepairBusy: () => indexRepairBusy.value,
  deletionBlocked: () => practiceBusy.value || indexRepairBusy.value || managementBusy.value || chapterBusy.value || chatting.value,
  clearFeedback, error: setError, refreshHealth,
})
const { documents, groups, uploadKind, uploadYear, fileInput, uploadBusy, uploadLabel, documentBusy, dragOver,
  showDrawer, source, sourceLoading, sourceError, loadGroups, refreshDocuments, upload, drop,
   practiceDocumentRenamed, deleteDocument, retryDocument, reparseDocument, preview, closePreview } = materials
let pollTimer: ReturnType<typeof setInterval> | undefined
let pollInFlight = false
let pollCount = 0
let courseGeneration = 0
const readyCount = computed(() => documents.value.filter(document => document.status === 'ready').length)
const spaceMaterialsReady = computed(() => documents.value.some(document => document.status === 'ready' && ['lecture', 'note'].includes(document.kind)))
const unitCount = computed(() => documents.value.reduce((sum, document) => sum + (document.units_with_text || 0), 0))
const confirmed = computed(() => knowledgeChapters.value.length > 0 && chapters.value.every(chapter => chapter.confirmed))
const processingCount = computed(() => documents.value.filter(document => ['queued', 'processing'].includes(document.status)).length)
const modelConfigured = computed(() => health.value?.llm_configured === true)
const indexRepairRequired = computed(() => !!courseId.value && (health.value?.index_repair_courses || []).includes(courseId.value))
const courseSwitchBusy = computed(() => spaceBusy.value || memoryBusy.value || initialLoading.value || courseLoading.value || chatting.value || uploadBusy.value || chapterBusy.value || outlineBusy.value || indexRepairBusy.value || !!documentBusy.value || newSessionBusy.value || creatingCourse.value || managementBusy.value || practiceBusy.value || wrongBusy.value)
const chapterControlsBusy = computed(() => chapterBusy.value || courseLoading.value || indexRepairBusy.value || uploadBusy.value || processingCount.value > 0 || !!documentBusy.value || outlineBusy.value || !!activeJob.value || chatting.value || managementBusy.value)
const canChat = computed(() => !memoryBusy.value && modelConfigured.value && (readyCount.value > 0 || !!currentChatContext.value || !!currentNoteContext.value) && !indexRepairRequired.value && !indexRepairBusy.value && !courseLoading.value && !messagesLoading.value && !managementBusy.value && !chapterBusy.value && !newSessionBusy.value)
const sectionMeta: Record<Section, { title: string; intro: string; label: string }> = {
  library: { title: '课程资料', intro: '上传课件、笔记和真题，确认章节后开始复习。', label: '资料库' },
  outline: { title: '复习笔记', intro: '按章节阅读知识重点，选中不理解的段落即可提问。', label: '复习笔记' },
  space: { title: '复习空间', intro: '写下目标，根据每次作答调整讲解和练习，按自己的节奏继续。', label: '复习空间' },
  practice: { title: '在线练习', intro: '作答与进度自动保存。按原文或模型参考答案判题，答错自动收录。', label: '在线练习' },
  wrong: { title: '错题本', intro: '回顾需要巩固的题目，重新作答，或与右侧助手讨论。', label: '错题本' },
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
          chat.clearSession()
          if (sessions.value[0]) await selectSession(sessions.value[0].id)
        }
      }
    } else if (target.action === 'delete-outline') {
      await outlineTasks.removeOutline(target.id)
    }
    management.value = null
    success.value = target.action.startsWith('delete') ? '已删除。' : target.action === 'new-session' ? '复习会话已创建。' : '名称已保存。'
  } catch (error) { managementError.value = errorText(error); if (target.action === 'delete-course') await refreshHealth() }
  finally { managementBusy.value = false; ++managementGeneration }
}
async function loadCourse(id: string) {
  practiceContext.value = null; wrongContext.value = null; noteContext.value = null
  const generation = ++courseGeneration
  chat.reset(); outlineTasks.reset(); materials.reset()
  clearFeedback(); chapters.value = []
  if (!id) { courseLoading.value = false; return }
  courseLoading.value = true
  try {
    const result = await Promise.all([
      api<Document[]>(`/courses/${id}/documents`), api<Chapter[]>(`/courses/${id}/chapters`),
      api<Session[]>(`/courses/${id}/sessions`), api<Outline[]>(`/courses/${id}/outlines`),
    ])
    if (generation !== courseGeneration) return
    ;[documents.value, chapters.value, sessions.value, outlines.value] = result
    await outlineTasks.loadLatestJob()
    if (generation !== courseGeneration) return
    await loadGroups(id)
    if (generation !== courseGeneration) return
    sessionId.value = sessions.value[0]?.id || ''
    if (sessionId.value) await selectSession(sessionId.value)
    let lastOutline = ''
    try { lastOutline = localStorage.getItem(`review-note-outline:${id}`) || '' } catch { /* Optional reading history. */ }
    selectedOutlineId.value = outlines.value.find(item => item.id === lastOutline)?.id || outlines.value[0]?.id || ''
    if (selectedOutlineId.value) await selectOutline(selectedOutlineId.value)
  } catch (error) { if (generation === courseGeneration) setError(error) }
  finally { if (generation === courseGeneration) courseLoading.value = false }
}
watch(courseId, id => { if (id) localStorage.setItem('review-course-id', id); else localStorage.removeItem('review-course-id'); void loadCourse(id) })
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
function explainNote(context: NoteChatContext) {
  if (chatting.value) return
  noteContext.value = { ...context }
  question.value = '请解释选中的这段笔记，结合课程资料讲清概念、适用条件和推导过程。'
  void sendQuestion()
}
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
    await outlineTasks.pollTasks()
  } catch (error) { if (id === courseId.value && currentCourseGeneration === courseGeneration && generation === managementGeneration) connectionError.value = errorText(error) }
  finally { pollInFlight = false }
}
onMounted(async () => { await initialise(); pollTimer = setInterval(() => void poll(), 2500) })
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer); stopChat() })
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <aside class="sidebar course-sidebar">
      <a class="brand" href="#" @click.prevent="section = 'library'"><div class="brand-mark" aria-hidden="true"><span></span><span></span><span></span></div><div><strong>知页</strong><small>课程复习工作台</small></div></a>
      <div class="workspace-label">你的复习空间</div>
      <nav class="main-nav" aria-label="主要功能"><button v-for="(item, key) in sectionMeta" :key="key" :class="['nav-item', { active: section === key }]" :aria-current="section === key ? 'page' : undefined" @click="section = key as Section"><UiIcon :name="key as Section" /><span>{{ item.label }}</span><span v-if="section === key" class="nav-active-dot" aria-hidden="true"></span></button></nav>
      <div class="sidebar-courses">
        <div class="courses-heading"><strong>全部课程 <span class="count-badge">{{ courses.length }}</span></strong><button class="icon-button small" aria-label="新建课程" title="新建课程" :disabled="courseSwitchBusy" @click="newCourseOpen = !newCourseOpen">＋</button></div>
        <form v-if="newCourseOpen" class="sidebar-course-form" @submit.prevent="createCourse"><input v-model="newCourseName" aria-label="新课程名称" placeholder="输入课程名称" maxlength="100" :disabled="courseSwitchBusy" /><div><button class="button primary small" :disabled="courseSwitchBusy || !newCourseName.trim()">{{ creatingCourse ? '创建中…' : '创建' }}</button><button type="button" class="text-button" :disabled="creatingCourse" @click="newCourseOpen = false">取消</button></div></form>
        <div class="course-list" role="navigation" aria-label="课程切换"><div v-for="item in courses" :key="item.id" :class="['course-list-row', { active: courseId === item.id }]"><button class="course-select" :aria-current="courseId === item.id ? 'page' : undefined" :title="item.name" :disabled="courseSwitchBusy" @click="courseId = item.id">{{ item.name }}</button><div class="course-row-actions"><button class="text-button" :disabled="courseSwitchBusy" :aria-label="`修改课程 ${item.name} 的名称`" @click="renameCourse(item)">改名</button><button class="text-button danger" :disabled="courseSwitchBusy" :aria-label="`删除课程 ${item.name}`" @click="deleteCourse(item)">删除</button></div></div><p v-if="!courses.length" class="muted course-list-empty">还没有课程，点击 ＋ 创建。</p></div>
      </div>
      <div class="sidebar-course"><p class="workspace-label">当前课程</p><strong>{{ course?.name || '先创建一门课程' }}</strong><p v-if="course">{{ readyCount }} 份可用资料 · {{ knowledgeChapters.length }} 个章节</p><div class="sidebar-progress"><span :style="{ width: documents.length ? `${readyCount / documents.length * 100}%` : '0%' }"></span></div></div>
      <div class="sidebar-bottom"><span class="leaf-icon">✳</span><p>让复习有出处，<br />让每一页都连起来。</p><div class="local-badge"><span></span> 本机个人工作台</div></div>
    </aside>
    <div class="workspace managed-workspace">
      <header class="topbar"><div class="breadcrumb"><span class="breadcrumb-course">{{ course?.name || '复习空间' }}</span><span class="breadcrumb-divider">/</span><span>{{ sectionMeta[section].label }}</span></div><div class="topbar-actions"><button v-if="courseId" class="button subtle small" @click="memoryOpen = true">记忆</button><button class="button subtle small" @click="modelSettingsOpen = true"><UiIcon name="settings" />模型配置</button><div :class="['health-pill', { offline: !!connectionError }]" :title="connectionError || '后端服务已连接'"><span></span>{{ initialLoading ? '连接中' : connectionError ? '服务未连接' : '服务已连接' }}</div></div></header>
      <main id="main-content" tabindex="-1" class="main-content" :class="{ 'with-review-chat': courseId && section !== 'library' && section !== 'space', 'review-space-content': courseId && section === 'space' }">
        <div v-if="section !== 'space' || !courseId" class="page-heading"><div><p class="course-caption">{{ course?.name || '从一门课程开始' }}</p><h1>{{ sectionMeta[section].title }}</h1><p>{{ sectionMeta[section].intro }}</p></div><div v-if="course" class="heading-note"><span>当前课程</span><strong>{{ readyCount }} 份资料可用于复习</strong></div></div>
        <div v-if="connectionError" class="notice error" role="alert"><strong>无法连接服务</strong><span>{{ connectionError }}</span><button class="text-button" @click="initialise">重新连接</button></div>
        <div v-else-if="health && !modelConfigured" class="notice warning"><strong>聊天模型尚未配置</strong><span>资料上传与目录整理可以使用。点击“模型配置”，添加模型并填写 API Key 或登录订阅账号，保存后即可生成回答和复习笔记。</span><button class="text-button" @click="modelSettingsOpen = true">配置模型</button></div>
        <div v-if="health && !health.embedding_ready && documents.length && !indexRepairRequired" class="notice info"><span>检索模型将在需要时加载。已下载的模型直接从本地读取；尚未下载时需要联网获取模型文件。</span></div>
        <div v-if="indexRepairRequired" class="notice warning"><strong>本地检索索引需要重新建立</strong><span>{{ indexRepairBusy ? '正在用本地向量模型重新处理已保存资料，请等待完成。首次加载模型可能较慢；此操作不会调用聊天模型。' : '资料、章节目录和复习笔记仍保留。点击重新建立索引后再使用问答；复习笔记仍可正常生成。' }}</span><button class="button subtle small" :disabled="indexRepairBusy || chapterBusy || !!activeJob || chatting" @click="repairIndex">{{ indexRepairBusy ? '正在重新建立…' : '重新建立索引' }}</button></div>
        <div v-if="actionError" class="notice error feedback" role="alert"><span>{{ actionError }}</span><button class="icon-button small" aria-label="关闭错误提示" @click="actionError = ''">×</button></div>
        <div v-if="success" class="notice success feedback" role="status"><span>{{ success }}</span><button class="icon-button small" aria-label="关闭成功提示" @click="success = ''">×</button></div>
        <div v-if="initialLoading || courseLoading" class="loading-bar">正在加载复习空间…</div>
        <section v-if="!courseId && !initialLoading" class="panel welcome-panel"><div class="welcome-art" aria-hidden="true"><span>资料</span><span>笔记</span><span>练习</span></div><h2>从第一门课程开始</h2><p>把课件、笔记和真题放在一起，随时阅读、练习和提问。</p><form @submit.prevent="createCourse"><input v-model="newCourseName" aria-label="新课程名称" placeholder="例如：数据结构" maxlength="100" /><button class="button primary" :disabled="creatingCourse || !newCourseName.trim()">{{ creatingCourse ? '创建中…' : '创建课程' }}</button></form></section>

        <template v-if="courseId && section === 'library'">
          <div class="stats-row"><div class="stat-card"><span>课程资料</span><strong>{{ documents.length }}<small>份</small></strong><p>{{ processingCount ? `${processingCount} 份正在处理` : '按类型组织课程内容' }}</p></div><div class="stat-card"><span>可用于复习</span><strong>{{ readyCount }}<small>份</small></strong><p>解析完成并已建立索引</p></div><div class="stat-card"><span>已提取原文</span><strong>{{ unitCount }}<small>处</small></strong><p>页、幻灯片或文档内容位置</p></div><div class="stat-card accent"><span>章节路线</span><strong>{{ knowledgeChapters.length }}<small>章</small></strong><p>{{ confirmed ? '目录已确认，准备好生成复习笔记' : '下一步：确认章节目录' }}</p></div></div>
          <section class="panel"><div class="section-top"><div><h2>上传与管理资料</h2><p class="muted">自动提取正文、图片文字和公式，保留原文位置。</p></div><span class="badge neutral">支持批量上传</span></div>
            <div class="upload-options"><label>资料类型<select v-model="uploadKind" :disabled="uploadBusy"><option v-for="(label, key) in uploadKindLabels" :key="key" :value="key">{{ label }}</option></select></label><label v-if="uploadKind === 'exam' || uploadKind === 'exercise' || uploadKind === 'answer'">年份 <span class="muted">（可选）</span><input v-model="uploadYear" type="text" inputmode="numeric" maxlength="4" placeholder="例如 2024" :disabled="uploadBusy" /></label><p>同批文件使用相同类型；不同类型请分批上传。</p></div>
            <div :class="['upload-area', { dragging: dragOver, busy: uploadBusy }]" @dragover.prevent="dragOver = true" @dragleave.prevent="dragOver = false" @drop.prevent="drop"><div class="upload-icon"><UiIcon name="upload" /></div><div class="upload-description"><h3>{{ uploadBusy ? '正在接收资料…' : '拖入课程资料' }}</h3><p>{{ uploadLabel || (indexRepairRequired ? '请先重新建立索引，再加入新资料' : '可以一次选择多个文件') }}</p><small>{{ uploadFormatsLabel }}</small></div><button class="button primary" :disabled="uploadBusy || indexRepairRequired || indexRepairBusy" @click="fileInput?.click()">{{ uploadBusy ? '上传中…' : '选择文件' }}</button><input ref="fileInput" type="file" multiple :accept="uploadAccept" hidden @change="upload(($event.target as HTMLInputElement).files || [])" /></div>
            <div class="list-heading"><strong>全部资料 <span class="count-badge">{{ documents.length }}</span></strong><div class="inline-actions"><button v-if="readyCount && !indexRepairRequired" class="text-button" :disabled="indexRepairBusy || chapterBusy || !!activeJob || chatting" @click="repairIndex">{{ indexRepairBusy ? '正在修复…' : '修复索引' }}</button><button class="text-button" @click="refreshDocuments().catch(setError)">刷新状态 ↻</button></div></div>
            <div v-if="!documents.length" class="empty-state compact"><p>还没有课程资料</p><span>建议先上传少量资料，检查解析内容后再逐批加入。</span></div>
            <div v-for="document in documents" :key="document.id" class="document-row"><div :class="['file-type', document.filename.split('.').pop()?.toLowerCase()]">{{ document.filename.split('.').pop()?.toUpperCase() }}</div><div class="document-info"><h3>{{ document.filename }}</h3><div class="document-meta"><span>{{ kindLabels[document.kind] }}</span><span v-if="document.year">{{ document.year }} 年</span><span v-if="document.units_total">{{ document.units_with_text }} / {{ document.units_total }} 处有内容</span></div><p v-if="document.error" class="document-error">{{ document.error }}</p></div><span :class="['badge', document.status]">{{ statusLabels[document.status] || document.status }}</span><div class="document-actions"><button class="text-button" :disabled="['queued', 'processing'].includes(document.status)" @click="preview(document)">原文预览</button><button v-if="document.status === 'ready'" class="text-button" :disabled="!!documentBusy || uploadBusy || indexRepairRequired || indexRepairBusy || !!activeJob || processingCount > 0" @click="reparseDocument(document.id)">重新解析（OCR）</button><button v-if="document.status === 'failed'" class="text-button" :disabled="!!documentBusy || indexRepairRequired || indexRepairBusy || !!activeJob" @click="retryDocument(document.id)">重试</button><button class="text-button danger" :disabled="!!documentBusy || uploadBusy || practiceBusy || indexRepairBusy || managementBusy || chapterBusy || chatting || ['queued', 'processing'].includes(document.status)" @click="deleteDocument(document)">删除</button></div></div>
          </section>
          <ChapterEditor :key="courseId" :chapters="chapters" :groups="groups" :busy="chapterControlsBusy" @save="saveChapters" @edit="editChapters" @delete="deleteChapter" @draft="draftChapters" />
          <div v-if="confirmed" class="next-step"><span>复习路线已经准备好了。</span><button class="text-button" @click="section = 'outline'">生成复习笔记</button></div>
        </template>

        <KeepAlive v-if="courseId" :key="`space:${courseId}`">
          <ReviewSpacePanel v-if="section === 'space'" :course-id="courseId" :course-name="course?.name || '当前课程'" :chapters="chapters" :model-configured="modelConfigured" :blocked="!spaceMaterialsReady || courseLoading || indexRepairRequired || indexRepairBusy || processingCount > 0 || uploadBusy || managementBusy || !!documentBusy" @busy="spaceBusy = $event" @change="wrongRevision++" />
        </KeepAlive>

        <div v-if="courseId" v-show="section !== 'library' && section !== 'space'" class="study-workspace">
          <div class="study-content">
        <template v-if="section === 'outline'">
          <section class="outline-intro panel"><div class="outline-illustration" aria-hidden="true"><UiIcon name="outline" /></div><div><h2>按课程资料生成笔记</h2><p>整理概念、公式和方法，用表格比较容易混淆的知识。</p><div class="outline-checks"><span :class="{ checked: readyCount > 0 }">{{ readyCount > 0 ? '✓' : '○' }} {{ readyCount }} 份可用资料</span><span :class="{ checked: confirmed }">{{ confirmed ? '✓' : '○' }} {{ confirmed ? '章节已确认' : '章节待确认' }}</span></div></div><button class="button primary" :disabled="!confirmed || !readyCount || !modelConfigured || indexRepairBusy || outlineBusy || !!activeJob || !!processingCount" @click="createOutline">{{ activeJob ? '正在生成…' : outlineBusy ? '创建任务…' : '生成复习笔记' }}</button></section>
          <div v-if="!confirmed" class="notice info"><span>生成前，请先在资料库确认章节目录与资料归属。</span><button class="text-button" @click="section = 'library'">确认章节目录</button></div>
          <div v-if="processingCount" class="notice info">还有 {{ processingCount }} 份资料正在处理，请待入库完成后再生成复习笔记。</div>
          <section v-if="displayedJob" class="panel job-panel"><div class="section-top"><div><h3>{{ statusLabels[displayedJob.status] || displayedJob.status }}</h3><p class="muted">{{ displayedJob.stage || '准备整理课程资料' }}</p></div><strong>{{ Math.round(displayedJob.progress || 0) }}%</strong></div><div class="progress-track"><span :style="{ width: `${Math.min(100, Math.max(0, displayedJob.progress || 0))}%` }"></span></div><p v-if="displayedJob.error" class="document-error">{{ displayedJob.error }}</p><button v-if="['failed', 'interrupted'].includes(displayedJob.status)" class="button subtle" :disabled="outlineBusy || !!activeJob || !modelConfigured || indexRepairRequired || indexRepairBusy" @click="retryOutline">重试未完成章节</button></section>
          <section class="panel outline-content-panel"><div class="section-top"><div><h2>阅读笔记</h2></div><div class="inline-actions"><select v-if="outlines.length" :value="selectedOutlineId" aria-label="选择历史笔记" :disabled="outlineBusy || outlineLoading || managementBusy" @change="selectOutline(($event.target as HTMLSelectElement).value)"><option v-for="item in outlines" :key="item.id" :value="item.id">{{ date(item.created_at) }} · {{ statusLabels[item.status] || item.status }}{{ item.stale ? ' · 已过期' : '' }}</option></select><button v-if="outline?.stale" class="button subtle danger" :disabled="outlineLoading || outlineBusy || !!activeJob || managementBusy" @click="deleteOldOutline">删除过期笔记</button><button class="button subtle" :disabled="!outline?.markdown" @click="exportOutline">下载笔记 .md</button></div></div>
            <div v-if="outline?.stale" class="notice warning"><strong>这份笔记已过期</strong><span>资料或章节已经变化。可重新生成，也可点击“删除过期笔记”清除当前选中的旧版本。</span></div>
            <div v-if="outline?.status === 'completed' && outline.current_style && outline.style !== outline.current_style" class="notice info">这是之前生成的笔记。点击上方生成按钮，可重新整理；英文资料中的关键名词会同时保留英文。</div>
            <p v-if="outlineLoading" class="loading-bar">正在读取复习笔记…</p><NotesReader v-else-if="outline?.markdown" :key="outline.id" :course-id="courseId" :outline-id="outline.id" :content="outline.markdown" :discussion-busy="chatting || messagesLoading || courseLoading || managementBusy" @explain="explainNote" /><div v-else class="empty-state outline-empty"><div class="empty-symbol"><UiIcon name="outline" /></div><h3>{{ activeJob ? '正在把资料整理成复习笔记' : '复习笔记从这里开始' }}</h3><p>{{ activeJob ? '正在筛选重点、合并主题，章节完成后会保存。' : '上传资料、确认目录，再按主题生成复习笔记。' }}</p><div class="outline-tags"><span>概念与原理</span><span>公式与条件</span><span>算法步骤</span><span>比较表格</span></div></div>
          </section>
        </template>

        <KeepAlive v-if="courseId" :key="courseId">
          <PracticePanel v-if="section === 'practice'" :course-id="courseId" :documents="documents" :chapters="knowledgeChapters" :model-configured="modelConfigured" :wrong-revision="wrongRevision" :deletion-busy="!!documentBusy || uploadBusy || indexRepairBusy || managementBusy || chapterBusy" :deleting-document-id="documentBusy" :upload-blocked="indexRepairRequired || indexRepairBusy" @delete-document="deleteDocument" @rename-document="practiceDocumentRenamed" @refresh="refreshDocuments().catch(setError)" @busy="practiceBusy = $event" @model-busy="practiceModelBusy = $event" @wrong-change="wrongRevision++" @context-change="practiceContext = $event" />
        </KeepAlive>

        <WrongQuestionsPanel v-if="courseId && section === 'wrong'" :key="courseId" :course-id="courseId" :revision="wrongRevision" :discussion-busy="chatting || messagesLoading || courseLoading || managementBusy" :active-question-id="wrongContext?.wrong_question_id || ''" @busy="wrongBusy = $event" @change="wrongRevision++" @context-change="wrongContext = $event" />

          </div>
          <aside class="review-chat panel review-chat-floating" aria-label="复习对话">
            <div class="chat-main">
              <header class="chat-top"><div><span class="chat-avatar">知</span><strong>复习对话</strong></div><button class="text-button" :disabled="chatting || newSessionBusy || courseLoading || managementBusy" @click="nameNewSession">＋ 新会话</button></header>
              <div class="chat-session-bar"><select :value="sessionId" aria-label="选择复习会话" :disabled="chatting || newSessionBusy || messagesLoading || managementBusy" @change="selectSession(($event.target as HTMLSelectElement).value)"><option v-if="!sessionId" value="">开始新的复习对话</option><option v-for="item in sessions" :key="item.id" :value="item.id">{{ item.title }}</option></select><button v-if="currentSession" class="text-button" :disabled="chatting || newSessionBusy || managementBusy" @click="renameSession(currentSession)">改名</button><button v-if="currentSession" class="text-button danger" :disabled="chatting || newSessionBusy || managementBusy" @click="deleteSession(currentSession)">删除</button></div>
              <div v-if="currentChatContext" class="chat-question-context" role="status"><strong>正在讨论第 {{ currentChatContext.number }} 题</strong><span :title="currentChatContext.filename">{{ currentChatContext.filename }}</span></div>
              <div v-if="currentNoteContext" class="chat-question-context" role="status"><strong>正在解释 · {{ currentNoteContext.heading }}</strong><span :title="currentNoteContext.selected_text">{{ currentNoteContext.selected_text }}</span><button type="button" class="text-button" :disabled="chatting" @click="noteContext = null">取消段落上下文</button></div>
              <button v-if="section === 'wrong' && currentChatContext" type="button" class="text-button chat-clear-context" :disabled="chatting" @click="wrongContext = null">取消题目上下文</button>
              <div ref="chatBox" class="message-list" aria-live="polite"><p v-if="messagesLoading" class="muted">正在读取历史会话…</p><div v-else-if="!messages.length" class="chat-welcome"><div class="chat-welcome-mark"><UiIcon name="chat" /></div><h2>{{ currentChatContext ? '这道题，哪里还没弄懂？' : currentNoteContext ? '一起读懂这段笔记' : '在这里理清思路' }}</h2><p>{{ currentChatContext ? '已带上当前题目和你的作答，直接提问即可。' : currentNoteContext ? '已带上选中的笔记段落，可以继续追问概念、公式或例子。' : '可以提问课程概念、比较解题方法，也可以继续追问。回答会结合当前课程资料。' }}</p></div>
                <article v-for="message in messages" :key="message.id" :class="['message', message.role]"><div class="message-avatar">{{ message.role === 'user' ? '我' : '知' }}</div><div class="message-body"><div class="message-label">{{ message.role === 'user' ? '你' : '复习助手' }}<span v-if="message.pending" class="streaming-label">正在整理…</span><span v-if="message.incomplete" class="incomplete-label">未完成 · 仅本地保留</span></div><div v-if="message.role === 'user'" class="user-text"><small v-if="message.practice_context" class="message-question-label">第 {{ message.practice_context.number }} 题 · {{ message.practice_context.filename }}</small><small v-if="message.note_context" class="message-question-label">笔记 · {{ message.note_context.heading }} · {{ message.note_context.selected_text.slice(0, 90) }}</small>{{ message.content }}</div><MarkdownContent v-else-if="message.content" :content="message.content" hide-citations /><div v-else-if="message.pending" class="thinking"><span></span><span></span><span></span><small>{{ chatStatus }}</small></div><p v-else-if="message.incomplete" class="muted">本次没有收到完整回答，可以重新提问。</p><TokenUsage v-if="message.role === 'assistant' && !message.pending" :usage="message.token_usage" /></div></article>
              </div>
              <form class="chat-composer" @submit.prevent="sendQuestion"><div v-if="chatting" class="chat-status"><span class="pulse-dot"></span>{{ chatStatus || '正在生成回答…' }}<button type="button" class="text-button" @click="stopChat()">停止生成</button></div><div v-if="indexRepairRequired" class="composer-notice">课程检索索引需要修复，请点击上方“重新建立索引”。</div><div v-else-if="!modelConfigured" class="composer-notice">配置聊天模型后，即可使用复习问答。</div><div v-else-if="!readyCount && !currentChatContext && !currentNoteContext" class="composer-notice">先上传并处理至少一份资料，再开始提问。</div><div class="composer-input"><textarea v-model="question" rows="2" :disabled="!canChat || chatting" :placeholder="currentChatContext ? '直接问这道题，也可以结合你的作答追问…' : currentNoteContext ? '围绕选中的笔记段落继续追问…' : '围绕课程资料提问，或继续追问…'" aria-label="复习问题" @keydown="keyboard"></textarea><button class="send-button" aria-label="发送问题" :disabled="!question.trim() || !canChat || chatting">↑</button></div><div class="composer-footer"><span>Enter 发送 · Shift + Enter 换行</span><span>基于当前课程资料回答</span></div></form>
            </div></aside>
        </div>
        <footer class="page-footer"><span>知页 · 课程复习工作台</span><span>资料与学习记录保存在本机</span></footer>
      </main>
    </div>
    <SourceDrawer v-if="showDrawer" :source="source" :loading="sourceLoading" :error="sourceError" @close="closePreview" />
    <MemorySettings v-if="memoryOpen && courseId" :course-id="courseId" :course-name="course?.name || '当前课程'" :session-id="sessionId" :active="chatting" @close="memoryOpen = false" @busy="memoryBusy = $event" />
    <ModelSettings v-if="modelSettingsOpen" :active="spaceBusy || chatting || !!activeJob || practiceModelBusy" @close="modelSettingsOpen = false" @saved="refreshHealth" />
    <ManagementDialog v-if="management" :key="`${management.action}:${management.id}`" :title="management.title" :description="management.description" :initial-name="management.initialName" :confirm-text="management.confirmText" :danger="management.danger" :busy="managementBusy" :error="managementError" @close="management = null" @submit="submitManagement" />
  </div>
</template>
