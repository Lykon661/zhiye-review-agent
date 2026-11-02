<script setup lang="ts">
import { computed, onUnmounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { api } from '../api'
import type { KnowledgeNoteJumpTarget, KnowledgePointChapter, KnowledgePointNode, KnowledgePointsResponse } from '../types'
import { kindLabels } from '../types'
import MarkdownContent from './MarkdownContent.vue'
import UiIcon from './UiIcon.vue'

const props = defineProps<{ courseId: string; refreshKey: string }>()
const emit = defineEmits<{
  openSource: [sourceId: string]
  jumpToNote: [target: KnowledgeNoteJumpTarget]
  requestGenerate: []
}>()

const data = ref<KnowledgePointsResponse | null>(null)
const loading = ref(false)
const error = ref('')
const expandedChapters = ref(new Set<string>())
const expandedPoints = ref(new Set<string>())
const selectedId = ref('')
const activeTreeKey = ref('')
const nodeButtons = new Map<string, HTMLButtonElement>()
let generation = 0
let disposed = false
let controller = new AbortController()

interface FlatTreeItem {
  key: string
  kind: 'chapter' | 'point'
  level: 1 | 2 | 3
  parentKey: string | null
  hasChildren: boolean
  canExpand: boolean
  expanded: boolean
  chapter: KnowledgePointChapter
  point?: KnowledgePointNode
}

function active(token: number, course: string) {
  return !disposed && token === generation && props.courseId === course
}

function errorText(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason)
}

const allPoints = computed(() => {
  const list: { chapter: KnowledgePointChapter; point: KnowledgePointNode }[] = []
  for (const chapter of data.value?.chapters || []) {
    for (const point of chapter.points) {
      list.push({ chapter, point })
      for (const child of point.children || []) {
        list.push({ chapter, point: child })
      }
    }
  }
  return list
})

const totalPointsCount = computed(() => allPoints.value.length)
const selectedEntry = computed(() => allPoints.value.find(item => item.point.id === selectedId.value) || null)

const flatVisibleItems = computed<FlatTreeItem[]>(() => {
  const items: FlatTreeItem[] = []
  for (const chapter of data.value?.chapters || []) {
    const chKey = `ch:${chapter.chapter_id}`
    const chExpanded = expandedChapters.value.has(chapter.chapter_id)
    const hasPoints = chapter.points.length > 0
    items.push({
      key: chKey, kind: 'chapter', level: 1, parentKey: null,
      hasChildren: hasPoints, canExpand: true, expanded: chExpanded, chapter,
    })
    if (!chExpanded) continue
    for (const point of chapter.points) {
      const ptKey = `pt:${point.id}`
      const hasChildren = (point.children?.length || 0) > 0
      const ptExpanded = expandedPoints.value.has(point.id)
      items.push({
        key: ptKey, kind: 'point', level: 2, parentKey: chKey,
        hasChildren, canExpand: hasChildren, expanded: ptExpanded, chapter, point,
      })
      if (hasChildren && ptExpanded) {
        for (const child of point.children) {
          items.push({
            key: `pt:${child.id}`, kind: 'point', level: 3, parentKey: ptKey,
            hasChildren: false, canExpand: false, expanded: false, chapter, point: child,
          })
        }
      }
    }
  }
  return items
})

const currentFocusableKey = computed(() => {
  const items = flatVisibleItems.value
  if (items.some(item => item.key === activeTreeKey.value)) return activeTreeKey.value
  const selectedItem = items.find(item => item.kind === 'point' && item.point?.id === selectedId.value)
  return selectedItem?.key || items[0]?.key || ''
})

function setButtonRef(key: string, el: Element | ComponentPublicInstance | null) {
  if (el instanceof HTMLButtonElement) nodeButtons.set(key, el)
  else nodeButtons.delete(key)
}

