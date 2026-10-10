<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { PracticeChatContext, WrongQuestion, WrongQuestionReview } from '../types'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{ courseId: string; revision: number; discussionBusy: boolean; activeQuestionId: string }>()
const emit = defineEmits<{ busy: [value: boolean]; change: []; contextChange: [context: PracticeChatContext | null] }>()
const questions = ref<WrongQuestion[]>([])
const loading = ref(false)
const saving = ref(false)
const deleting = ref('')
const error = ref('')
const formOpen = ref(false)
const stem = ref('')
const ownAnswer = ref('')
const answer = ref('')
const reviewId = ref('')
const reviewSelected = ref<string[]>([])
const reviewDraft = ref('')
const reviewResult = ref<WrongQuestionReview | null>(null)
const reviewSaving = ref(false)
const discussedId = ref('')
const busy = computed(() => loading.value || saving.value || reviewSaving.value || !!deleting.value)
const automaticCount = computed(() => questions.value.filter(q => q.reason === 'incorrect' && q.resolution.origin !== 'model').length)
const referenceCount = computed(() => questions.value.filter(q => q.reason === 'reference' || (q.reason === 'incorrect' && q.resolution.origin === 'model')).length)
let generation = 0
let disposed = false
let controller = new AbortController()
watch(busy, value => emit('busy', value), { immediate: true })
watch(() => props.courseId, () => {
  ++generation; controller.abort(); controller = new AbortController()
  questions.value = []; stem.value = ''; ownAnswer.value = ''; answer.value = ''; formOpen.value = false
  reviewId.value = ''; discussedId.value = ''; emit('contextChange', null)
  void load()
}, { immediate: true })
watch(() => props.revision, () => { if (!saving.value && !deleting.value && !reviewSaving.value) void load() })
watch(() => props.activeQuestionId, value => { if (!value) discussedId.value = '' })
watch([reviewSelected, reviewDraft], () => {
  reviewResult.value = null
  if (reviewId.value === discussedId.value) updateDiscussion()
}, { deep: true })
function active(token: number) { return !disposed && token === generation }
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason) }
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
async function load() {
  if (!props.courseId) return
  const token = ++generation
  loading.value = true; error.value = ''
  try {
    const value = await api<WrongQuestion[]>(`/courses/${props.courseId}/wrong-questions`, { signal: controller.signal })
    if (active(token)) {
      questions.value = value
      if (!value.some(item => item.id === reviewId.value)) reviewId.value = ''
      updateDiscussion()
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) loading.value = false }
}
async function add() {
  if (busy.value || !stem.value.trim()) return
  const token = generation
  saving.value = true; error.value = ''
  try {
    const value = await api<WrongQuestion>(`/courses/${props.courseId}/wrong-questions`, {
      method: 'POST', body: JSON.stringify({ stem: stem.value, own_answer: ownAnswer.value, answer: answer.value }),
      signal: controller.signal,
    })
    if (active(token)) {
      questions.value.unshift(value)
      stem.value = ''; ownAnswer.value = ''; answer.value = ''; formOpen.value = false
      emit('change')
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) saving.value = false }
}
async function remove(question: WrongQuestion) {
  if (busy.value) return
  const token = generation
  deleting.value = question.id; error.value = ''
  try {
    await api(`/courses/${props.courseId}/wrong-questions/${question.id}`, { method: 'DELETE', signal: controller.signal })
    if (active(token)) {
      questions.value = questions.value.filter(q => q.id !== question.id)
      if (reviewId.value === question.id) reviewId.value = ''
      if (discussedId.value === question.id) { discussedId.value = ''; emit('contextChange', null) }
      emit('change')
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) deleting.value = '' }
}
function startReview(item: WrongQuestion) {
  if (busy.value) return
  reviewId.value = item.id; reviewSelected.value = []; reviewDraft.value = ''; reviewResult.value = null; error.value = ''
  updateDiscussion()
}
function reviewLabel(value: WrongQuestionReview) {
  const matches = value.correct ?? value.reference_match
  const origin = value.grading_basis === 'model' || value.resolution.origin === 'model' ? '按模型参考答案' : '按原文答案'
  if (matches !== null) return origin + (matches ? '：本次答对' : '：本次答错')
  return value.resolution.answer ? '作答已保存，请自行对照参考答案' : '作答已保存，暂无可用于判题的答案'
}
function displayResolution(item: WrongQuestion) {
  return reviewId.value === item.id && reviewResult.value ? reviewResult.value.resolution : item.resolution
}
async function submitReview(item: WrongQuestion) {
  if (busy.value || reviewId.value !== item.id) return
  const token = generation
  reviewSaving.value = true; error.value = ''
  try {
    const value = await api<WrongQuestionReview>(`/courses/${props.courseId}/wrong-questions/${item.id}/reviews`, {
      method: 'POST', body: JSON.stringify({ selected: reviewSelected.value, own_answer: reviewDraft.value }), signal: controller.signal,
    })
    if (active(token)) {
      reviewResult.value = value
      const current = questions.value.find(q => q.id === item.id)
      if (current) current.reviews = [value, ...(current.reviews || [])]
      emit('change'); updateDiscussion()
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) reviewSaving.value = false }
}
function updateDiscussion() {
  if (!discussedId.value) return
  const item = questions.value.find(value => value.id === discussedId.value)
  if (!item) { discussedId.value = ''; emit('contextChange', null); return }
  const latest = item.reviews?.[0]
  emit('contextChange', { wrong_question_id: item.id, question_id: item.source_question_id,
    filename: item.question.filename, number: item.question.number || '手动录入', original_number: item.question.number,
    stem: item.question.stem, options: item.question.options,
    selected: [...(reviewId.value === item.id ? reviewSelected.value : latest?.selected || item.selected)],
    own_answer: reviewId.value === item.id ? reviewDraft.value : latest?.own_answer ?? item.own_answer })
}
function discuss(item: WrongQuestion) {
  if (props.discussionBusy) return
  discussedId.value = item.id; updateDiscussion()
}
onUnmounted(() => { disposed = true; controller.abort(); emit('busy', false); emit('contextChange', null) })
</script>

