<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { AgentMemory, Chapter, Document } from '../types'

const props = defineProps<{ courseId: string; chapters: Chapter[]; modelConfigured: boolean; working: boolean; wrongRevision: number }>()
const emit = defineEmits<{ generated: [document: Document]; busy: [value: boolean] }>()
const mode = ref<'topics' | 'weakness'>('topics')
const chapterIds = ref<string[]>([])
const topic = ref('')
const weakIds = ref<string[]>([])
const count = ref(5)
const questionType = ref('mixed')
const difficulty = ref('medium')
const memory = ref<AgentMemory | null>(null)
const memoryLoading = ref(false)
const memoryError = ref('')
const error = ref('')
const submitting = ref(false)
const disabled = computed(() => props.working || submitting.value)
const weaknesses = computed(() => memory.value?.learning.filter(item => item.status === 'needs_review') || [])
const canGenerate = computed(() => props.modelConfigured && !disabled.value
  && (mode.value === 'topics' ? chapterIds.value.length > 0 || !!topic.value.trim() : !memoryLoading.value && !memoryError.value && weaknesses.value.length > 0))
let serial = 0
let disposed = false
const controller = new AbortController()

async function loadMemory() {
  const token = ++serial, course = props.courseId
  memoryLoading.value = true; memoryError.value = ''
  try {
    const value = await api<AgentMemory>(`/courses/${course}/memory`, { signal: controller.signal })
    if (disposed || token !== serial || course !== props.courseId) return
    memory.value = value
    const valid = new Set(weaknesses.value.map(item => item.id))
    weakIds.value = weakIds.value.filter(id => valid.has(id))
  } catch (reason) {
    if (!disposed && token === serial && course === props.courseId) memoryError.value = reason instanceof Error ? reason.message : String(reason)
  } finally { if (!disposed && token === serial) memoryLoading.value = false }
}
watch(mode, value => { error.value = ''; if (value === 'weakness') void loadMemory() })
watch(() => props.wrongRevision, () => { if (mode.value === 'weakness') void loadMemory() })
watch(() => props.chapters, chapters => {
  const valid = new Set(chapters.map(chapter => chapter.id))
  chapterIds.value = chapterIds.value.filter(id => valid.has(id))
})
watch(() => props.courseId, () => {
  ++serial; memory.value = null; memoryLoading.value = false; memoryError.value = ''; error.value = ''
  chapterIds.value = []; weakIds.value = []; topic.value = ''
  if (mode.value === 'weakness') void loadMemory()
})
async function generate() {
  if (!canGenerate.value) return
  if (!Number.isInteger(count.value) || count.value < 1 || count.value > 10) { error.value = '题数请填写 1–10 的整数'; return }
  const course = props.courseId
  submitting.value = true; emit('busy', true); error.value = ''
  try {
    const document = await api<Document>(`/courses/${course}/practice/generate`, {
      method: 'POST', signal: controller.signal,
      body: JSON.stringify({ mode: mode.value, chapter_ids: mode.value === 'topics' ? chapterIds.value : [],
        topic: mode.value === 'topics' ? topic.value.trim() : '', weak_topic_ids: mode.value === 'weakness' ? weakIds.value : [],
        count: count.value, question_type: questionType.value, difficulty: difficulty.value }),
    })
    if (!disposed && course === props.courseId) emit('generated', document)
  } catch (reason) {
    if (!disposed && course === props.courseId) {
      error.value = reason instanceof Error ? reason.message : String(reason)
      if (mode.value === 'weakness') void loadMemory()
    }
  } finally { submitting.value = false; if (!disposed) emit('busy', false) }
}
onUnmounted(() => { disposed = true; controller.abort(); emit('busy', false) })
</script>