function toggleChapter(chapterId: string) {
  const next = new Set(expandedChapters.value)
  if (next.has(chapterId)) next.delete(chapterId)
  else next.add(chapterId)
  expandedChapters.value = next
}

function togglePoint(pointId: string) {
  const next = new Set(expandedPoints.value)
  if (next.has(pointId)) next.delete(pointId)
  else next.add(pointId)
  expandedPoints.value = next
}

function expandAll() {
  const ch = new Set<string>(), pt = new Set<string>()
  for (const chapter of data.value?.chapters || []) {
    ch.add(chapter.chapter_id)
    for (const point of chapter.points) {
      if (point.children?.length) pt.add(point.id)
    }
  }
  expandedChapters.value = ch
  expandedPoints.value = pt
}

function collapseAll() {
  expandedChapters.value = new Set()
  expandedPoints.value = new Set()
}

function selectPoint(point: KnowledgePointNode) {
  selectedId.value = point.id
  activeTreeKey.value = `pt:${point.id}`
}

function displayLabel(point: KnowledgePointNode) {
  const zh = point.name_zh || point.title
  return point.name_en ? `${zh}（${point.name_en}）` : zh
}

function showRawTitle(point: KnowledgePointNode) {
  const constructed = displayLabel(point).replace(/\s+/g, '')
  const raw = point.title.replace(/\s+/g, '')
  return /[$`\\]/.test(point.title) || (raw !== constructed && point.title !== point.name_zh)
}

function filteredAliases(point: KnowledgePointNode) {
  const exclude = new Set([point.name_zh, point.name_en || '', point.title].map(s => s.trim().toLowerCase()).filter(Boolean))
  return (point.aliases || []).filter(alias => !exclude.has(alias.trim().toLowerCase()))
}

function sourceReasonText(reason: KnowledgePointNode['source_reason']) {
  if (reason === 'no_english') return '未定位原文（该节点标题未标注英文原词，无法直接比对英文课件词表）'
  if (reason === 'not_matched') return '未定位原文（课件与笔记原文中未直接匹配到该节点的英文原词）'
  return '未定位原文'
}

function jumpFromDetail() {
  const entry = selectedEntry.value
  const outlineId = data.value?.outline_id
  if (!entry || !outlineId) return
  emit('jumpToNote', {
    outlineId,
    chapterTitle: entry.chapter.title,
    title: entry.point.title,
    nameZh: entry.point.name_zh,
    nameEn: entry.point.name_en,
    level: entry.point.level,
    noteLine: entry.point.note_line,
  })
}

function focusItem(key: string) {
  activeTreeKey.value = key
  nodeButtons.get(key)?.focus()
}

function onTreeKeydown(event: KeyboardEvent, index: number) {
  const items = flatVisibleItems.value
  const current = items[index]
  if (!current) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    const next = items[index + 1]
    if (next) focusItem(next.key)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    const prev = items[index - 1]
    if (prev) focusItem(prev.key)
  } else if (event.key === 'Home') {
    event.preventDefault()
    if (items[0]) focusItem(items[0].key)
  } else if (event.key === 'End') {
    event.preventDefault()
    const last = items[items.length - 1]
    if (last) focusItem(last.key)
  } else if (event.key === 'ArrowRight') {
    event.preventDefault()
    if (current.canExpand && !current.expanded) {
      if (current.kind === 'chapter') toggleChapter(current.chapter.chapter_id)
      else if (current.point) togglePoint(current.point.id)
    } else if (current.hasChildren && current.expanded && items[index + 1]) {
      focusItem(items[index + 1].key)
    }
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault()
    if (current.canExpand && current.expanded) {
      if (current.kind === 'chapter') toggleChapter(current.chapter.chapter_id)
      else if (current.point) togglePoint(current.point.id)
    } else if (current.parentKey) {
      focusItem(current.parentKey)
    }
  }
}

async function load(resetExpansion = false) {
  if (!props.courseId) return
  const course = props.courseId
  const token = ++generation
  controller.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const response = await api<KnowledgePointsResponse>(`/courses/${course}/knowledge-points`, { signal: controller.signal })
    if (!active(token, course)) return
    data.value = response
    if (resetExpansion || !expandedChapters.value.size) {
      const ch = new Set<string>(), pt = new Set<string>()
      for (const chapter of response.chapters) {
        ch.add(chapter.chapter_id)
        for (const point of chapter.points) {
          if (point.children?.length) pt.add(point.id)
        }
      }
      expandedChapters.value = ch
      expandedPoints.value = pt
    }
    const exists = allPoints.value.some(item => item.point.id === selectedId.value)
    if (!exists) selectedId.value = allPoints.value[0]?.point.id || ''
  } catch (reason) {
    if (active(token, course) && (reason as Error).name !== 'AbortError') {
      error.value = errorText(reason)
    }
  } finally {
    if (active(token, course)) loading.value = false
  }
}

watch(() => props.courseId, () => {
  ++generation
  controller.abort()
  controller = new AbortController()
  data.value = null
  selectedId.value = ''
  activeTreeKey.value = ''
  expandedChapters.value = new Set()
  expandedPoints.value = new Set()
  error.value = ''
  void load(true)
}, { immediate: true })

watch(() => props.refreshKey, (next, prev) => {
  if (next !== prev && props.courseId) void load(false)
})

onUnmounted(() => {
  disposed = true
  controller.abort()
})
</script>

<template>
  <div class="kp-panel">
    <div v-if="error" class="notice error" role="alert">
      <span>{{ error }}</span>
      <button type="button" class="text-button" :disabled="loading" @click="load(false)">重试</button>
    </div>

    <div v-if="data?.warnings?.length" class="notice info" role="status">
      <span>{{ data.warnings.join('；') }}</span>
    </div>

    <p v-if="loading && !data" class="loading-bar" role="status">正在读取知识点树…</p>

    <div v-else-if="data?.status === 'no_notes'" class="empty-state outline-empty">
      <div class="empty-symbol"><UiIcon name="outline" /></div>
      <h3>还没有提取知识点</h3>
      <p>知识点结构将从当前有效复习笔记中自动整理，无需额外调用模型。请先确认章节目录并在上方生成复习笔记。</p>
      <button type="button" class="button subtle" @click="emit('requestGenerate')">返回阅读笔记视图</button>
    </div>

    <div v-else-if="data?.status === 'stale'" class="empty-state outline-empty">
      <div class="empty-symbol"><UiIcon name="outline" /></div>
      <h3>复习笔记已过期，知识点待更新</h3>
      <p>课程资料或章节目录已发生变化，旧版笔记不再对应最新章节结构。请点击上方“生成复习笔记”更新后再浏览知识点树。</p>
      <button type="button" class="button subtle" @click="emit('requestGenerate')">返回阅读笔记视图</button>
    </div>

    <div v-else-if="data?.status === 'ready' && !totalPointsCount" class="empty-state compact">
      <h3>未从当前笔记中识别到知识点标题</h3>
      <p>复习笔记中的三级标题（###）与四级标题（####）会自动整理为知识点树。</p>
      <button type="button" class="text-button" @click="emit('requestGenerate')">查看复习笔记</button>
    </div>

    <template v-else-if="data?.status === 'ready'">
      <div class="kp-toolbar">
        <span class="muted">共 {{ data.chapters.length }} 个章节 · {{ totalPointsCount }} 个知识点 · 点击节点查看出处与跳转笔记</span>
        <div class="inline-actions">
          <button type="button" class="text-button" @click="expandAll">全部展开</button>
          <button type="button" class="text-button" @click="collapseAll">全部收起</button>
          <button type="button" class="text-button" :disabled="loading" @click="load(false)">刷新 ↻</button>
        </div>
      </div>

      <div class="kp-layout">
        <nav class="kp-tree-pane" aria-label="课程知识点树">
          <ul class="kp-tree" role="tree" aria-label="按章节组织的知识点">
            <li
              v-for="(item, index) in flatVisibleItems"
              :key="item.key"
              role="none"
              :class="['kp-tree-row', `level-${item.level}`, { selected: item.kind === 'point' && item.point?.id === selectedId }]"
            >
              <template v-if="item.kind === 'chapter'">
                <button
                  :ref="el => setButtonRef(item.key, el)"
                  type="button"
                  role="treeitem"
                  :aria-level="1"
                  :aria-expanded="item.expanded"
                  :aria-selected="false"
                  :tabindex="item.key === currentFocusableKey ? 0 : -1"
                  class="kp-chapter-btn"
                  @focus="activeTreeKey = item.key"
                  @click="toggleChapter(item.chapter.chapter_id)"
                  @keydown="onTreeKeydown($event, index)"
                >
                  <span :class="['kp-arrow', { expanded: item.expanded }]" aria-hidden="true">▸</span>
                  <strong class="kp-chapter-title">{{ item.chapter.title }}</strong>
                  <span class="count-badge">{{ item.chapter.points.length }}</span>
                </button>
                <p v-if="item.expanded && !item.chapter.points.length" class="kp-chapter-empty muted">本章笔记暂无三级或四级知识点标题。</p>
              </template>

              <template v-else-if="item.point">
                <button
                  v-if="item.hasChildren"
                  type="button"
                  tabindex="-1"
                  class="kp-toggle-btn"
                  :aria-expanded="item.expanded"
                  :aria-label="`${item.expanded ? '收起' : '展开'} ${item.point.name_zh || item.point.title} 的子知识点`"
                  @click="togglePoint(item.point.id)"
                >
                  <span :class="['kp-arrow', { expanded: item.expanded }]" aria-hidden="true">▸</span>
                </button>
                <span v-else class="kp-toggle-spacer" aria-hidden="true"></span>

                <button
                  :ref="el => setButtonRef(item.key, el)"
                  type="button"
                  role="treeitem"
                  :aria-level="item.level"
                  :aria-expanded="item.hasChildren ? item.expanded : undefined"
                  :aria-selected="item.point.id === selectedId"
                  :tabindex="item.key === currentFocusableKey ? 0 : -1"
                  :class="['kp-node-btn', { active: item.point.id === selectedId }]"
                  @focus="activeTreeKey = item.key"
                  @click="selectPoint(item.point)"
                  @keydown="onTreeKeydown($event, index)"
                >
                  <span class="kp-node-zh">{{ item.point.name_zh || item.point.title }}</span>
                  <span v-if="item.point.name_en" class="kp-node-en">（{{ item.point.name_en }}）</span>
                  <span v-if="item.point.sources.length" class="kp-source-count" :title="`${item.point.sources.length} 处课件原文`">{{ item.point.sources.length }}</span>
                </button>
              </template>
            </li>
          </ul>
        </nav>

        <aside class="kp-detail-pane" aria-label="知识点详情">
          <div v-if="selectedEntry" class="kp-detail-card">
            <div class="kp-detail-meta">
              <span class="badge neutral">{{ selectedEntry.point.level === 3 ? '知识点' : '子知识点' }}</span>
              <span class="muted">{{ selectedEntry.chapter.title }}</span>
            </div>

            <h3 class="kp-detail-title">
              {{ selectedEntry.point.name_zh || selectedEntry.point.title }}
              <span v-if="selectedEntry.point.name_en" class="kp-detail-en">（{{ selectedEntry.point.name_en }}）</span>
            </h3>

            <div v-if="showRawTitle(selectedEntry.point)" class="kp-raw-title">
              <span class="muted">笔记原标题：</span>
              <MarkdownContent :content="selectedEntry.point.title" hide-citations />
            </div>

            <div class="kp-detail-fields">
              <div><span class="kp-field-label">中文名称</span><strong>{{ selectedEntry.point.name_zh || '—' }}</strong></div>
              <div><span class="kp-field-label">英文名称</span><strong>{{ selectedEntry.point.name_en || '未标注' }}</strong></div>
              <div v-if="filteredAliases(selectedEntry.point).length" class="kp-field-full">
                <span class="kp-field-label">相关术语 / 别名</span>
                <span>{{ filteredAliases(selectedEntry.point).join('、') }}</span>
              </div>
            </div>

            <div class="kp-detail-actions">
              <button type="button" class="button primary small" :disabled="!data.outline_id" @click="jumpFromDetail">
                在笔记中查看
              </button>
            </div>

            <div class="kp-sources-section">
              <div class="kp-sources-header">
                <strong>课件出处页</strong>
                <span class="count-badge">{{ selectedEntry.point.sources.length }}</span>
              </div>
              <div v-if="selectedEntry.point.sources.length" class="kp-source-list">
                <button
                  v-for="src in selectedEntry.point.sources"
                  :key="src.source_id"
                  type="button"
                  class="kp-source-item"
                  @click="emit('openSource', src.source_id)"
                >
                  <span class="kp-source-kind">{{ kindLabels[src.kind] || src.kind }}</span>
                  <div class="kp-source-info">
                    <strong>{{ src.filename }}</strong>
                    <span class="muted">{{ src.locator.label }}{{ src.title && src.title !== src.filename ? ` · ${src.title}` : '' }}</span>
                  </div>
                  <span class="kp-source-open">查看原文 →</span>
                </button>
              </div>
              <p v-else class="kp-no-source muted">
                {{ sourceReasonText(selectedEntry.point.source_reason) }}
              </p>
            </div>
          </div>
          <div v-else class="empty-state compact">
            <p>点击左侧知识点查看中英文名称、出处页与笔记位置。</p>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.kp-panel { margin-top: 20px; }
.kp-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding-bottom: 16px; margin-bottom: 20px; border-bottom: 1px solid var(--line); }
.kp-layout { display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr); gap: 22px; align-items: start; }
.kp-tree-pane { border: 1px solid var(--line); border-radius: 10px; background: var(--surface-alt); padding: 14px 12px; min-width: 0; }
.kp-tree { list-style: none; padding: 0; margin: 0; display: grid; gap: 3px; }
.kp-tree-row { display: flex; align-items: flex-start; min-width: 0; flex-wrap: wrap; }
.kp-tree-row.level-1 { margin-top: 10px; }
.kp-tree-row.level-1:first-child { margin-top: 0; }
.kp-tree-row.level-2 { padding-left: 14px; }
.kp-tree-row.level-3 { padding-left: 34px; }
.kp-chapter-btn { width: 100%; display: flex; align-items: center; gap: 8px; padding: 8px 10px; border: 0; border-radius: 7px; background: var(--surface); border: 1px solid var(--line); color: var(--ink); text-align: left; font-size: 13px; }
.kp-chapter-btn:hover { border-color: var(--accent-line); background: var(--soft); }
.kp-chapter-title { flex: 1; min-width: 0; overflow-wrap: anywhere; font-weight: 600; }
.kp-chapter-empty { width: 100%; padding: 8px 12px 4px 28px; font-size: 12px; }
.kp-toggle-btn, .kp-toggle-spacer { width: 24px; height: 32px; flex-shrink: 0; }
.kp-toggle-btn { border: 0; background: transparent; color: var(--muted); border-radius: 5px; display: inline-flex; align-items: center; justify-content: center; padding: 0; }
.kp-toggle-btn:hover { background: #e3e9f6; color: var(--accent); }
.kp-arrow { display: inline-block; transition: transform .15s; font-size: 12px; color: var(--muted); }
.kp-arrow.expanded { transform: rotate(90deg); }
.kp-node-btn { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 4px; flex-wrap: wrap; text-align: left; padding: 6px 10px; border: 1px solid transparent; border-radius: 7px; background: transparent; color: #43516b; font-size: 13px; line-height: 1.65; }
.kp-node-btn:hover { background: #e8eefb; color: var(--ink); }
.kp-node-btn.active { background: var(--surface); border-color: var(--accent-line); color: var(--accent-dark); font-weight: 600; box-shadow: 0 1px 3px #1d2d4d0d; }
.kp-node-zh { overflow-wrap: anywhere; }
.kp-node-en { color: var(--muted); font-weight: 400; font-size: 12px; overflow-wrap: anywhere; }
.kp-node-btn.active .kp-node-en { color: #4c6396; }
.kp-source-count { margin-left: auto; padding: 0 6px; border-radius: 4px; background: #e5ebf7; color: #52627d; font-size: 11px; font-weight: 500; font-variant-numeric: tabular-nums; flex-shrink: 0; }
.kp-detail-pane { position: sticky; top: 16px; min-width: 0; }
.kp-detail-card { border: 1px solid var(--line); border-radius: 10px; background: var(--surface); padding: 22px; }
.kp-detail-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 12px; }
.kp-detail-title { font-size: 18px; line-height: 1.5; color: var(--ink); overflow-wrap: anywhere; }
.kp-detail-en { font-size: 15px; font-weight: 500; color: var(--muted); }
.kp-raw-title { margin-top: 10px; padding: 8px 12px; border-radius: 7px; background: var(--surface-alt); border: 1px solid var(--line); font-size: 13px; display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; }
.kp-raw-title :deep(.markdown-content) { font-size: 13px; line-height: 1.6; display: inline; }
.kp-raw-title :deep(.markdown-content p) { margin: 0; display: inline; }
.kp-detail-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 18px 0; padding: 14px; border-radius: 8px; background: var(--surface-alt); border: 1px solid var(--line); font-size: 13px; }
.kp-detail-fields > div { min-width: 0; overflow-wrap: anywhere; }
.kp-field-full { grid-column: 1 / -1; }
.kp-field-label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
.kp-detail-actions { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; }
.kp-sources-section { border-top: 1px solid var(--line); padding-top: 18px; }
.kp-sources-header { display: flex; align-items: center; margin-bottom: 12px; font-size: 13px; }
.kp-source-list { display: grid; gap: 8px; }
.kp-source-item { width: 100%; display: flex; align-items: center; gap: 12px; text-align: left; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-alt); transition: border-color .15s, background-color .15s; }
.kp-source-item:hover { border-color: var(--accent-line); background: var(--soft); }
.kp-source-kind { flex-shrink: 0; padding: 3px 7px; border-radius: 5px; background: var(--soft); color: var(--accent-dark); border: 1px solid #d7e1fd; font-size: 11px; }
.kp-source-info { flex: 1; min-width: 0; }
.kp-source-info strong { display: block; font-size: 13px; color: var(--ink); overflow-wrap: anywhere; }
.kp-source-info span { display: block; font-size: 12px; overflow-wrap: anywhere; }
.kp-source-open { flex-shrink: 0; font-size: 12px; color: var(--accent); font-weight: 500; }
.kp-no-source { padding: 12px 14px; border-radius: 8px; background: var(--surface-alt); border: 1px dashed var(--line); font-size: 12px; }
@media (max-width: 1400px) {
  .kp-layout { grid-template-columns: minmax(0, 1fr); }
  .kp-detail-pane { position: static; }
}
@media (max-width: 600px) {
  .kp-tree-pane { padding: 10px 8px; }
  .kp-tree-row.level-2 { padding-left: 8px; }
  .kp-tree-row.level-3 { padding-left: 22px; }
  .kp-detail-card { padding: 16px; }
  .kp-detail-fields { grid-template-columns: 1fr; }
  .kp-source-item { flex-wrap: wrap; }
  .kp-source-open { margin-left: auto; }
}
</style>
