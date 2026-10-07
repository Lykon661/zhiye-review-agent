<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { NoteChatContext } from '../types'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{ courseId: string; outlineId: string; content: string; discussionBusy: boolean }>()
const emit = defineEmits<{ explain: [context: NoteChatContext] }>()
const body = ref<HTMLElement | null>(null)
const headings = ref<{ title: string; level: number; element: HTMLElement }[]>([])
interface TocEntry { index: number; title: string; level: number; depth: number; ancestors: number[]; hasChildren: boolean }
const expanded = ref(new Set<number>())
const tocOpen = ref(false)
const tocDocked = ref(false)
const tocStyle = ref<Record<string, string>>({})
const activeHeading = ref(0)
const tocEntries = computed(() => {
  const entries: TocEntry[] = [], parents: TocEntry[] = []
  const courseTitle = headings.value[0]?.level === 1
    && headings.value.filter(heading => heading.level === 1).length === 1
    && headings.value.some(heading => heading.level === 2)
  headings.value.forEach((heading, index) => {
    if (courseTitle && index === 0) return
    while (parents.length && parents[parents.length - 1].level >= heading.level) parents.pop()
    const entry: TocEntry = { index, title: heading.title, level: heading.level, depth: parents.length,
      ancestors: parents.map(parent => parent.index), hasChildren: false }
    if (parents.length) parents[parents.length - 1].hasChildren = true
    entries.push(entry); parents.push(entry)
  })
  return entries
})
const visibleEntries = computed(() => tocEntries.value.filter(entry => entry.ancestors.every(index => expanded.value.has(index))))
function toggleChapter(index: number) {
  const next = new Set(expanded.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expanded.value = next
}
function chapterActive(entry: TocEntry) {
  const active = tocEntries.value.find(item => item.index === activeHeading.value)
  return entry.index === activeHeading.value || !!active?.ancestors.includes(entry.index)
}
const selected = ref<NoteChatContext | null>(null)
const selectionError = ref('')
interface ReadingPosition { heading: number; title: string; offset: number }
const previous = ref<ReadingPosition | null>(null)
const storageKey = () => `review-note-position:${props.courseId}:${props.outlineId}`
let timer: ReturnType<typeof setTimeout> | undefined
let mounted = false
let layoutFrame: number | undefined
let layoutObserver: ResizeObserver | undefined
function placeToc() {
  if (!mounted || !body.value) return
  const panel = body.value.closest('.outline-content-panel') as HTMLElement | null
  const sidebar = document.querySelector('.course-sidebar') as HTMLElement | null
  if (!panel || !sidebar) return
  const panelRect = panel.getBoundingClientRect()
  const sidebarRect = sidebar.getBoundingClientRect()
  const gutter = panelRect.left - sidebarRect.right
  tocDocked.value = window.innerWidth > 600 && gutter >= 208
  if (tocDocked.value) {
    const width = Math.min(240, gutter - 32)
    const top = Math.max(16, Math.min(panelRect.top, window.innerHeight * .25))
    tocStyle.value = { left: `${sidebarRect.right + (gutter - width) / 2}px`, top: `${top}px`,
      width: `${width}px`, maxHeight: `${Math.max(120, window.innerHeight - top - 16)}px` }
    tocOpen.value = false
  } else {
    const left = window.innerWidth > 600 ? sidebarRect.right + 12 : 16
    tocStyle.value = { left: `${left}px`, top: '16px', width: `${Math.min(280, window.innerWidth - left - 16)}px`,
      maxHeight: 'calc(100dvh - 32px)' }
  }
}
function scheduleTocLayout() {
  if (layoutFrame !== undefined) return
  layoutFrame = requestAnimationFrame(() => { layoutFrame = undefined; placeToc() })
}
async function prepare() {
  await nextTick()
  if (!mounted || !body.value) return
  headings.value = Array.from(body.value.querySelectorAll<HTMLElement>('h1,h2,h3,h4,h5,h6')).map((element, index) => {
    element.id = `note-heading-${props.outlineId}-${index}`
    return { title: element.textContent || '未命名章节', level: Number(element.tagName.slice(1)), element }
  })
  placeToc()
  if (!layoutObserver) {
    layoutObserver = new ResizeObserver(scheduleTocLayout)
    const panel = body.value.closest('.outline-content-panel')
    const main = body.value.closest('.main-content')
    if (panel) layoutObserver.observe(panel)
    if (main) layoutObserver.observe(main)
  }
}
function savePosition() {
  const root = body.value
  if (!root || !headings.value.length || root.getBoundingClientRect().top > 160 || root.getBoundingClientRect().bottom < 80) return
  let index = 0
  for (let i = 0; i < headings.value.length; i++) {
    if (headings.value[i].element.getBoundingClientRect().top <= 160) index = i
    else break
  }
  const heading = headings.value[index]
  activeHeading.value = index
  const value = { heading: index, title: heading.title, offset: Math.max(0, -heading.element.getBoundingClientRect().top) }
  try { localStorage.setItem(storageKey(), JSON.stringify(value)) } catch { /* Reading stays available without browser storage. */ }
}
function scroll() {
  scheduleTocLayout()
  if (timer) clearTimeout(timer)
  timer = setTimeout(savePosition, 300)
}
function jump(index: number, offset = 0) {
  const targetIndex = Math.min(Math.max(0, index), headings.value.length - 1)
  const heading = headings.value[targetIndex]
  if (!heading) return
  const target = tocEntries.value.find(entry => entry.index === targetIndex)
  if (target) expanded.value = new Set([...expanded.value, ...target.ancestors])
  activeHeading.value = targetIndex
  const next = headings.value[targetIndex + 1]
  const span = next ? next.element.getBoundingClientRect().top - heading.element.getBoundingClientRect().top
    : (body.value?.getBoundingClientRect().bottom || 0) - heading.element.getBoundingClientRect().top
  const bounded = Math.min(Math.max(0, offset), Math.max(0, span - 60))
  window.scrollTo({ top: Math.max(0, window.scrollY + heading.element.getBoundingClientRect().top + bounded - 24), behavior: 'smooth' })
  previous.value = null
  tocOpen.value = false
}
function sourceElement(node: Node, offset: number, end: boolean) {
  let element = node.nodeType === Node.ELEMENT_NODE ? node as HTMLElement : node.parentElement
  if (node.nodeType === Node.ELEMENT_NODE && node.childNodes.length) {
    const child = node.childNodes[Math.min(Math.max(0, offset - (end ? 1 : 0)), node.childNodes.length - 1)]
    element = child.nodeType === Node.ELEMENT_NODE ? child as HTMLElement : child.parentElement
  }
  return element?.closest<HTMLElement>('[data-note-start]') || null
}
function selectionChanged() {
  const selection = window.getSelection()
  selected.value = null; selectionError.value = ''
  if (!selection?.rangeCount || selection.isCollapsed || !body.value) return
  const range = selection.getRangeAt(0)
  if (!body.value.contains(range.startContainer) || !body.value.contains(range.endContainer)) return
  const text = selection.toString().trim()
  if (!text) return
  if (text.length > 1200) { selectionError.value = '选择内容较长，请缩小到需要解释的段落。'; return }
  const first = sourceElement(range.startContainer, range.startOffset, false)
  const last = sourceElement(range.endContainer, range.endOffset, true)
  if (!first || !last) return
  const start = Number(first.dataset.noteStart), end = Number(last.dataset.noteEnd)
  if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) return
  const excerpt = props.content.replace(/\r\n?/g, '\n').split('\n').slice(start, end).join('\n')
  if (new TextEncoder().encode(excerpt).length > 4500) { selectionError.value = '这段内容较长，请选择更短的段落后解释。'; return }
  const title = headings.value.filter(item => Number(item.element.dataset.noteStart) <= start).at(-1)?.title || '复习笔记'
  selected.value = { outline_id: props.outlineId, heading: title, selected_text: text, source_text: excerpt, start_line: start, end_line: end }
}
function explain() {
  if (!selected.value || props.discussionBusy) return
  emit('explain', { ...selected.value })
  selected.value = null
}
watch(() => props.content, () => { selected.value = null; void prepare() })
onMounted(() => {
  mounted = true
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey()) || 'null') as ReadingPosition | null
    if (saved && Number.isInteger(saved.heading) && Number.isFinite(saved.offset)) previous.value = saved
  } catch { /* Ignore an unavailable or old reading position. */ }
  void prepare()
  window.addEventListener('scroll', scroll, { passive: true })
  window.addEventListener('resize', scheduleTocLayout)
  window.addEventListener('pagehide', savePosition)
  document.addEventListener('selectionchange', selectionChanged)
})
onBeforeUnmount(() => {
  savePosition(); mounted = false
  if (timer) clearTimeout(timer)
  if (layoutFrame !== undefined) cancelAnimationFrame(layoutFrame)
  layoutObserver?.disconnect()
  window.removeEventListener('scroll', scroll)
  window.removeEventListener('resize', scheduleTocLayout)
  window.removeEventListener('pagehide', savePosition)
  document.removeEventListener('selectionchange', selectionChanged)
})
</script>

