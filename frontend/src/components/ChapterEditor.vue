<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { BlockReference, Chapter, ChapterDraft, Document } from '../types'
interface BlockGroup { document: Document; blocks: BlockReference[] }
const props = defineProps<{ chapters: Chapter[]; groups: BlockGroup[]; busy: boolean }>()
type EditAction = 'rename' | 'merge'
interface DragState { id: string; pointerId: number; startY: number; x: number; y: number; active: boolean; handle: HTMLElement }
const emit = defineEmits<{ save: [chapters: ChapterDraft[]]; edit: [chapters: ChapterDraft[]]; delete: [id: string]; draft: [] }>()
const error = ref('')
const editor = ref<{ id: string; action: EditAction } | null>(null)
const title = ref('')
const targetId = ref('')
const editorControl = ref<HTMLInputElement | HTMLSelectElement | null>(null)
const actionLabels: Record<EditAction, string> = { rename: '重命名章节', merge: '合并章节' }
const knowledgeChapters = computed(() => props.chapters.filter(chapter => (chapter.kind || 'knowledge') === 'knowledge'))
const tableBody = ref<HTMLElement | null>(null)
const dragged = ref<DragState | null>(null)
const previewOrder = ref<string[]>([])
const pendingOrder = ref<string[] | null>(null)
const hasExcludedChapters = computed(() => props.groups.some(group => group.document.chapter_excluded))
let scrollFrame: number | null = null
const displayedChapters = computed(() => {
  const order = pendingOrder.value || previewOrder.value
  if (!order.length) return knowledgeChapters.value
  const byId = new Map(knowledgeChapters.value.map(chapter => [chapter.id, chapter]))
  return order.map(id => byId.get(id)).filter((chapter): chapter is Chapter => !!chapter)
})
const dragTitle = computed(() => knowledgeChapters.value.find(chapter => chapter.id === dragged.value?.id)?.title || '')
const dragPreviewStyle = computed(() => ({
  left: `${Math.max(8, Math.min((dragged.value?.x || 0) + 16, window.innerWidth - 270))}px`,
  top: `${Math.max(8, Math.min((dragged.value?.y || 0) + 12, window.innerHeight - 60))}px`,
}))
const editingChapter = computed(() => knowledgeChapters.value.find(chapter => chapter.id === editor.value?.id))
const mergeTargets = computed(() => knowledgeChapters.value.filter(chapter => chapter.id !== editor.value?.id && chapter.block_count > 0))
const unassigned = computed(() => {
  const assigned = new Set(props.chapters.flatMap(chapter => chapter.block_ids))
  return props.groups.filter(group => !group.document.chapter_excluded).flatMap(group => group.blocks).filter(block => !assigned.has(block.id)).length
})
const canSubmit = computed(() => {
  if (props.busy || !editor.value || !editingChapter.value) return false
  if (editor.value.action === 'rename') return !!title.value.trim() && title.value.trim() !== editingChapter.value.title
  return mergeTargets.value.some(chapter => chapter.id === targetId.value)
})
watch(() => props.chapters, () => { cancelDrag(); pendingOrder.value = null; editor.value = null; error.value = '' })
watch(() => props.busy, busy => { if (busy) cancelDrag(); else pendingOrder.value = null })
function payload(chapters = props.chapters): ChapterDraft[] {
  // Auxiliary file mappings remain in every update for note generation and retrieval.
  return chapters.map((chapter, position) => ({ id: chapter.id, title: chapter.title, position, block_ids: [...chapter.block_ids] }))
}
function save() {
  if (props.busy || dragged.value) return
  error.value = ''
  if (unassigned.value) { error.value = '目录与文件尚未对应，请先点击“按文件重新整理”。'; return }
  emit('save', payload())
}
function draft() {
  if (props.busy || dragged.value) return
  if ((props.chapters.length || hasExcludedChapters.value) && !window.confirm('按文件重新整理会恢复已删除的章节、文件名和默认顺序，并拆开已合并的章节。继续重新整理？')) return
  editor.value = null; error.value = ''; emit('draft')
}
async function openEditor(chapter: Chapter, action: EditAction) {
  if (props.busy || dragged.value) return
  error.value = ''; editor.value = { id: chapter.id, action }
  title.value = chapter.title
  targetId.value = ''
  await nextTick()
  editorControl.value?.focus()
  if (editorControl.value instanceof HTMLInputElement) editorControl.value.select()
}
function cancel() { if (!props.busy) editor.value = null }
function submitEdit() {
  if (!canSubmit.value || !editor.value || !editingChapter.value) return
  const { id, action } = editor.value
  let chapters = [...props.chapters]
  if (action === 'rename') {
    chapters = chapters.map(chapter => chapter.id === id ? { ...chapter, title: title.value.trim() } : chapter)
  } else {
    const blockIds = editingChapter.value.block_ids
    chapters = chapters.filter(chapter => chapter.id !== id).map(chapter => chapter.id === targetId.value
      ? { ...chapter, block_ids: [...new Set([...chapter.block_ids, ...blockIds])] } : chapter)
  }
  emit('edit', payload(chapters))
}
function removeChapter(chapter: Chapter) {
  if (props.busy || dragged.value) return
  if (!window.confirm(`删除章节“${chapter.title}”？\n本章将从目录和新生成的笔记中移除，原资料仍保留在资料库并可用于问答。点击“按文件重新整理”可恢复目录。`)) return
  editor.value = null; error.value = ''; emit('delete', chapter.id)
}
function saveOrder(ordered: Chapter[]) {
  if (props.busy || ordered.every((chapter, index) => chapter.id === knowledgeChapters.value[index]?.id)) return
  pendingOrder.value = ordered.map(chapter => chapter.id)
  let index = 0
  emit('edit', payload(props.chapters.map(chapter => (chapter.kind || 'knowledge') === 'knowledge' ? ordered[index++]! : chapter)))
}
function moveWithKeyboard(id: string, direction: number) {
  if (props.busy || dragged.value) return
  const ordered = [...knowledgeChapters.value]
  const current = ordered.findIndex(chapter => chapter.id === id)
  const next = current + direction
  if (current < 0 || next < 0 || next >= ordered.length) return
  const [chapter] = ordered.splice(current, 1)
  if (!chapter) return
  ordered.splice(next, 0, chapter)
  editor.value = null; saveOrder(ordered)
}
function startDrag(event: PointerEvent, chapter: Chapter) {
  if (props.busy || dragged.value || pendingOrder.value || knowledgeChapters.value.length < 2 || event.button !== 0) return
  const handle = event.currentTarget as HTMLElement
  handle.setPointerCapture(event.pointerId)
  previewOrder.value = knowledgeChapters.value.map(item => item.id)
  dragged.value = { id: chapter.id, pointerId: event.pointerId, startY: event.clientY, x: event.clientX, y: event.clientY, active: false, handle }
}
function updateDragOrder() {
  const drag = dragged.value
  if (!drag?.active) return
  const rows = Array.from(tableBody.value?.querySelectorAll<HTMLElement>('tr[data-chapter-id]') || [])
    .filter(row => row.dataset.chapterId !== drag.id)
  const order = rows.map(row => row.dataset.chapterId!)
  const position = rows.reduce((count, row) => {
    const rect = row.getBoundingClientRect()
    return count + (drag.y > rect.top + rect.height / 2 ? 1 : 0)
  }, 0)
  order.splice(position, 0, drag.id)
  if (order.length === knowledgeChapters.value.length && order.some((id, index) => id !== previewOrder.value[index])) previewOrder.value = order
}
function scrollWhileDragging() {
  const drag = dragged.value
  if (!drag?.active) { scrollFrame = null; return }
  const edge = 70
  const delta = drag.y < edge ? -Math.ceil((edge - drag.y) / 5)
    : drag.y > window.innerHeight - edge ? Math.ceil((drag.y - window.innerHeight + edge) / 5) : 0
  if (delta) { window.scrollBy(0, Math.max(-20, Math.min(20, delta))); updateDragOrder() }
  scrollFrame = window.requestAnimationFrame(scrollWhileDragging)
}
function moveDrag(event: PointerEvent) {
  const drag = dragged.value
  if (!drag || drag.pointerId !== event.pointerId) return
  drag.x = event.clientX; drag.y = event.clientY
  if (!drag.active && Math.abs(event.clientY - drag.startY) < 5) return
  event.preventDefault()
  if (!drag.active) {
    drag.active = true; editor.value = null
    scrollFrame = window.requestAnimationFrame(scrollWhileDragging)
  }
  updateDragOrder()
}
function cancelDrag() {
  if (scrollFrame !== null) { window.cancelAnimationFrame(scrollFrame); scrollFrame = null }
  const drag = dragged.value
  dragged.value = null; previewOrder.value = []
  if (drag?.handle.hasPointerCapture(drag.pointerId)) drag.handle.releasePointerCapture(drag.pointerId)
}
function finishDrag(event: PointerEvent) {
  if (dragged.value?.pointerId !== event.pointerId) return
  const active = dragged.value.active
  const ordered = [...displayedChapters.value]
  cancelDrag()
  if (active) saveOrder(ordered)
}
function abortDrag(event: PointerEvent) { if (dragged.value?.pointerId === event.pointerId) cancelDrag() }
function dragKey(event: KeyboardEvent) { if (event.key === 'Escape' && dragged.value) { event.preventDefault(); cancelDrag() } }
onMounted(() => {
  window.addEventListener('pointermove', moveDrag, { passive: false })
  window.addEventListener('pointerup', finishDrag)
  window.addEventListener('pointercancel', abortDrag)
  window.addEventListener('keydown', dragKey)
  window.addEventListener('blur', cancelDrag)
})
onUnmounted(() => {
  cancelDrag()
  window.removeEventListener('pointermove', moveDrag)
  window.removeEventListener('pointerup', finishDrag)
  window.removeEventListener('pointercancel', abortDrag)
  window.removeEventListener('keydown', dragKey)
  window.removeEventListener('blur', cancelDrag)
})
</script>

