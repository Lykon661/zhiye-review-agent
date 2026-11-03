<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ExamQuestionHit, KnowledgePointNode } from '../types'
import { kindLabels } from '../types'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{
  point: KnowledgePointNode
  parent: KnowledgePointNode | null
  questions: (ExamQuestionHit & { via: string | null })[]
  childCounts: Record<string, number>
  hasPapers: boolean
  canJump: boolean
}>()
const emit = defineEmits<{ select: [id: string]; jump: [] }>()

const openQuestions = ref(new Set<string>())
watch(() => props.point.id, () => { openQuestions.value = new Set() })

function name(point: KnowledgePointNode) {
  return point.name_zh || point.title
}

function toggleQuestion(id: string) {
  const next = new Set(openQuestions.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openQuestions.value = next
}

function stemText(stem: string) {
  return stem.replace(/!\[[^\]]*\]\([^)]*\)/g, '［图］')
}

// Pages from one courseware collapse into a single entry.
function sourceDocuments() {
  const seen = new Map<string, KnowledgePointNode['sources'][number]>()
  for (const source of props.point.sources) if (!seen.has(source.document_id)) seen.set(source.document_id, source)
  return [...seen.values()]
}
</script>

<template>
  <div class="kpd">
    <div class="kpd-head">
      <p v-if="parent" class="kpd-parent"><button type="button" @click="emit('select', parent.id)">← {{ name(parent) }}</button></p>
      <button type="button" class="kpd-jump" :disabled="!canJump" @click="emit('jump')">在笔记中查看 →</button>
    </div>

    <div v-if="point.children.length" class="kpd-children">
      <button v-for="child in point.children" :key="child.id" type="button" :class="['kpd-child', { hot: childCounts[child.id] }]" @click="emit('select', child.id)">
        {{ name(child) }}<em v-if="childCounts[child.id]">{{ childCounts[child.id] }}</em>
      </button>
    </div>

    <ol v-if="questions.length" class="kpd-questions">
      <li v-for="question in questions" :key="question.question_id" :class="['kpd-question', { open: openQuestions.has(question.question_id) }]">
        <button type="button" class="kpd-question-head" :aria-expanded="openQuestions.has(question.question_id)" @click="toggleQuestion(question.question_id)">
          <span class="kpd-paper">{{ question.paper }} · 第 {{ question.number }} 题</span>
          <span class="kpd-term">{{ question.via || question.term }}</span>
          <span class="kpd-caret" aria-hidden="true">{{ openQuestions.has(question.question_id) ? '收起' : '展开' }}</span>
        </button>
        <div class="kpd-stem"><MarkdownContent class="kpd-stem-text" :content="stemText(question.stem)" hide-citations /></div>
        <ul v-if="question.options.length && openQuestions.has(question.question_id)" class="kpd-options">
          <li v-for="option in question.options" :key="option.key"><b>{{ option.key }}</b><MarkdownContent :content="option.text" hide-citations /></li>
        </ul>
      </li>
    </ol>
    <p v-else class="kpd-empty">{{ hasPapers ? '历年试卷里没有找到提到这个知识点的题目。' : '上传历年试卷后，这里会列出考过这个知识点的原题。' }}</p>

    <p class="kpd-sources">
      <span>课件出处</span>
      <template v-if="point.sources.length">
        <span v-for="doc in sourceDocuments()" :key="doc.document_id" class="kpd-source">{{ kindLabels[doc.kind] || doc.kind }} · {{ doc.filename }}</span>
      </template>
      <span v-else class="kpd-source muted">未在课件中定位到</span>
    </p>
  </div>
</template>

<style scoped>
.kpd { margin: 4px 0 12px; padding: 14px 16px; border-radius: 10px; background: var(--surface); border: 1px solid var(--accent-line); display: grid; gap: 12px; min-width: 0; }
.kpd-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.kpd-parent button, .kpd-jump { border: 0; background: none; padding: 0; font-size: 12px; color: var(--accent); }
.kpd-jump { margin-left: auto; }
.kpd-jump:disabled { color: var(--muted); }
.kpd-children { display: flex; flex-wrap: wrap; gap: 6px; }
.kpd-child { padding: 3px 9px; border-radius: 999px; border: 1px solid var(--line); background: var(--surface-alt); font-size: 12px; color: var(--ink); }
.kpd-child.hot { background: var(--soft); border-color: var(--accent-line); color: var(--accent-dark); }
.kpd-child em { font-style: normal; font-weight: 700; margin-left: 4px; }
.kpd-questions { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.kpd-question { border: 1px solid var(--line); border-radius: 8px; background: var(--surface-alt); min-width: 0; }
.kpd-question-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 8px 12px 0; border: 0; background: none; text-align: left; font-size: 12px; color: var(--muted); }
.kpd-paper { color: var(--ink); font-weight: 600; overflow-wrap: anywhere; }
.kpd-term { padding: 1px 7px; border-radius: 5px; background: var(--soft); color: var(--accent-dark); white-space: nowrap; }
.kpd-caret { margin-left: auto; color: var(--accent); white-space: nowrap; }
.kpd-stem { padding: 4px 12px 10px; font-size: 13px; line-height: 1.6; overflow-x: auto; }
.kpd-question:not(.open) .kpd-stem-text { max-height: 3.2em; overflow: hidden; -webkit-mask-image: linear-gradient(#000 60%, transparent); mask-image: linear-gradient(#000 60%, transparent); }
.kpd-stem :deep(p) { margin: 0; }
.kpd-options { list-style: none; margin: 0; padding: 0 12px 10px; display: grid; gap: 4px; font-size: 12px; color: #43516b; }
.kpd-options li { display: flex; gap: 8px; align-items: baseline; }
.kpd-options b { color: var(--accent-dark); }
.kpd-options :deep(p) { margin: 0; }
.kpd-empty { font-size: 12px; color: var(--muted); }
.kpd-sources { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); padding-top: 10px; border-top: 1px dashed var(--line); }
.kpd-source { padding: 2px 8px; border-radius: 5px; background: var(--surface-alt); border: 1px solid var(--line); color: var(--ink); overflow-wrap: anywhere; }
</style>
