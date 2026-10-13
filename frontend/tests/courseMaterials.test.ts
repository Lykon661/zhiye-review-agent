import { afterEach, expect, it, vi } from 'vitest'
import { ref } from 'vue'
import { api } from '../src/api'
import { useCourseMaterials } from '../src/useCourseMaterials'
import type { Block, BlockReference, Chapter, Course, Document, Outline } from '../src/types'

vi.mock('../src/api', () => ({ api: vi.fn() }))
afterEach(() => vi.clearAllMocks())

function createMaterials(courseId = ref('course')) {
  return useCourseMaterials({
    courseId, courses: ref<Course[]>([]), chapters: ref<Chapter[]>([]),
    outlines: ref<Outline[]>([]), outline: ref<Outline | null>(null), actionError: ref(''), success: ref(''),
    managementRevision: () => 0, chapterBusy: () => false, indexRepairRequired: () => false,
    indexRepairBusy: () => false, deletionBlocked: () => false,
    clearFeedback: () => {}, error: () => {}, refreshHealth: async () => {},
  })
}

it('loads ready document block references in one course request and keeps empty groups', async () => {
  const materials = createMaterials()
  const ready = { id: 'ready', status: 'ready' } as Document
  const excluded = { id: 'excluded', status: 'ready', chapter_excluded: true } as Document
  materials.documents.value = [ready, excluded, { id: 'failed', status: 'failed' } as Document]
  vi.mocked(api).mockResolvedValueOnce([
    { id: 'assigned', document_id: 'ready', chapter_id: 'chapter' },
    { id: 'unassigned', document_id: 'ready', chapter_id: null },
  ])

  await materials.loadGroups()

  expect(api).toHaveBeenCalledExactlyOnceWith('/courses/course/block-references')
  expect(materials.groups.value).toEqual([
    { document: ready, blocks: [
      { id: 'assigned', document_id: 'ready', chapter_id: 'chapter' },
      { id: 'unassigned', document_id: 'ready', chapter_id: null },
    ] },
    { document: excluded, blocks: [] },
  ])
})

it('loads full original text when opening a document preview', async () => {
  const materials = createMaterials()
  vi.mocked(api).mockResolvedValueOnce([
    { id: 'block', text: 'Original lecture text', locator: { label: '第一页' }, heading: null } as Block,
  ])

  await materials.preview({ id: 'document', filename: 'lecture.txt', kind: 'lecture' } as Document)

  expect(api).toHaveBeenCalledExactlyOnceWith('/documents/document/blocks')
  expect(materials.source.value?.text).toContain('Original lecture text')
})

it('ignores block references returned after switching courses', async () => {
  const courseId = ref('course')
  const materials = createMaterials(courseId)
  materials.documents.value = [{ id: 'old-document', status: 'ready' } as Document]
  let resolve!: (blocks: BlockReference[]) => void
  vi.mocked(api).mockReturnValueOnce(new Promise<BlockReference[]>(done => { resolve = done }))
  const loading = materials.loadGroups()
  courseId.value = 'next-course'
  materials.reset()
  const document = { id: 'next-document', status: 'ready' } as Document
  materials.documents.value = [document]
  vi.mocked(api).mockResolvedValueOnce([{ id: 'next-block', document_id: document.id, chapter_id: null }])
  await materials.loadGroups()

  resolve([{ id: 'old-block', document_id: 'old-document', chapter_id: null }])
  await loading

  expect(materials.groups.value).toEqual([
    { document, blocks: [{ id: 'next-block', document_id: document.id, chapter_id: null }] },
  ])
})

it('closing a preview prevents its late response from updating the drawer', async () => {
  const materials = useCourseMaterials({
    courseId: ref('course'), courses: ref<Course[]>([]), chapters: ref<Chapter[]>([]),
    outlines: ref<Outline[]>([]), outline: ref<Outline | null>(null), actionError: ref(''), success: ref(''),
    managementRevision: () => 0, chapterBusy: () => false, indexRepairRequired: () => false,
    indexRepairBusy: () => false, deletionBlocked: () => false,
    clearFeedback: () => {}, error: () => {}, refreshHealth: async () => {},
  })
  let resolve!: (blocks: Block[]) => void
  vi.mocked(api).mockReturnValueOnce(new Promise<Block[]>(done => { resolve = done }))
  const loading = materials.preview({ id: 'document', filename: 'lecture.txt', kind: 'lecture' } as Document)
  materials.closePreview()
  resolve([{ id: 'block', text: 'late text', locator: { label: '第一页' } } as Block])
  await loading
  expect(materials.showDrawer.value).toBe(false)
  expect(materials.sourceLoading.value).toBe(false)
  expect(materials.source.value).toBeNull()
})