<template>
  <div class="notes-reader">
    <Teleport to="body">
    <aside v-if="tocEntries.length" :class="['notes-toc', { 'is-docked': tocDocked, 'is-open': tocOpen }]" :style="tocStyle" aria-label="章节目录">
      <div class="notes-toc-heading"><span>章节目录</span><button v-if="!tocDocked" type="button" class="text-button" aria-label="关闭章节目录" @click="tocOpen = false">关闭</button></div>
      <nav aria-label="复习笔记章节">
        <ul>
          <li v-for="entry in visibleEntries" :key="entry.index" :class="['notes-toc-entry', { root: entry.depth === 0, active: chapterActive(entry) }]" :style="{ paddingLeft: `${entry.depth * 14}px` }">
            <button v-if="entry.hasChildren" type="button" class="notes-toc-toggle" :aria-expanded="expanded.has(entry.index)" :aria-label="`${expanded.has(entry.index) ? '收起' : '展开'}${entry.title}的小章节`" @click="toggleChapter(entry.index)"><span :class="{ expanded: expanded.has(entry.index) }" aria-hidden="true">▸</span></button>
            <span v-else class="notes-toc-spacer" aria-hidden="true"></span>
            <button type="button" class="notes-toc-title" :aria-current="entry.index === activeHeading ? 'location' : undefined" :title="entry.title" @click="jump(entry.index)">{{ entry.title }}</button>
          </li>
        </ul>
      </nav>
    </aside>
    </Teleport>
    <div class="notes-body">
      <div class="notes-reading-tools">
        <button v-if="tocEntries.length && !tocDocked" type="button" class="text-button" :aria-expanded="tocOpen" @click="tocOpen = !tocOpen">章节目录</button>
        <button v-if="previous && headings.length" type="button" class="text-button" @click="jump(previous.heading, previous.offset)">继续上次阅读 · {{ previous.title }}</button>
        <span class="muted">阅读位置自动保存 · 选中文字可解释这段</span>
      </div>
      <div v-if="selected || selectionError" class="notes-selection" role="status">
        <template v-if="selected"><span>已选择：{{ selected.selected_text.slice(0, 90) }}{{ selected.selected_text.length > 90 ? '…' : '' }}</span><button type="button" class="button subtle" :disabled="discussionBusy" @mousedown.prevent @click="explain">解释这段</button></template>
        <span v-else>{{ selectionError }}</span>
      </div>
      <div ref="body"><MarkdownContent :content="content" note-positions /></div>
    </div>
  </div>