<template>
  <section class="panel practice-generator">
    <div class="section-top"><div><h2>AI 生题</h2><p class="muted">按章节、知识点或记忆中的薄弱点练习，题目和作答进度自动保存。</p></div></div>
    <div class="generation-modes" role="group" aria-label="生题方式">
      <button :class="['button', mode === 'topics' ? 'primary' : 'subtle']" :aria-pressed="mode === 'topics'" :disabled="disabled" @click="mode = 'topics'">章节／知识点</button>
      <button :class="['button', mode === 'weakness' ? 'primary' : 'subtle']" :aria-pressed="mode === 'weakness'" :disabled="disabled" @click="mode = 'weakness'">我的薄弱点</button>
    </div>
    <form class="generation-form" @submit.prevent="generate">
      <template v-if="mode === 'topics'">
        <fieldset v-if="chapters.length" class="generation-topics"><legend>选择章节（可选，最多 6 个）</legend>
          <label v-for="chapter in chapters" :key="chapter.id" class="generation-chip"><input v-model="chapterIds" type="checkbox" :value="chapter.id" :disabled="disabled || (!chapterIds.includes(chapter.id) && chapterIds.length >= 6)" /><span>{{ chapter.title }}</span></label>
        </fieldset>
        <label class="generation-topic-input">知识点（可单独填写，也可缩小所选章节的范围）<textarea v-model="topic" rows="2" maxlength="400" :disabled="disabled" placeholder="例如：缓存的直接映射、Booth 乘法、二叉树遍历"></textarea></label>
      </template>
      <div v-else class="generation-memory">
        <div class="generation-memory-heading"><span>本课程需要巩固的知识点</span><button type="button" class="text-button" :disabled="disabled || memoryLoading" @click="loadMemory">{{ memoryLoading ? '加载中…' : '刷新记忆 ↻' }}</button></div>
        <p v-if="memoryError" class="notice error" role="alert">{{ memoryError }}</p>
        <p v-else-if="memoryLoading && !memory" class="muted" role="status">正在读取学习记忆…</p>
        <fieldset v-else-if="weaknesses.length" class="generation-topics"><legend class="muted">不勾选时，自动优先练习错误次数较多的薄弱点；可指定最多 10 个。</legend>
          <label v-for="item in weaknesses" :key="item.id" class="generation-chip"><input v-model="weakIds" type="checkbox" :value="item.id" :disabled="disabled || (!weakIds.includes(item.id) && weakIds.length >= 10)" /><span>{{ item.topic }}<small>答错 {{ item.incorrect_count }} 次{{ item.grading_basis === 'model' ? ' · 模型参考判题' : '' }}</small></span></label>
        </fieldset>
        <p v-else class="muted">当前课程还没有待复习的薄弱点。先作答积累记忆，或切换到“章节／知识点”生题。</p>
        <p v-if="memory?.updating" class="muted">学习记忆正在整理，稍后可刷新查看最新知识点。</p>
      </div>
      <div class="generation-options">
        <label>题数<input v-model.number="count" type="number" min="1" max="10" step="1" required :disabled="disabled" /></label>
        <label>题型<select v-model="questionType" :disabled="disabled"><option value="mixed">混合题型</option><option value="single_choice">单选题</option><option value="multiple_choice">多选题</option><option value="other">简答／计算题</option></select></label>
        <label>难度<select v-model="difficulty" :disabled="disabled"><option value="basic">基础</option><option value="medium">适中</option><option value="advanced">进阶</option></select></label>
        <button class="button primary" type="submit" :disabled="!canGenerate">{{ submitting ? '正在创建练习…' : '生成练习' }}</button>
      </div>
      <p v-if="!modelConfigured" class="muted">请先在右上角配置模型。</p>
      <p v-if="error" class="notice error" role="alert">{{ error }}</p>
      <p class="generation-hint muted">优先依据当前课程资料，资料不足时使用模型知识。答案由 AI 生成，仅供参考；选择题可提交判题，简答／计算题自行对照。</p>
    </form>
  </section>
</template>

<style scoped>
.generation-modes, .generation-options { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; }
.generation-form { display: grid; gap: 18px; margin-top: 20px; }
.generation-topics { border: 0; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 10px; max-height: 230px; overflow: auto; }
.generation-topics legend { font-size: 13px; margin-bottom: 12px; line-height: 1.8; }
.generation-chip { display: flex; align-items: start; gap: 8px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; font-size: 13px; line-height: 1.6; background: var(--surface-alt); cursor: pointer; max-width: 100%; overflow-wrap: anywhere; }
.generation-chip:has(input:checked) { border-color: var(--accent); background: var(--soft); }
.generation-chip input { accent-color: var(--accent); margin-top: 4px; flex-shrink: 0; }
.generation-chip small { display: block; color: var(--muted); font-size: 11px; }
.generation-topic-input, .generation-options label { display: grid; gap: 8px; font-size: 13px; color: var(--muted); }
.generation-topic-input textarea { width: 100%; resize: vertical; line-height: 1.8; }
.generation-options input { width: 85px; }
.generation-memory { display: grid; gap: 14px; }
.generation-memory-heading { display: flex; justify-content: space-between; align-items: center; gap: 12px; font-size: 13px; }
.generation-hint { font-size: 12px; line-height: 1.8; margin: 0; }
@media (max-width: 600px) { .generation-options label { flex: 1; } .generation-options select { width: 100%; } .generation-options button { width: 100%; } }
</style>