<template>
  <section class="panel wrong-panel">
    <div class="section-top">
      <div><h2>收录与重练</h2><p class="muted">与原文答案或模型答案不一致的选择题自动收录，并标明来源；也可以手动标记或直接录入。</p></div>
      <div class="inline-actions"><button class="button primary" :disabled="busy" :aria-expanded="formOpen" @click="formOpen = !formOpen">{{ formOpen ? '收起录入' : '＋ 添加错题' }}</button><button class="text-button" :disabled="busy" @click="load">刷新 ↻</button></div>
    </div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <form v-if="formOpen" class="wrong-form" @submit.prevent="add">
      <label for="wrong-stem">题目内容 <span class="muted">必填，可粘贴题干和选项</span></label>
      <textarea id="wrong-stem" v-model="stem" rows="5" maxlength="20000" required :disabled="busy" placeholder="输入需要复习的题目，支持 Markdown 和数学公式。"></textarea>
      <label for="wrong-own-answer">你的作答（可选）</label>
      <textarea id="wrong-own-answer" v-model="ownAnswer" rows="3" maxlength="20000" :disabled="busy" placeholder="记录当时的答案或解题思路。"></textarea>
      <label for="wrong-answer">参考答案与解析（可选）</label>
      <textarea id="wrong-answer" v-model="answer" rows="4" maxlength="20000" :disabled="busy" placeholder="填写参考答案或解析。"></textarea>
      <div class="wrong-form-actions"><button class="button primary" :disabled="busy || !stem.trim()">{{ saving ? '保存中…' : '保存到错题本' }}</button><button type="button" class="text-button" :disabled="busy" @click="formOpen = false">取消</button></div>
    </form>
    <p v-if="loading" class="loading-bar" role="status">正在读取错题…</p>
    <div v-else-if="!questions.length && !error" class="empty-state compact"><div class="empty-symbol">☆</div><h3>还没有收录错题</h3><p>在线练习答错后会自动收录，也可以点击题目旁的“加入错题本”或在这里添加。</p></div>
    <template v-else-if="questions.length">
      <div class="wrong-summary"><span>共 {{ questions.length }} 道 · 按原文答案自动收录 {{ automaticCount }} 道 · 按模型参考答案答错 {{ referenceCount }} 道 · 手动加入 {{ questions.length - automaticCount - referenceCount }} 道</span><span>按最近收录排序</span></div>
      <div class="wrong-list">
        <article v-for="(item, index) in questions" :key="item.id" class="wrong-card">
          <div class="wrong-card-top"><div class="wrong-meta"><strong>#{{ index + 1 }}</strong><span class="badge neutral">{{ (item.reason === 'reference' || (item.reason === 'incorrect' && item.resolution.origin === 'model')) ? '按模型参考答案答错' : item.reason === 'incorrect' ? '按原文答案答错' : '手动加入' }}</span><span>{{ item.question.question_type === 'single_choice' ? '单选题' : item.question.question_type === 'multiple_choice' ? '多选题' : '其他题型' }}</span><span>{{ date(item.created_at) }}</span></div><button class="text-button danger" :disabled="busy" :aria-label="`删除第 ${index + 1} 道错题`" @click="remove(item)">{{ deleting === item.id ? '删除中…' : '删除错题' }}</button></div>
          <p class="wrong-source">{{ item.question.filename }}<span v-if="item.question.section"> · {{ item.question.section }}</span><span v-if="item.question.number"> · 原题 {{ item.question.number }}</span><span v-if="item.question.locator.label"> · {{ item.question.locator.label }}</span></p>
          <MarkdownContent :content="item.question.stem" />
          <p v-if="item.resolution.external_knowledge || item.question.locator.external_knowledge" class="wrong-source"><span class="badge neutral">含模型自行搜索的知识</span> · 联网来源保存在参考解析中</p>
          <p v-if="item.resolution.model_knowledge || item.question.locator.model_knowledge" class="wrong-source"><span class="badge neutral">含模型已有知识的补充</span></p>
          <fieldset v-if="reviewId === item.id && item.question.options.length" class="wrong-options wrong-review-options"><legend>本次作答</legend><label v-for="option in item.question.options" :key="option.key" :class="['wrong-option', { chosen: reviewSelected.includes(option.key) }]"><input v-if="item.question.question_type === 'multiple_choice'" v-model="reviewSelected" type="checkbox" :value="option.key" :disabled="reviewSaving" /><input v-else type="radio" :name="`review-${item.id}`" :value="option.key" :checked="reviewSelected.includes(option.key)" :disabled="reviewSaving" @change="reviewSelected = [option.key]" /><strong>{{ option.key }}</strong><MarkdownContent :content="option.text" /></label></fieldset>
          <div v-else-if="item.question.options.length" class="wrong-options"><div v-for="option in item.question.options" :key="option.key" :class="['wrong-option', { chosen: item.selected.includes(option.key) }]"><strong>{{ option.key }}</strong><MarkdownContent :content="option.text" /></div></div>
          <div v-if="reviewId === item.id && !item.question.options.length" class="wrong-review-draft"><label :for="`review-draft-${item.id}`">本次作答</label><textarea :id="`review-draft-${item.id}`" v-model="reviewDraft" rows="4" maxlength="4000" :disabled="reviewSaving" placeholder="写下你的答案或解题过程，提交后对照解析。"></textarea></div>
          <div class="wrong-review-actions"><button type="button" class="button subtle" :disabled="busy" @click="startReview(item)">{{ reviewId === item.id ? '清空并重新作答' : '重新作答' }}</button><button v-if="reviewId === item.id" type="button" class="button primary" :disabled="busy || !!reviewResult || (item.question.options.length ? !reviewSelected.length : !reviewDraft.trim())" @click="submitReview(item)">{{ reviewSaving ? '正在保存…' : '提交本次作答' }}</button><button v-if="reviewId === item.id" type="button" class="text-button" :disabled="busy" @click="reviewId = ''; reviewResult = null; updateDiscussion()">结束重练</button><button type="button" class="text-button" :disabled="discussionBusy" @click="discuss(item)">{{ discussedId === item.id ? '正在讨论这题' : '讨论这题' }}</button></div>
          <p v-if="reviewId === item.id && reviewResult" class="notice info" role="status">{{ reviewLabel(reviewResult) }}</p>
          <details v-if="item.reviews?.length && reviewId !== item.id" class="wrong-details"><summary>重练记录 · {{ item.reviews.length }} 次 · {{ reviewLabel(item.reviews[0]) }}</summary><div v-for="attempt in item.reviews" :key="attempt.id" class="wrong-review-history"><p>{{ date(attempt.created_at) }} · {{ reviewLabel(attempt) }}</p><p v-if="attempt.selected.length">本次选择：{{ attempt.selected.join('、') }}</p><MarkdownContent v-if="attempt.own_answer" :content="attempt.own_answer" /></div></details>
          <details v-if="item.question.translation" class="wrong-details"><summary>查看中文翻译</summary><MarkdownContent :content="item.question.translation.stem" /><div v-for="option in item.question.translation.options" :key="option.key" class="wrong-option"><strong>{{ option.key }}</strong><MarkdownContent :content="option.text" /></div></details>
          <div v-if="reviewId !== item.id && (item.selected.length || item.own_answer)" class="wrong-own-answer"><p v-if="item.selected.length">当时选择：{{ item.selected.join('、') }}</p><template v-if="item.own_answer"><p>你的作答</p><MarkdownContent :content="item.own_answer" /></template></div>
          <details v-if="(displayResolution(item).answer || displayResolution(item).choices?.length) && (reviewId !== item.id || reviewResult)" :key="`${item.id}:${reviewId === item.id ? reviewResult?.id || 'hidden' : 'original'}`" class="wrong-details" :open="reviewId === item.id && !!reviewResult"><summary>查看参考答案与解析</summary><p class="wrong-source">{{ displayResolution(item).origin === 'uploaded' ? '上传答案' : displayResolution(item).origin === 'embedded' ? '原题自带答案' : displayResolution(item).origin === 'model' ? '模型参考答案' : '手动填写' }}<span v-if="displayResolution(item).source"> · {{ displayResolution(item).source }}</span></p><p v-if="displayResolution(item).choices?.length" class="wrong-correct">{{ displayResolution(item).origin === 'model' ? '模型参考选项' : '原文答案选项' }}：{{ displayResolution(item).choices?.join('、') }}</p><MarkdownContent :content="displayResolution(item).answer || ''" hide-citations /></details>
          <p v-else class="muted wrong-no-answer">{{ reviewId === item.id && !reviewResult ? '旧作答和解析已隐藏，提交本次作答后再对照。' : item.source_document_id ? '暂未保存参考答案。可在在线练习中显示答案后自动补充。' : '暂未保存参考答案。' }}</p>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.wrong-form { display: grid; gap: 10px; background: var(--surface-alt); border: 1px solid var(--line); border-radius: 10px; padding: 22px; margin-bottom: 24px; }
