<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { ExamQuestionHit, KnowledgeNoteJumpTarget, KnowledgePointChapter, KnowledgePointNode, KnowledgePointsResponse } from '../types'
import KnowledgePointDetail from './KnowledgePointDetail.vue'
import UiIcon from './UiIcon.vue'

const props = defineProps<{ courseId: string; refreshKey: string }>()
const emit = defineEmits<{
  jumpToNote: [target: KnowledgeNoteJumpTarget]
  requestGenerate: []
}>()

interface PointEntry {
  chapter: KnowledgePointChapter
  point: KnowledgePointNode
  parent: KnowledgePointNode | null
  questions: (ExamQuestionHit & { via: string | null })[]
}

const data = ref<KnowledgePointsResponse | null>(null)
const loading = ref(false)
const error = ref('')
const mode = ref<'rank' | 'chapter'>('rank')
const selectedId = ref('')
let generation = 0
let disposed = false
let controller = new AbortController()

function active(token: number, course: string) {
  return !disposed && token === generation && props.courseId === course
}

function errorText(reason: unknown) {
  return reason instanceof Error ? reason.message : String(reason)
}

function pointName(point: KnowledgePointNode) {
  return point.name_zh || point.title
}

// A point's frequency includes its sub-points, each question counted once.
function collectQuestions(point: KnowledgePointNode) {
  const seen = new Set<string>()
  const list: PointEntry['questions'] = []
  for (const [node, via] of [[point, null], ...point.children.map(child => [child, pointName(child)])] as [KnowledgePointNode, string | null][]) {
    for (const question of node.exam_questions || []) {
      if (seen.has(question.question_id)) continue
      seen.add(question.question_id)
      list.push({ ...question, via })
    }
  }
  return list
}

const entries = computed(() => {
  const map = new Map<string, PointEntry>()
  for (const chapter of data.value?.chapters || []) {
    for (const point of chapter.points) {
      map.set(point.id, { chapter, point, parent: null, questions: collectQuestions(point) })
      for (const child of point.children || []) {
        map.set(child.id, { chapter, point: child, parent: point, questions: collectQuestions(child) })
      }
    }
  }
  return map
})

const papers = computed(() => data.value?.exam_papers || [])
const topEntries = computed(() => [...entries.value.values()].filter(entry => !entry.parent))
const totalPoints = computed(() => entries.value.size)
const testedPoints = computed(() => topEntries.value.filter(entry => entry.questions.length).length)
const matchedQuestions = computed(() => papers.value.reduce((sum, paper) => sum + paper.matched_count, 0))
const totalQuestions = computed(() => papers.value.reduce((sum, paper) => sum + paper.question_count, 0))

const ranking = computed(() => topEntries.value.filter(entry => entry.questions.length)
  .sort((a, b) => b.questions.length - a.questions.length))
const maxCount = computed(() => ranking.value[0]?.questions.length || 1)

const chapterViews = computed(() => (data.value?.chapters || []).map(chapter => {
  const points = chapter.points.map(point => entries.value.get(point.id)!).filter(Boolean)
  const ids = new Set(points.flatMap(entry => entry.questions.map(question => question.question_id)))
  return { chapter, points, count: ids.size, tested: points.filter(entry => entry.questions.length).length }
}))

const selected = computed(() => entries.value.get(selectedId.value) || null)

function heat(count: number) {
  return count >= 3 ? 3 : count
}

function paperLabel(title: string, index: number) {
  return title.match(/(?:19|20)\d{2}/)?.[0] || `卷${index + 1}`
}

function paperHits(entry: PointEntry, documentId: string) {
  return entry.questions.filter(question => question.document_id === documentId).length
}

function childCounts(point: KnowledgePointNode) {
  return Object.fromEntries(point.children.map(child => [child.id, entries.value.get(child.id)?.questions.length || 0]))
}

// The open detail stays under its top-level row when one of its sub-points is chosen.
function openUnder(entry: PointEntry) {
  return !!selected.value && (selected.value.point.id === entry.point.id || selected.value.parent?.id === entry.point.id)
}

function toggle(id: string) {
  selectedId.value = selectedId.value === id ? '' : id
}




function jumpToNote(entry: PointEntry) {
  const outlineId = data.value?.outline_id
  if (!outlineId) return
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

async function load() {
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
    if (!entries.value.has(selectedId.value)) selectedId.value = ''
    if (!response.exam_papers?.length) mode.value = 'chapter'
  } catch (reason) {
    if (active(token, course) && (reason as Error).name !== 'AbortError') error.value = errorText(reason)
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
  error.value = ''
  void load()
}, { immediate: true })