<template>
  <section class="panel chapter-panel">
    <div class="section-top"><div><h2>章节目录</h2><p class="muted">拖动左侧手柄上下排序，松手后自动保存；可重命名、合并或删除章节。提纲、试卷、练习题和答案保留在资料库。</p></div><button class="button subtle" :disabled="busy || !!dragged || !groups.length" @click="draft">{{ chapters.length || hasExcludedChapters ? '按文件重新整理' : '按文件建立目录' }}</button></div>
    <div v-if="!knowledgeChapters.length" class="empty-state compact"><div class="empty-symbol">≡</div><p>暂无知识章节</p><span>{{ hasExcludedChapters ? '原资料仍保留，点击“按文件重新整理”可恢复已删除的章节。' : '上传知识课件或笔记后会自动建立章节目录。' }}</span></div>
    <div v-else class="chapter-table-wrap">
      <table class="chapter-table">
        <thead><tr><th class="chapter-drag-cell">移动</th><th>顺序</th><th>章节名称</th><th>原文块</th><th>操作</th></tr></thead>
        <tbody ref="tableBody"><tr v-for="(chapter, index) in displayedChapters" :key="chapter.id" :data-chapter-id="chapter.id" :class="{ 'chapter-dragging': dragged?.active && dragged.id === chapter.id }">
          <td class="chapter-drag-cell"><button type="button" class="chapter-drag-handle" :disabled="busy || knowledgeChapters.length < 2" :aria-label="`拖动排序 ${chapter.title}，也可使用上下方向键移动`" title="按住上下拖动，或使用上下方向键" @pointerdown="startDrag($event, chapter)" @keydown.up.prevent="moveWithKeyboard(chapter.id, -1)" @keydown.down.prevent="moveWithKeyboard(chapter.id, 1)" @dragstart.prevent>⠿</button></td>
          <td><span class="row-number">{{ String(index + 1).padStart(2, '0') }}</span></td>
          <td class="file-chapter-title">{{ chapter.title }}</td><td class="chapter-block-count">{{ chapter.block_count }}</td>
          <td><div class="chapter-row-actions">
            <button class="text-button" :disabled="busy || !!dragged" :aria-label="`重命名 ${chapter.title}`" @click="openEditor(chapter, 'rename')">重命名</button>
            <button class="text-button" :disabled="busy || !!dragged || !chapter.block_count || knowledgeChapters.filter(item => item.block_count > 0).length < 2" :aria-label="`合并 ${chapter.title}`" @click="openEditor(chapter, 'merge')">合并</button>
            <button class="text-button danger" :disabled="busy || !!dragged" :aria-label="`删除 ${chapter.title}`" @click="removeChapter(chapter)">删除</button>
          </div></td>
        </tr></tbody>
      </table>
    </div>
    <form v-if="editor && editingChapter" class="chapter-edit-form" @submit.prevent="submitEdit" @keydown.esc.prevent="cancel">
      <h3>{{ actionLabels[editor.action] }} · {{ editingChapter.title }}</h3>
      <label v-if="editor.action === 'rename'">新章节名称<input ref="editorControl" v-model="title" maxlength="150" :disabled="busy" required /></label>
      <template v-else>
        <label>合并到<select ref="editorControl" v-model="targetId" :disabled="busy" required><option disabled value="">选择目标章节</option><option v-for="chapter in mergeTargets" :key="chapter.id" :value="chapter.id">{{ chapter.title }}</option></select></label>
        <p class="muted">本章全部原文将归入目标章节，保留目标章节名称和顺序，本章从目录中移除。</p>
      </template>
      <div class="chapter-edit-actions"><button type="button" class="button subtle small" :disabled="busy" @click="cancel">取消</button><button class="button primary small" :disabled="!canSubmit">{{ busy ? '保存中…' : editor.action === 'merge' ? '确认合并' : '保存' }}</button></div>
    </form>
    <div class="chapter-footer"><p class="muted">调整后按当前目录生成笔记。删除章节保留原资料，可继续用于问答；“按文件重新整理”可恢复已删除的目录条目。</p><span class="spacer"></span><span v-if="knowledgeChapters.length && chapters.every(chapter => chapter.confirmed)" class="badge ready">目录已确认</span><button class="button primary" :disabled="busy || !!dragged || !knowledgeChapters.length || !!editor" @click="save">{{ busy ? '处理中…' : '确认章节目录' }}</button></div>
    <div v-if="dragged?.active" class="chapter-drag-preview" :style="dragPreviewStyle" aria-hidden="true">⠿ {{ dragTitle }}</div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.file-chapter-title { overflow-wrap: anywhere; font-size: 14px; color: var(--ink); padding-top: 18px; padding-bottom: 18px; min-width: 180px; }
