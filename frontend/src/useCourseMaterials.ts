import { ref, type Ref } from 'vue'
import { api } from './api'
import type { Block, BlockReference, Chapter, Course, Document, DocumentKind, Outline, Source } from './types'
import { supportsUpload, uploadFormatsLabel } from './uploadFormats'

interface Options {
  courseId: Ref<string>
  courses: Ref<Course[]>
  chapters: Ref<Chapter[]>
  outlines: Ref<Outline[]>
  outline: Ref<Outline | null>
  actionError: Ref<string>
  success: Ref<string>
  managementRevision: () => number
  chapterBusy: () => boolean
  indexRepairRequired: () => boolean
  indexRepairBusy: () => boolean
  deletionBlocked: () => boolean
  clearFeedback: () => void
  error: (reason: unknown) => void
  refreshHealth: () => Promise<void>
}
export function useCourseMaterials(options: Options) {
  const { courseId, courses, chapters, outlines, outline, actionError, success } = options
  const clearFeedback = options.clearFeedback, setError = options.error
  const errorText = (reason: unknown) => reason instanceof Error ? reason.message : String(reason)
  const documents = ref<Document[]>([])
  const groups = ref<{ document: Document; blocks: BlockReference[] }[]>([])
  const uploadKind = ref<DocumentKind>('lecture'), uploadYear = ref('')
  const fileInput = ref<HTMLInputElement | null>(null)
  const uploadBusy = ref(false), uploadLabel = ref(''), documentBusy = ref<string | null>(null), dragOver = ref(false)
  const showDrawer = ref(false), source = ref<Source | null>(null), sourceLoading = ref(false), sourceError = ref('')
  let epoch = 0, sourceGeneration = 0, groupsGeneration = 0
  function reset() {
    ++epoch; ++sourceGeneration
    documents.value = []; groups.value = []; showDrawer.value = false
    source.value = null; sourceLoading.value = false; sourceError.value = ''
  }
  async function loadGroups(id = courseId.value) {
    const version = epoch, generation = ++groupsGeneration
    if (!id) return
    const candidates = documents.value.filter(document => document.status === 'ready')
    const references = candidates.length ? await api<BlockReference[]>(`/courses/${id}/block-references`) : []
    if (courseId.value !== id || version !== epoch || generation !== groupsGeneration) return
    const byDocument = new Map<string, BlockReference[]>()
    for (const block of references) {
      const blocks = byDocument.get(block.document_id)
      if (blocks) blocks.push(block)
      else byDocument.set(block.document_id, [block])
    }
    groups.value = candidates.map(document => ({ document, blocks: byDocument.get(document.id) || [] }))
  }
  async function refreshDocuments() {
    const id = courseId.value
    const generation = options.managementRevision(), version = epoch
    if (!id) return
    const next = await api<Document[]>(`/courses/${id}/documents`)
    if (id !== courseId.value || generation !== options.managementRevision() || version !== epoch) return
    const signature = (items: Document[]) => items.map(item => `${item.id}:${item.status}:${!!item.chapter_excluded}`).join('|')
    const changed = signature(documents.value) !== signature(next)
    documents.value = next
    if (changed) {
      const [updatedChapters, updatedCourses, updatedOutlines] = await Promise.all([api<Chapter[]>(`/courses/${id}/chapters`), api<Course[]>('/courses'), api<Outline[]>(`/courses/${id}/outlines`)])
      if (id !== courseId.value || generation !== options.managementRevision() || version !== epoch) return
      chapters.value = updatedChapters; courses.value = updatedCourses; outlines.value = updatedOutlines
      if (outline.value) outline.value.stale = updatedOutlines.find(value => value.id === outline.value?.id)?.stale ?? outline.value.stale
      await loadGroups(id)
    }
  }
  async function upload(files: FileList | File[]) {
    if (!courseId.value || uploadBusy.value || options.chapterBusy() || !files.length) return
    if (options.indexRepairRequired() || options.indexRepairBusy()) { actionError.value = '请先重新建立索引，再上传新资料。'; return }
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
  function practiceDocumentRenamed(document: Document) {
    documents.value = documents.value.map(item => item.id === document.id ? { ...item, practice_title: document.practice_title } : item)
  }
  async function deleteDocument(document: Document) {
    if (documentBusy.value || uploadBusy.value || options.deletionBlocked() || ['queued', 'processing'].includes(document.status)) return
    const isGenerated = document.kind === 'generated'
    const isPaper = document.kind === 'exam' || document.kind === 'exercise'
    const description = isGenerated ? '会删除这组 AI 题目及作答进度，无法恢复。已收录的错题会保留。' : isPaper
      ? '会删除这份试卷文件、练习题目和检索内容，无法恢复。已收录的错题会保留，旧笔记将标记为过期。'
      : '对应的检索内容也会删除，旧笔记将标记为过期。'
    if (!window.confirm(`删除“${document.practice_title || document.filename}”？${description}`)) return
    clearFeedback(); documentBusy.value = document.id
    try { await api(`/documents/${document.id}`, { method: 'DELETE' }); await refreshDocuments(); success.value = isGenerated ? 'AI 练习已删除。' : isPaper ? '试卷已删除。' : '资料已删除。' }
    catch (error) { setError(error); await options.refreshHealth() }
    finally { documentBusy.value = null }
  }
  async function retryDocument(id: string) {
    if (options.chapterBusy()) return
    clearFeedback(); documentBusy.value = id
    try { await api(`/documents/${id}/retry`, { method: 'POST' }); await refreshDocuments() }
    catch (error) { setError(error) }
    finally { documentBusy.value = null }
  }
  async function reparseDocument(id: string) {
    if (options.chapterBusy()) return
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
  function closePreview() {
    ++sourceGeneration
    showDrawer.value = false; sourceLoading.value = false
  }

  return { documents, groups, uploadKind, uploadYear, fileInput, uploadBusy, uploadLabel, documentBusy, dragOver,
    showDrawer, source, sourceLoading, sourceError, reset, loadGroups, refreshDocuments, upload, drop,
    practiceDocumentRenamed, deleteDocument, retryDocument, reparseDocument, preview, closePreview }
}