watch(() => props.refreshKey, (next, prev) => {
  if (next !== prev && props.courseId) void load()
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
      <button type="button" class="text-button" :disabled="loading" @click="load">重试</button>
    </div>

    <div v-if="data?.warnings?.length" class="notice info" role="status">
      <span>{{ data.warnings.join('；') }}</span>
    </div>

    <p v-if="loading && !data" class="loading-bar" role="status">正在整理考频地图…</p>

    <div v-else-if="data?.status === 'no_notes'" class="empty-state outline-empty">
      <div class="empty-symbol"><UiIcon name="outline" /></div>
      <h3>还没有复习笔记</h3>
      <p>考频地图从复习笔记的知识点标题整理而来，再对照上传的历年试卷统计每个知识点考过几题。请先生成复习笔记。</p>
      <button type="button" class="button subtle" @click="emit('requestGenerate')">返回阅读笔记</button>
    </div>

    <div v-else-if="data?.status === 'stale'" class="empty-state outline-empty">
      <div class="empty-symbol"><UiIcon name="outline" /></div>
      <h3>复习笔记已过期</h3>
      <p>课程资料或章节目录有变化，请先在“阅读笔记”中重新生成复习笔记。</p>
      <button type="button" class="button subtle" @click="emit('requestGenerate')">返回阅读笔记</button>
    </div>

    <div v-else-if="data?.status === 'ready' && !totalPoints" class="empty-state compact">
      <h3>当前笔记里没有识别到知识点标题</h3>
      <p>复习笔记中的三级、四级标题会整理成知识点。</p>
      <button type="button" class="text-button" @click="emit('requestGenerate')">查看复习笔记</button>
    </div>

    <template v-else-if="data?.status === 'ready'">
      <section class="kp-summary" aria-label="考频概览">
        <div class="kp-stat"><strong>{{ testedPoints }}</strong><span>/ {{ topEntries.length }} 个知识点考过</span></div>
        <div class="kp-stat"><strong>{{ matchedQuestions }}</strong><span>/ {{ totalQuestions }} 道题对上知识点</span></div>
        <div class="kp-stat"><strong>{{ papers.length }}</strong><span>份试卷</span></div>
      </section>
      <p v-if="!papers.length" class="kp-no-papers">还没有可用的试卷或练习题。上传历年试卷并解析完成后，这里会统计每个知识点考过几题。</p>

      <div class="kp-tabs" role="tablist" aria-label="考频地图视图">
        <button type="button" role="tab" :aria-selected="mode === 'rank'" :class="{ on: mode === 'rank' }" :disabled="!papers.length" @click="mode = 'rank'">考点排行</button>
        <button type="button" role="tab" :aria-selected="mode === 'chapter'" :class="{ on: mode === 'chapter' }" @click="mode = 'chapter'">按章节</button>
      </div>

      <ol v-if="mode === 'rank'" class="kp-rank">
        <li class="kp-rank-head" aria-hidden="true">
          <span>知识点</span>
          <span class="kp-paper-cols"><span v-for="(paper, index) in papers" :key="paper.document_id" :title="paper.title">{{ paperLabel(paper.title, index) }}</span></span>
        </li>
        <li v-for="(entry, index) in ranking" :key="entry.point.id" :class="['kp-rank-item', { open: openUnder(entry) }]">
          <button type="button" class="kp-rank-row" :aria-expanded="openUnder(entry)" @click="toggle(entry.point.id)">
            <span class="kp-rank-no">{{ index + 1 }}</span>
            <span class="kp-rank-main">
              <span class="kp-rank-name">{{ pointName(entry.point) }}<small v-if="entry.point.name_en">{{ entry.point.name_en }}</small></span>
              <span class="kp-rank-bar"><i :style="{ width: `${entry.questions.length / maxCount * 100}%` }"></i><b>{{ entry.questions.length }} 题</b></span>
            </span>
            <span class="kp-paper-cols">
              <span v-for="paper in papers" :key="paper.document_id" :class="['kp-cell', `heat-${heat(paperHits(entry, paper.document_id))}`]" :title="`${paper.title}：${paperHits(entry, paper.document_id)} 题`">{{ paperHits(entry, paper.document_id) || '' }}</span>
            </span>
          </button>
          <div v-if="openUnder(entry)"><KnowledgePointDetail :point="selected!.point" :parent="selected!.parent" :questions="selected!.questions" :child-counts="childCounts(selected!.point)" :has-papers="!!papers.length" :can-jump="!!data.outline_id" @select="selectedId = $event" @jump="jumpToNote(selected!)" /></div>
        </li>
      </ol>

      <div v-else class="kp-chapters">
        <section v-for="view in chapterViews" :key="view.chapter.chapter_id" class="kp-chapter">
          <header class="kp-chapter-head">
            <h3>{{ view.chapter.title }}</h3>
            <span :class="['kp-chapter-count', { hot: view.count }]">{{ view.count ? `${view.tested}/${view.points.length} 点 · ${view.count} 题` : `${view.points.length} 点 · 未考` }}</span>
          </header>
          <div class="kp-chips">
            <button v-for="entry in view.points" :key="entry.point.id" type="button" :class="['kp-chip', `heat-${heat(entry.questions.length)}`, { on: openUnder(entry) }]" :aria-expanded="openUnder(entry)" @click="toggle(entry.point.id)">
              {{ pointName(entry.point) }}<em v-if="entry.questions.length">{{ entry.questions.length }}</em>
            </button>
          </div>
          <div v-if="selected && view.points.some(openUnder)" class="kp-chapter-detail"><KnowledgePointDetail :point="selected!.point" :parent="selected!.parent" :questions="selected!.questions" :child-counts="childCounts(selected!.point)" :has-papers="!!papers.length" :can-jump="!!data.outline_id" @select="selectedId = $event" @jump="jumpToNote(selected!)" /></div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.kp-panel { margin-top: 20px; --heat-1: #e3eafd; --heat-2: #b7c8f8; --heat-3: #3455db; }
.kp-summary { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.kp-stat { display: flex; align-items: baseline; gap: 6px; flex-wrap: wrap; padding: 14px 16px; background: var(--surface-alt); }
.kp-stat + .kp-stat { border-left: 1px solid var(--line); }
.kp-stat strong { font-size: 24px; line-height: 1; color: var(--accent-dark); font-variant-numeric: tabular-nums; }
.kp-stat span { font-size: 12px; color: var(--muted); }
.kp-no-papers { margin-top: 12px; font-size: 13px; color: var(--warning); background: var(--warning-soft); padding: 10px 12px; border-radius: 8px; }
.kp-tabs { display: flex; gap: 20px; margin: 20px 0 6px; border-bottom: 1px solid var(--line); }
.kp-tabs button { border: 0; background: none; padding: 8px 0; margin-bottom: -1px; border-bottom: 2px solid transparent; font-size: 14px; color: var(--muted); }
.kp-tabs button.on { color: var(--ink); font-weight: 600; border-bottom-color: var(--accent); }
.kp-tabs button:disabled { opacity: .45; cursor: not-allowed; }
.heat-0 { background: var(--surface-alt); color: var(--muted); }
.heat-1 { background: var(--heat-1); color: var(--accent-dark); }
.heat-2 { background: var(--heat-2); color: var(--accent-dark); }
.heat-3 { background: var(--heat-3); color: #fff; }
.kp-rank { list-style: none; padding: 0; margin: 0; }
.kp-rank-head { display: flex; justify-content: space-between; align-items: center; padding: 10px 12px 6px 44px; font-size: 11px; color: var(--muted); }
.kp-paper-cols { display: flex; gap: 6px; flex-shrink: 0; }
.kp-paper-cols > span { width: 34px; text-align: center; }
.kp-rank-item { border-bottom: 1px solid var(--line); }
.kp-rank-row { width: 100%; display: flex; align-items: center; gap: 12px; padding: 12px; border: 0; background: none; text-align: left; color: var(--ink); border-radius: 8px; }
.kp-rank-row:hover { background: var(--surface-alt); }
.kp-rank-item.open .kp-rank-row { background: var(--soft); }
.kp-rank-no { width: 20px; flex-shrink: 0; text-align: center; font-size: 13px; font-weight: 700; color: var(--muted); font-variant-numeric: tabular-nums; }
.kp-rank-item:nth-child(-n+4) .kp-rank-no { color: var(--accent); }
.kp-rank-main { flex: 1; min-width: 0; display: grid; gap: 6px; }
.kp-rank-name { font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.kp-rank-name small { margin-left: 8px; font-size: 12px; font-weight: 400; color: var(--muted); }
.kp-rank-bar { display: flex; align-items: center; gap: 8px; }
.kp-rank-bar i { height: 6px; min-width: 6px; max-width: calc(100% - 48px); border-radius: 3px; background: var(--accent); opacity: .8; }
.kp-rank-bar b { flex-shrink: 0; white-space: nowrap; font-size: 11px; color: var(--accent-dark); font-variant-numeric: tabular-nums; }
.kp-cell.heat-0 { background: none; border: 1px dashed var(--line); }
.kp-cell { height: 26px; display: inline-flex; align-items: center; justify-content: center; border-radius: 6px; font-size: 12px; font-weight: 600; }
.kp-chapters { display: grid; }
.kp-chapter { padding: 16px 0; border-bottom: 1px solid var(--line); }
.kp-chapter-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.kp-chapter-head h3 { font-size: 14px; color: var(--ink); overflow-wrap: anywhere; }
.kp-chapter-count { flex-shrink: 0; font-size: 12px; color: var(--muted); }
.kp-chapter-count.hot { color: var(--accent); font-weight: 600; }
.kp-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.kp-chip { border: 1px solid transparent; padding: 4px 10px; border-radius: 999px; font-size: 12px; line-height: 1.5; }
.kp-chip.heat-0 { border-color: var(--line); background: var(--surface); }
.kp-chip:hover { border-color: var(--accent-line); }
.kp-chip.on { outline: 2px solid var(--accent); outline-offset: 1px; }
.kp-chapter-detail { margin-top: 12px; }
.kp-chip em { font-style: normal; font-weight: 700; margin-left: 5px; }
</style>