.chapter-footer > .muted { font-size: 12px; line-height: 1.85; max-width: 60%; }
.chapter-table th:last-child { width: 195px; min-width: 195px; }
.chapter-table .chapter-block-count, .chapter-table .badge { white-space: nowrap; }
.chapter-row-actions { display: flex; align-items: center; gap: 14px; white-space: nowrap; }
.chapter-row-actions .text-button { font-size: 12px; }
.chapter-edit-form { display: grid; gap: 16px; background: var(--surface-alt); border: 1px solid var(--line); border-radius: 10px; padding: 20px; margin-top: 18px; }
.chapter-edit-form h3 { font-size: 14px; color: var(--ink); overflow-wrap: anywhere; }
.chapter-edit-form label { display: grid; gap: 8px; font-size: 13px; color: #53617b; }
.chapter-edit-form input, .chapter-edit-form select { width: 100%; min-width: 0; }
.chapter-edit-actions { display: flex; justify-content: flex-end; gap: 10px; }
.chapter-table .chapter-drag-cell { width: 48px; min-width: 48px; text-align: center; padding-left: 5px; padding-right: 5px; }
.chapter-drag-handle { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 36px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: #8291ae; font-size: 24px; line-height: 1; cursor: grab; touch-action: none; user-select: none; }
.chapter-drag-handle:not(:disabled):hover { background: var(--soft); border-color: #cdd9f8; color: var(--accent); }
.chapter-drag-handle:not(:disabled):active { cursor: grabbing; }
.chapter-drag-handle:disabled { cursor: default; opacity: .35; }
.chapter-dragging > td { background: var(--soft); box-shadow: inset 0 2px #a9bcef, inset 0 -2px #a9bcef; }
.chapter-dragging .file-chapter-title { color: var(--accent-dark); }
.chapter-drag-preview { position: fixed; z-index: 80; pointer-events: none; max-width: 260px; padding: 12px 17px; border: 1px solid #b7c8f4; border-radius: 8px; background: var(--surface); color: var(--accent-dark); box-shadow: 0 8px 28px #1d2d4d26; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 600px) {
  .chapter-footer > .muted { max-width: 100%; }
  .chapter-edit-form { padding: 16px; }
  .chapter-row-actions { gap: 12px; }
}
</style>