</template>

<style scoped>
.notes-reader { margin-top: 20px; }
.notes-body { min-width: 0; width: 100%; max-width: none; margin: 0; }
.notes-body > .markdown-content { font-size: 16px; line-height: 2; }
.notes-toc { display: none; position: fixed; z-index: 8; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; padding: 12px 5px; }
.notes-toc.is-docked, .notes-toc.is-open { display: block; }
.notes-toc.is-open { z-index: 30; padding: 20px; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 40px #1d2d4d26; }
.notes-toc-heading { display: flex; align-items: center; justify-content: space-between; font-size: 13px; color: var(--muted); margin-bottom: 18px; }
.notes-toc ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
.notes-toc-entry { display: flex; align-items: flex-start; min-width: 0; gap: 3px; }
.notes-toc-entry.root { margin-top: 16px; }
.notes-toc-entry.root:first-child { margin-top: 0; }
.notes-toc-toggle, .notes-toc-spacer { width: 22px; height: 34px; flex-shrink: 0; }
.notes-toc-toggle { display: flex; align-items: center; justify-content: center; padding: 0; border: 0; background: transparent; color: var(--muted); border-radius: 5px; }
.notes-toc-toggle span { display: block; transition: transform .15s; }
.notes-toc-toggle span.expanded { transform: rotate(90deg); }
.notes-toc-title { min-width: 0; flex: 1; text-align: left; white-space: normal; overflow-wrap: anywhere; border: 0; background: transparent; padding: 6px; color: #53617b; font-size: 13px; line-height: 1.75; border-radius: 5px; }
.notes-toc-entry.root > .notes-toc-title { font-size: 14px; font-weight: 600; color: var(--ink); }
.notes-toc-entry.active > .notes-toc-title { color: var(--accent-dark); background: var(--soft); }
.notes-toc-title:hover, .notes-toc-toggle:hover { background: var(--soft); color: var(--accent); }
.notes-reading-tools { display: flex; gap: 12px; align-items: flex-start; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding: 0 0 16px; margin-bottom: 24px; font-size: 12px; }
.notes-reading-tools > .text-button { white-space: normal; text-align: left; }
.notes-reading-tools > .muted { flex-basis: 100%; font-size: 12px; }
.notes-selection { position: sticky; top: 12px; z-index: 5; display: flex; gap: 14px; align-items: center; justify-content: space-between; padding: 14px 16px; border: 1px solid var(--accent-line); border-radius: 9px; background: #edf2ff; box-shadow: 0 4px 16px #1d2d4d0d; font-size: 13px; margin-bottom: 20px; }
.notes-selection span { min-width: 0; overflow-wrap: anywhere; color: #435b92; }
.notes-selection button { flex-shrink: 0; }
@media (max-width: 600px) {
  .notes-selection { flex-wrap: wrap; padding: 12px; }
  .notes-selection .button { margin-left: auto; }
  .notes-body > .markdown-content { font-size: 15px; }
}
</style>