.wrong-form label { font-size: 13px; color: #53617b; }
.wrong-form label:not(:first-child) { margin-top: 10px; }
.wrong-form textarea { width: 100%; resize: vertical; line-height: 1.9; }
.wrong-form-actions { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; margin-top: 10px; }
.wrong-summary { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin: 20px 0; padding-bottom: 16px; border-bottom: 1px solid var(--line); font-size: 12px; color: var(--muted); line-height: 1.9; }
.wrong-list { display: grid; gap: 22px; }
.wrong-card { border: 1px solid var(--line); border-radius: 10px; padding: 24px; min-width: 0; background: var(--surface); }
.wrong-card-top { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 14px; }
.wrong-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; font-size: 12px; color: var(--muted); }
.wrong-meta > strong { color: var(--accent); font-size: 15px; }
.wrong-card-top > .text-button { flex-shrink: 0; }
.wrong-source { font-size: 12px; color: var(--muted); margin: 8px 0 20px; overflow-wrap: anywhere; }
.wrong-card > .markdown-content { font-size: 16px; }
.wrong-options { display: grid; gap: 10px; margin: 22px 0; min-width: 0; }
.wrong-option { display: flex; align-items: flex-start; gap: 12px; padding: 13px 15px; border: 1px solid var(--line); border-radius: 8px; min-width: 0; background: var(--surface-alt); }
.wrong-option.chosen { background: var(--soft); border-color: #bbcaf1; }
.wrong-option > strong { font-size: 14px; color: var(--accent); padding-top: 2px; }
.wrong-option > .markdown-content { flex: 1; min-width: 0; }
.wrong-review-options { border: 0; padding: 0; }
.wrong-review-options legend, .wrong-review-draft label { font-size: 13px; color: #53617b; margin-bottom: 8px; }
.wrong-review-options label { cursor: pointer; background: var(--surface); }
.wrong-review-options label:hover { border-color: #a7b8d5; }
.wrong-review-options label:focus-within { outline: 2px solid var(--accent); outline-offset: 3px; }
.wrong-review-options input { width: 16px; height: 16px; flex-shrink: 0; margin: 7px 0 0; accent-color: var(--accent); }
.wrong-review-options .chosen { background: #f0f4ff; border-color: var(--accent); box-shadow: inset 3px 0 var(--accent); }
.wrong-review-draft { display: grid; gap: 10px; margin: 20px 0; }
.wrong-review-draft textarea { width: 100%; resize: vertical; line-height: 1.9; }
.wrong-review-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin: 22px 0; }
.wrong-review-history { border-bottom: 1px solid var(--line); padding: 12px 0; font-size: 13px; color: var(--muted); }
.wrong-own-answer { border-left: 3px solid #9ab0e5; background: var(--surface-alt); padding: 14px 18px; margin: 20px 0; }
.wrong-own-answer > p { font-size: 12px; color: var(--muted); margin: 0 0 10px; }
.wrong-details { border-top: 1px solid var(--line); padding-top: 16px; margin-top: 22px; }
.wrong-details summary { cursor: pointer; font-size: 13px; color: var(--accent); margin-bottom: 12px; border-radius: 3px; line-height: 1.8; }
.wrong-details[open] { padding-bottom: 8px; }
.wrong-correct { font-size: 13px; color: var(--success); margin-bottom: 14px; }
.wrong-no-answer { font-size: 13px; margin-top: 18px; }
@media (max-width: 600px) {
  .wrong-card { padding: 18px 14px; }
  .wrong-form { padding: 16px; }
  .wrong-form textarea, .wrong-review-draft textarea { font-size: 16px; }
  .wrong-card-top { flex-wrap: wrap; }
  .wrong-meta { gap: 8px; }
  .wrong-option { padding: 12px; gap: 10px; }
}
</style>
