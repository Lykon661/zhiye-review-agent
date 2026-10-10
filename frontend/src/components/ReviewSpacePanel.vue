<script setup lang="ts">
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import type { Chapter, ReviewSpace, ReviewSpaceMessage, ReviewSpaceSummary } from '../types'
import { useReviewSpaceDraft } from '../useReviewSpaceDraft'
import MarkdownContent from './MarkdownContent.vue'
import UiIcon from './UiIcon.vue'
import TokenUsage from './TokenUsage.vue'
const props = defineProps<{ courseId: string; courseName: string; chapters?: Chapter[]; modelConfigured: boolean; blocked: boolean }>()
const emit = defineEmits<{ busy: [value: boolean]; change: [] }>()
const spaces = ref<ReviewSpaceSummary[]>([]), current = ref<ReviewSpace | null>(null)
const loading = ref(false), requesting = ref(false), error = ref(''), pollError = ref('')
const input = ref(''), selected = ref<string[]>([])
const ownAnswer = ref(''), editingId = ref(''), titleDraft = ref(''), deleteId = ref('')
const usedHint = ref(false), seenAnswer = ref(false)
const draft = useReviewSpaceDraft({ current, selected, ownAnswer, usedHint, seenAnswer })
const draftStatus = draft.status, draftError = draft.error
const thread = ref<HTMLElement | null>(null)
const scopeOpen = ref(false), scopeDraft = ref<string[]>([]), newChapterIds = ref<string[]>([])
const scopeOptions = computed(() => (props.chapters ?? []).filter(chapter => (!chapter.kind || chapter.kind === 'knowledge') && chapter.block_count > 0))
const activeChapterIds = computed(() => current.value ? current.value.chapter_ids ?? [] : newChapterIds.value)
const activeChapterTitles = computed(() => current.value?.chapters ?? scopeOptions.value.filter(chapter => activeChapterIds.value.includes(chapter.id)).map(chapter => chapter.title))
const scopeLabel = computed(() => activeChapterIds.value.length ? `已选 ${activeChapterIds.value.length} 个章节` : '全部章节')
const working = computed(() => requesting.value || current.value?.status === 'processing')
const backgroundWorking = computed(() => spaces.value.some(item => item.id !== current.value?.id && item.status === 'processing'))
const courseWorking = computed(() => working.value || backgroundWorking.value)
const canEditScope = computed(() => !working.value && !loading.value && !props.blocked && current.value?.status !== 'completed')
const canSend = computed(() => !scopeOpen.value && !courseWorking.value && !loading.value && !props.blocked && props.modelConfigured && current.value?.status !== 'completed')
const scopeHasGaps = computed(() => {
  const step = current.value?.messages.at(-1)?.step
  return !!(activeChapterIds.value.length && step?.kind === 'explanation' && step.scope_has_gaps
    && JSON.stringify(step.search_scope?.chapter_ids) === JSON.stringify(activeChapterIds.value))
})
function editScope() { scopeDraft.value = [...activeChapterIds.value]; scopeOpen.value = !scopeOpen.value }
async function applyScope(ids = scopeDraft.value) {
  if (!canEditScope.value) return false
  if (!current.value) { newChapterIds.value = [...ids]; scopeOpen.value = false; return true }
  const id = current.value.id
  requesting.value = true; error.value = ''; ++serial; clearTimeout(pollTimer); await flushDraft()
  if (disposed || current.value?.id !== id) { requesting.value = false; return false }
  try {
    const value = await api<ReviewSpace>(`/review-spaces/${id}/scope`, { method: 'PUT', body: JSON.stringify({ chapter_ids: ids, revision: current.value.revision }) })
    if (disposed || current.value?.id !== id) return false
    apply(value); scopeOpen.value = false; return true
  } catch (reason) {
    if (!disposed) { error.value = message(reason); if (reason instanceof ApiError && reason.status === 409) { try { apply(await api<ReviewSpace>(`/review-spaces/${id}`)) } catch { /* retain error */ } } }
    return false
  } finally { if (!disposed) { requesting.value = false; schedulePoll() } }
}
async function expandScope() {
  if (!canSend.value || !scopeHasGaps.value) return
  const feedback = [...(current.value?.messages ?? [])].reverse().find(item => item.role === 'user')?.content
  if (feedback && await applyScope([])) await perform('advance', { feedback })
}
const statusLabels: Record<string, string> = { processing: '正在回复', waiting_answer: '可以作答或继续聊', reading: '复习中', feedback: '复习中', paused: '已保存', failed: '需要重试', completed: '已结束' }
const actionLabels: Record<string, string> = { read_learning: '查看学习记录', search_materials: '查阅课程资料', search_web: '联网补充', list_questions: '查找题目', select_question: '选择题目', generate_question: '生成题目', explain: '回复讲解', finish: '保存复习小结' }
let active = true, disposed = false, serial = 0
let readController = new AbortController()
let pollTimer: ReturnType<typeof setTimeout> | undefined
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason) }
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
function apply(value: ReviewSpace, force = false) {
  if (disposed || value.course_id !== props.courseId) return
  const changed = current.value?.id !== value.id || current.value?.step?.id !== value.step?.id
  if (current.value?.id !== value.id || force) scopeOpen.value = false
  const resumedAnswer = value.status === 'waiting_answer' && current.value?.status !== 'waiting_answer'
  draft.settled(current.value, value)
  current.value = value; spaces.value = [{ ...value }, ...spaces.value.filter(item => item.id !== value.id)]
  try { localStorage.setItem(`review-space:${props.courseId}`, value.id) } catch { /* optional selection */ }
  if (changed || force || resumedAnswer) draft.restore(value)
  if (value.step?.revealed) seenAnswer.value = true
  schedulePoll()
}
function schedulePoll() {
  clearTimeout(pollTimer)
  if (!disposed && (current.value || courseWorking.value) && (active || courseWorking.value)) pollTimer = setTimeout(() => { void refreshCurrent() }, courseWorking.value ? 1500 : 12000)
}
async function refreshCurrent() {
  const id = current.value?.id, course = props.courseId, token = serial
  if ((!id && !backgroundWorking.value) || requesting.value || loading.value || disposed) { schedulePoll(); return }
  try {
    const [values, value] = await Promise.all([
      api<ReviewSpaceSummary[]>(`/courses/${course}/review-spaces`, { signal: readController.signal }),
      id ? api<ReviewSpace>(`/review-spaces/${id}`, { signal: readController.signal }) : Promise.resolve(null),
    ])
    // A reply finishing in another conversation only updates its sidebar status.
    if (!disposed && token === serial && course === props.courseId && current.value?.id === id && !requesting.value) {
      spaces.value = values
      if (value) apply(value)
      pollError.value = ''
    }
  } catch (reason) { if (!disposed && token === serial && (reason as Error).name !== 'AbortError') pollError.value = message(reason) }
  finally { if (!disposed && token === serial) schedulePoll() }
}
async function load() {
  const course = props.courseId, token = ++serial; loading.value = true
  try {
    const values = await api<ReviewSpaceSummary[]>(`/courses/${course}/review-spaces`, { signal: readController.signal })
    if (disposed || token !== serial || course !== props.courseId) return
    spaces.value = values
    let remembered = ''; try { remembered = localStorage.getItem(`review-space:${course}`) || '' } catch { /* optional selection */ }
    const chosen = values.find(v => v.id === remembered) || values.find(v => v.status === 'processing')
    if (chosen) { const value = await api<ReviewSpace>(`/review-spaces/${chosen.id}`, { signal: readController.signal }); if (!disposed && token === serial) apply(value, true) }
  } catch (reason) { if (!disposed && token === serial && (reason as Error).name !== 'AbortError') error.value = message(reason) }
  finally { if (!disposed && token === serial) { loading.value = false; schedulePoll() } }
}
async function selectSpace(id: string) {
  if (requesting.value || loading.value) return
  const token = ++serial; loading.value = true; error.value = ''; pollError.value = ''; clearTimeout(pollTimer); await flushDraft()
  try { const value = await api<ReviewSpace>(`/review-spaces/${id}`, { signal: readController.signal }); if (!disposed && token === serial) { apply(value, true); input.value = '' } }
  catch (reason) { if (!disposed && token === serial) error.value = message(reason) }
  finally { if (!disposed && token === serial) { loading.value = false; schedulePoll() } }
}
async function newConversation() {
  if (requesting.value || loading.value) return
  loading.value = true; ++serial; clearTimeout(pollTimer); await flushDraft()
  if (!disposed) {
    current.value = null; input.value = ''; error.value = ''; pollError.value = ''; newChapterIds.value = []; scopeOpen.value = false
    try { localStorage.removeItem(`review-space:${props.courseId}`) } catch { /* optional selection */ }
    loading.value = false
    schedulePoll()
  }
}
function editTitle(item: ReviewSpaceSummary) { editingId.value = item.id; titleDraft.value = item.title || item.goal.slice(0, 80); deleteId.value = '' }
async function manageRecord(item: ReviewSpaceSummary, action: 'rename' | 'delete') {
  if (working.value || loading.value || item.status === 'processing' || (action === 'rename' && !titleDraft.value.trim())) return
  const course = props.courseId, title = titleDraft.value.trim(), token = ++serial
  requesting.value = true; error.value = ''; clearTimeout(pollTimer); await flushDraft()
  try {
    if (disposed || token !== serial || course !== props.courseId) return
    const fresh = await api<ReviewSpace>(`/review-spaces/${item.id}`, { signal: readController.signal })
    if (disposed || token !== serial || course !== props.courseId) return
    if (action === 'rename') {
      const value = await api<ReviewSpace>(`/review-spaces/${item.id}/title`, { method: 'PUT', body: JSON.stringify({ title, revision: fresh.revision }) })
      if (!disposed && token === serial && course === props.courseId) { if (current.value?.id === item.id) apply(value); else spaces.value = [value, ...spaces.value.filter(v => v.id !== item.id)]; editingId.value = '' }
    } else {
      await api(`/review-spaces/${item.id}`, { method: 'DELETE', body: JSON.stringify({ revision: fresh.revision }) })
      if (!disposed && token === serial && course === props.courseId) {
        spaces.value = spaces.value.filter(v => v.id !== item.id); deleteId.value = ''; editingId.value = ''
        if (current.value?.id === item.id) { current.value = null; input.value = ''; newChapterIds.value = []; scopeOpen.value = false; try { localStorage.removeItem(`review-space:${props.courseId}`) } catch { /* optional selection */ } }
      }
    }
  } catch (reason) { if (!disposed && token === serial) error.value = message(reason) }
  finally { if (!disposed) { requesting.value = false; schedulePoll() } }
}
async function perform(action: 'advance' | 'answer' | 'control' | 'reveal', body: Record<string, unknown>) {
  if (!current.value || requesting.value || loading.value) return false
  const id = current.value.id; requesting.value = true; error.value = ''; ++serial; clearTimeout(pollTimer); await flushDraft()
  if (disposed || current.value?.id !== id) { requesting.value = false; return false }
  try {
    const value = await api<ReviewSpace>(`/review-spaces/${id}/${action}`, { method: 'POST', body: JSON.stringify({ ...body, revision: current.value.revision }) })
    apply(value); if (action === 'answer') emit('change'); return true
  } catch (reason) {
    if (!disposed) { error.value = message(reason); if (reason instanceof ApiError && reason.status === 409) { try { apply(await api<ReviewSpace>(`/review-spaces/${id}`)) } catch { /* retain actionable error */ } } }
    return false
  } finally { if (!disposed) { requesting.value = false; schedulePoll() } }
}
async function send(override?: string) {
  if (!canSend.value) return
  const content = (override ?? input.value).trim()
  if (!content) return
  if (current.value) { if (await perform('advance', { feedback: content }) && override === undefined) input.value = ''; return }
  requesting.value = true; error.value = ''; ++serial
  try {
    const value = await api<ReviewSpace>(`/courses/${props.courseId}/review-spaces`, { method: 'POST', body: JSON.stringify({ goal: content, chapter_ids: activeChapterIds.value }) })
    apply(value, true); input.value = ''
    try { const values = await api<ReviewSpaceSummary[]>(`/courses/${value.course_id}/review-spaces`); if (!disposed && value.course_id === props.courseId) spaces.value = values } catch { /* current conversation remains readable */ }
  } catch (reason) { if (!disposed) error.value = message(reason) }
  finally { if (!disposed) { requesting.value = false; schedulePoll() } }
}
function activeQuestion(item: ReviewSpaceMessage) { return item.kind === 'question' && item.step?.id === current.value?.step?.id && current.value?.status === 'waiting_answer' }
function answer() { if ((selected.value.length || ownAnswer.value.trim()) && current.value?.status === 'waiting_answer' && current.value.step) void perform('answer', { step_id: current.value.step.id, selected: [...selected.value], own_answer: ownAnswer.value, used_hint: usedHint.value, seen_answer: seenAnswer.value }) }
function reveal() { if (current.value?.step) void perform('reveal', { step_id: current.value.step.id }) }
function enter(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.isComposing || event.shiftKey || event.ctrlKey || event.altKey || event.metaKey) return
  event.preventDefault(); void send()
}
watch([selected, ownAnswer, usedHint, seenAnswer], draft.changed, { deep: true, flush: 'sync' })
function flushDraft() { return draft.flush() }
watch(() => current.value?.messages?.at(-1)?.id, async () => { await nextTick(); if (thread.value) thread.value.scrollTop = thread.value.scrollHeight })
watch(courseWorking, value => emit('busy', value), { immediate: true })
watch(() => props.courseId, () => { void flushDraft(); ++serial; readController.abort(); readController = new AbortController(); clearTimeout(pollTimer); current.value = null; spaces.value = []; input.value = ''; editingId.value = ''; deleteId.value = ''; error.value = ''; pollError.value = ''; newChapterIds.value = []; scopeOpen.value = false; void load() })
onMounted(() => { void load() })
onActivated(() => { active = true; if (current.value || backgroundWorking.value) void refreshCurrent() })
onDeactivated(() => { active = false; void flushDraft(); schedulePoll() })
onUnmounted(() => { draft.dispose(); disposed = true; ++serial; readController.abort(); clearTimeout(pollTimer); emit('busy', false) })
</script>

<template>
  <div class="review-space-layout">
    <aside class="space-sidebar" aria-label="复习对话记录">
      <div class="space-sidebar-title"><UiIcon name="space" /><h1>复习空间</h1></div>
      <button class="button subtle space-new" :disabled="requesting || loading" @click="newConversation"><span aria-hidden="true">＋</span>新建复习对话</button>
      <div class="space-history">
        <p class="space-history-label">复习记录</p>
        <p v-if="!spaces.length" class="muted">对话会自动保存在这里。</p>
        <div v-for="item in spaces" :key="item.id" :class="['space-history-record', { chosen: current?.id === item.id }]">
          <form v-if="editingId === item.id" class="space-title-form" @submit.prevent="manageRecord(item, 'rename')">
            <label :for="`space-title:${item.id}`" class="space-sr-only">复习记录名称</label>
            <input :id="`space-title:${item.id}`" v-model="titleDraft" maxlength="80" :disabled="working || item.status === 'processing'" />
            <div><button class="text-button" :disabled="working || loading || item.status === 'processing' || !titleDraft.trim()">保存</button><button type="button" class="text-button" :disabled="requesting" @click="editingId = ''">取消</button></div>
          </form>
          <template v-else>
            <button class="space-history-item" :disabled="requesting || loading" @click="selectSpace(item.id)"><strong>{{ item.title || item.goal }}</strong><small>{{ date(item.created_at) }} · {{ statusLabels[item.status] }}</small></button>
            <div v-if="deleteId !== item.id" class="space-record-actions">
              <button class="text-button" :disabled="working || loading || item.status === 'processing'" @click="editTitle(item)">重命名</button>
              <button class="text-button danger" :disabled="working || loading || item.status === 'processing'" @click="deleteId = item.id; editingId = ''">删除</button>
            </div>
            <div v-else class="space-delete-confirm">
              <p>删除这条复习记录？</p>
              <button class="text-button danger" :disabled="working || loading || item.status === 'processing'" @click="manageRecord(item, 'delete')">确认删除</button>
              <button class="text-button" :disabled="requesting" @click="deleteId = ''">取消</button>
            </div>
          </template>
        </div>
      </div>
      <button v-if="current && current.status !== 'completed'" class="text-button space-end" :disabled="requesting || loading" @click="perform('control', { action: 'finish' })">结束对话</button>
    </aside>
    <section :class="['space-chat', { 'is-empty': !current }]" aria-label="复习聊天">
      <header class="space-chat-heading"><div><h2>{{ current?.title || '新复习对话' }}</h2><p>{{ courseName }}</p></div><span v-if="current" class="space-save-note">自动保存</span></header>
      <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="pollError" class="notice warning"><span>{{ pollError }}</span><button class="text-button" :disabled="requesting" @click="refreshCurrent">重新连接</button></div>
      <div ref="thread" :class="['space-thread', { 'is-empty': !current }]" aria-live="polite" aria-relevant="additions">
        <p v-if="loading" class="muted" role="status">正在读取对话…</p>
        <div v-else-if="!current" class="space-welcome"><div class="space-welcome-mark"><UiIcon name="outline" /></div><h3>今天想复习什么？</h3><p>可以讲解知识点、梳理复习内容、解答疑问，也可以按你的要求出题练习。<br />直接在下方输入想复习的内容，助手会查阅当前课程资料，并根据你的反馈继续讲解。</p></div>
        <template v-else>
          <article v-for="item in current.messages" :key="item.id" :class="['space-message', item.role]">
            <div class="space-message-role">{{ item.role === 'user' ? '你' : '复习助手' }}</div>
            <div class="space-message-body">
              <template v-if="item.kind === 'question' && item.step?.question">
                <p class="space-question-topic">{{ item.step.topic }} · {{ item.step.question.question_type === 'single_choice' ? '单选题' : item.step.question.question_type === 'multiple_choice' ? '多选题' : '简答 / 计算题' }}<span v-if="(item.step.batch_count || 1) > 1"> · 第 {{ item.step.batch_index }} / {{ item.step.batch_count }} 题</span></p><p v-if="item.step.external_knowledge" class="space-note">含模型自行搜索的知识，来源保存在参考解析中。</p><p v-if="item.step.model_knowledge" class="space-note">含模型已有知识的补充。</p>
                <MarkdownContent :content="item.step.question.stem" hide-citations />
                <fieldset v-if="item.step.question.options.length" class="space-options" :disabled="!activeQuestion(item) || working"><legend class="space-sr-only">选择答案</legend><label v-for="option in item.step.question.options" :key="option.key" :class="{ selected: activeQuestion(item) && selected.includes(option.key) }"><template v-if="activeQuestion(item)"><input v-if="item.step.question.question_type === 'multiple_choice'" v-model="selected" type="checkbox" :value="option.key" /><input v-else type="radio" :name="`space-answer:${item.step.id}`" :checked="selected.includes(option.key)" @change="selected = [option.key]" /></template><strong>{{ option.key }}</strong><MarkdownContent :content="option.text" hide-citations /></label></fieldset>
                <template v-if="activeQuestion(item)"><div v-if="item.step.question.question_type === 'other'" class="space-written-answer"><label :for="`space-written:${item.step.id}`">你的作答</label><textarea :id="`space-written:${item.step.id}`" v-model="ownAnswer" rows="4" maxlength="4000" :disabled="working" placeholder="写下答案或解题过程，提交后对照参考解析。"></textarea><p class="space-note">简答和计算题不自动判分。</p></div><div class="space-answer-conditions"><label><input v-model="usedHint" type="checkbox" :disabled="working" />用了提示或查阅资料</label><label><input v-model="seenAnswer" type="checkbox" :disabled="working || current.step?.revealed" />看过这题答案</label></div><div class="space-question-actions"><button class="button primary small" :disabled="working || (item.step.question.question_type === 'other' ? !ownAnswer.trim() : !selected.length)" @click="answer">提交作答</button><button v-if="!current.step?.revealed" class="text-button" :disabled="working" @click="reveal">看参考答案</button><button class="text-button" :disabled="!canSend" @click="send('这批题先不做，继续聊刚才的知识点。')">先不做，继续聊</button><span class="space-note">{{ draftStatus }}</span></div><p v-if="draftError" class="notice warning">{{ draftError }}</p></template>
                <details v-if="item.step.revealed_answer" class="space-sources"><summary>参考答案</summary><MarkdownContent :content="item.step.revealed_answer.answer" hide-citations /></details>
              </template>
              <template v-else><p v-if="item.result" class="space-result-note">{{ item.result.correct === null ? '作答已保存，请自行对照参考解析' : item.result.correct ? '作答与参考答案一致' : '作答与参考答案有差异' }}<span v-if="item.result.grading_basis"> · {{ item.result.grading_basis === 'model' ? '依据模型参考答案' : '依据原文答案' }}</span></p><p v-if="item.role === 'user'" class="space-user-text">{{ item.content }}</p><MarkdownContent v-else :content="item.content" hide-citations /></template>
              <details v-if="item.step?.sources?.length" class="space-sources"><summary>查看讲解依据</summary><div v-for="source in item.step.sources" :key="source.chunk_id"><strong><a v-if="source.kind === 'web'" :href="source.url" target="_blank" rel="noopener noreferrer">{{ source.title }}</a><template v-else>{{ source.filename }}</template> · {{ source.locator.label }}</strong><p>{{ source.text }}</p></div></details>
              <TokenUsage v-if="item.role === 'assistant'" :usage="item.token_usage ?? item.step?.token_usage" />
              <p v-if="item.step?.search_scope" class="space-note space-answer-scope">本条搜索范围：{{ item.step.search_scope.chapter_ids.length ? item.step.search_scope.chapters.join('、') : '全部章节' }}</p>
            </div>
          </article>
          <div v-if="current.status === 'processing'" class="space-typing" role="status"><span class="space-working-dot"></span>{{ current.stage || '正在回复…' }}</div>
          <div v-if="current.status === 'failed'" class="space-retry"><p>{{ current.error }}</p><TokenUsage :usage="current.failed_token_usage" /><button class="text-button" :disabled="!canSend" @click="perform('advance', { feedback: '' })">重新回复</button></div>
          <div v-if="current.status === 'completed'" class="space-ended"><span>这次对话已保存。</span><button class="text-button" :disabled="working" @click="newConversation">新建复习对话</button></div>
        </template>
      </div>
      <details v-if="current?.trace.length" class="space-tool-log"><summary>查看助手操作</summary><ol><li v-for="(item, index) in current.trace" :key="index"><strong>{{ actionLabels[item.action] || '调整回复' }}</strong><span>{{ item.result }}</span><p>{{ item.reason }}</p></li></ol></details>
      <form class="space-composer" @submit.prevent="send()">
        <div class="space-scope-bar"><button type="button" class="text-button" :disabled="!canEditScope" :aria-expanded="scopeOpen" aria-controls="space-scope-picker" @click="editScope">搜索范围：{{ scopeLabel }} <span aria-hidden="true">{{ scopeOpen ? '▴' : '▾' }}</span></button><span v-if="activeChapterIds.length" class="space-scope-titles" :title="activeChapterTitles.join('、')">{{ activeChapterTitles.join('、') }}</span></div>
        <div v-if="scopeOpen" id="space-scope-picker" class="space-scope-picker">
          <p class="space-note">可多选章节；不选表示搜索全部章节。应用后用于后续回答，历史回答保留原来的范围。</p>
          <fieldset :disabled="!canEditScope"><legend class="space-sr-only">选择搜索章节</legend><label v-for="chapter in scopeOptions" :key="chapter.id"><input v-model="scopeDraft" type="checkbox" :value="chapter.id" /><span>{{ chapter.title }}</span></label><p v-if="!scopeOptions.length" class="space-note">暂无可选择的知识章节，默认搜索全部资料。</p></fieldset>
          <div class="space-scope-actions"><button type="button" class="text-button" :disabled="!canEditScope" @click="scopeDraft = []">全部章节</button><span class="space-note">{{ scopeDraft.length ? `已选 ${scopeDraft.length} 个章节` : '搜索全部章节' }}</span><button type="button" class="button primary small" :disabled="!canEditScope" @click="applyScope()">应用范围</button><button type="button" class="text-button" :disabled="working" @click="scopeOpen = false">取消</button></div>
        </div>
        <div v-if="scopeHasGaps" class="space-scope-gap"><span>当前所选章节的原文依据仍有缺项。</span><button type="button" class="text-button" :disabled="!canSend" @click="expandScope">扩大到全部章节并重新回答</button></div>
        <div class="space-input-shell">
          <label class="space-sr-only" for="space-message">复习消息</label><textarea id="space-message" v-model="input" rows="3" maxlength="2000" :disabled="!canSend" placeholder="说说你想复习的内容，或直接问一个问题…" @keydown="enter"></textarea>
          <div class="space-input-tools"><button class="space-send" :disabled="!canSend || !input.trim()"><span>{{ working ? '正在回复' : '发送' }}</span><svg v-if="!working" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6" /></svg></button></div>
        </div>
        <div class="space-composer-footer"><span v-if="!modelConfigured">先在右上角配置模型。</span><span v-else-if="blocked">请先处理资料库中的资料或索引提示。</span><span v-else-if="backgroundWorking">另一段对话正在后台回复，可以切换查看；回复完成后可继续提问。</span><span v-else>Enter 发送 · Shift + Enter 换行</span></div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.space-scope-bar { display: flex; align-items: center; gap: 12px; min-width: 0; padding: 0 2px 10px; font-size: 12px; }
.space-scope-bar > button { flex-shrink: 0; font-size: 12px; }
.space-scope-titles { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.space-scope-picker { padding: 12px; margin-bottom: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface); }
.space-scope-picker > p { margin: 0 0 10px; line-height: 1.6; }
.space-scope-picker fieldset { border: 0; padding: 0; margin: 0; max-height: 150px; overflow-y: auto; display: grid; gap: 6px; }
.space-scope-picker label { display: flex; align-items: baseline; gap: 8px; padding: 5px; font-size: 13px; cursor: pointer; }
.space-scope-picker input { accent-color: var(--accent); }
.space-scope-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
.space-scope-gap { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; color: var(--muted); font-size: 12px; }
.space-scope-gap > button { font-size: 12px; }
.space-answer-scope { margin-top: 6px; font-size: 11px; }
.review-space-layout { --space-column: 880px; display: grid; grid-template-columns: 216px minmax(0, 1fr); gap: 32px; align-items: start; }
.space-sidebar { min-width: 0; height: calc(100dvh - 100px); min-height: 520px; display: flex; flex-direction: column; padding: 8px 20px 8px 0; border-right: 1px solid var(--line); }
.space-sidebar-title { display: flex; align-items: center; gap: 9px; padding: 8px 10px; margin-bottom: 24px; }
.space-sidebar-title .ui-icon { color: var(--accent); width: 22px; height: 22px; }
.space-sidebar-title h1 { font-size: 20px; line-height: 1.4; letter-spacing: 0; font-weight: 650; }
.space-new { width: 100%; justify-content: flex-start; gap: 9px; flex-shrink: 0; padding-inline: 12px; background: #eaf0fe; border-color: #dce4f8; color: var(--accent-dark); }
.space-new > span { font-size: 20px; line-height: 1; font-weight: 400; }
.space-new:not(:disabled):hover { background: #e0e9fd; border-color: #cbd8f7; }
.space-end { flex-shrink: 0; align-self: flex-start; margin: 14px 12px 4px; font-size: 12px; color: var(--muted); }
.space-history { margin-top: 32px; flex: 1; min-height: 0; overflow-y: auto; padding: 2px; scrollbar-width: thin; }
.space-history-label { color: var(--muted); font-size: 11px; margin: 0 10px 14px; }
.space-history > .muted { font-size: 12px; padding: 12px 10px; line-height: 1.8; }
.space-history-item { width: 100%; display: block; text-align: left; padding: 11px 12px 8px; border: 0; border-radius: 8px; background: transparent; cursor: pointer; }
.space-history-record { border: 1px solid transparent; border-radius: 10px; margin-bottom: 8px; }
.space-history-record:hover, .space-history-record:focus-within { background: #edf1f9; }
.space-history-record.chosen { background: #eaf0fe; border-color: #dce4f8; }
.space-record-actions { display: flex; gap: 14px; padding: 0 12px 8px; opacity: 0; }
.space-history-record:hover .space-record-actions, .space-history-record:focus-within .space-record-actions, .space-history-record.chosen .space-record-actions { opacity: 1; }
.space-record-actions .text-button, .space-delete-confirm .text-button { font-size: 11px; }
.space-record-actions .text-button { color: var(--muted); }
.space-record-actions .text-button:hover { color: var(--accent); }
.space-record-actions .danger:hover { color: var(--danger); }
.space-title-form { padding: 12px; }
.space-title-form input { width: 100%; min-width: 0; padding: 7px 8px; font-size: 13px; }
.space-title-form > div { display: flex; gap: 12px; margin-top: 8px; }
.space-delete-confirm { padding: 0 12px 10px; font-size: 12px; }
.space-delete-confirm p { margin: 0 0 7px; }
.space-delete-confirm button + button { margin-left: 12px; }
.space-written-answer { margin: 16px 0; }.space-written-answer > label { font-size: 12px; color: var(--muted); }.space-written-answer textarea { width: 100%; padding: 10px 12px; margin-top: 7px; resize: vertical; min-height: 100px; }
.space-history-item strong { display: block; font-size: 13px; font-weight: 500; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }.space-history-item small { display: block; color: var(--muted); font-size: 11px; margin-top: 6px; }
.space-chat { min-width: 0; display: flex; flex-direction: column; height: calc(100dvh - 100px); min-height: 520px; overflow: hidden; }
.space-chat-heading { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 12px 10px 18px; border-bottom: 1px solid var(--line); }
.space-chat-heading > div { min-width: 0; }
.space-chat-heading h2 { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.space-chat-heading p { font-size: 11px; color: var(--muted); margin-top: 3px; }
.space-save-note { color: var(--muted); font-size: 11px; white-space: nowrap; }
.space-chat > .notice { margin: 12px 10px 0; flex-shrink: 0; }
.space-thread { flex: 1; min-height: 0; overflow-y: auto; padding: 32px 24px; scrollbar-width: thin; }
.space-thread.is-empty { flex: 0 1 auto; margin-top: auto; padding: 32px 24px 28px; }
.space-welcome { max-width: var(--space-column); margin: 0 auto; padding: 20px 0 0; text-align: center; }
.space-welcome-mark { width: 48px; height: 54px; margin: 0 auto 24px; display: grid; place-items: center; color: var(--accent); background: #e5edff; border-radius: 12px 12px 18px 4px; }
.space-welcome-mark .ui-icon { width: 27px; height: 27px; }
.space-welcome h3 { font-size: clamp(24px, 2.4vw, 34px); line-height: 1.4; margin: 0 0 14px; font-weight: 600; letter-spacing: -.5px; }
.space-welcome p { color: var(--muted); font-size: 14px; line-height: 1.8; }
.space-message { margin: 0 auto 32px; min-width: 0; max-width: var(--space-column); }
.space-message-role { font-size: 11px; color: var(--muted); margin-bottom: 10px; }
.space-message.assistant .space-message-role { color: var(--accent); font-weight: 600; }
.space-message.user { display: flex; flex-direction: column; align-items: flex-end; }
.space-message-body { min-width: 0; width: 100%; overflow-wrap: anywhere; }
.space-message.user .space-message-body { background: #e7edfb; border-radius: 14px 14px 4px 14px; padding: 13px 18px; max-width: 85%; width: auto; }
.space-user-text { margin: 0; white-space: pre-wrap; font-size: 15px; line-height: 1.8; }
.space-message.assistant :deep(.markdown-content) { font-size: 15px; line-height: 1.9; }
.space-question-topic { color: var(--accent); font-size: 13px; margin: 0 0 12px; }
.space-options { border: 0; margin: 16px 0; padding: 0; display: grid; gap: 8px; min-width: 0; }.space-options label { display: flex; align-items: baseline; gap: 12px; border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; }.space-options label.selected { border-color: var(--accent); background: var(--surface-alt); }.space-options input { accent-color: var(--accent); }.space-options strong { font-size: 13px; color: var(--accent); }.space-options :deep(.markdown-content) { flex: 1; min-width: 0; }.space-options :deep(.markdown-content p) { margin: 0; }
.space-answer-conditions, .space-question-actions { display: flex; gap: 14px; align-items: center; flex-wrap: wrap; font-size: 12px; margin: 12px 0; color: var(--muted); }.space-answer-conditions label { display: flex; align-items: center; gap: 5px; }.space-sources { margin-top: 16px; font-size: 12px; }.space-sources summary, .space-tool-log summary { cursor: pointer; color: var(--accent); font-size: 12px; }.space-sources > div { margin-top: 12px; }.space-sources a { color: var(--accent); }.space-sources p { color: var(--muted); white-space: pre-wrap; max-height: 160px; overflow: auto; margin-top: 5px; }
.space-result-note { font-size: 12px; color: var(--accent); margin: 0 0 12px; }.space-note { color: var(--muted); font-size: 12px; }
.space-typing { display: flex; gap: 10px; align-items: center; color: var(--muted); font-size: 13px; max-width: var(--space-column); margin: 0 auto; }
.space-working-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.space-retry, .space-ended { padding: 14px 0; color: var(--muted); font-size: 13px; max-width: var(--space-column); margin: 0 auto; }
.space-tool-log { width: calc(100% - 48px); max-width: var(--space-column); margin: 0 auto; padding: 0 0 12px; max-height: 160px; overflow: auto; flex-shrink: 0; }
.space-tool-log ol { padding-left: 18px; font-size: 12px; color: var(--muted); }.space-tool-log li { margin: 12px 0; }.space-tool-log span { margin-left: 10px; }.space-tool-log p { margin: 4px 0; }
.space-composer { width: 100%; max-width: calc(var(--space-column) + 48px); margin: 0 auto; padding: 10px 24px 2px; flex-shrink: 0; }
.space-chat.is-empty .space-composer { margin-bottom: auto; padding-bottom: 28px; }
.space-input-shell { background: var(--surface); border: 1px solid #d5ddec; border-radius: 16px; padding: 16px 18px 12px; box-shadow: 0 6px 24px #24324b08; }
.space-input-shell:focus-within { border-color: #93a9ec; box-shadow: 0 0 0 3px #3455db0a, 0 6px 24px #24324b08; }
.space-input-shell textarea { display: block; width: 100%; margin: 0 0 10px; border: 0; border-radius: 0; background: transparent; outline: none; resize: vertical; min-height: 76px; max-height: 180px; padding: 2px; line-height: 1.75; font-size: 15px; }
.space-input-shell textarea:focus-visible { outline: none; }
.space-input-tools { display: flex; align-items: center; gap: 12px; min-height: 36px; }
.space-send { margin-left: auto; flex-shrink: 0; display: inline-flex; justify-content: center; align-items: center; gap: 5px; height: 36px; padding: 0 12px; border: 0; border-radius: 9px; background: var(--accent); color: #fff; font-size: 12px; font-weight: 600; }
.space-send svg { width: 18px; height: 18px; }
.space-send:not(:disabled):hover { background: var(--accent-dark); }
.space-send:disabled { background: #e7ecf7; color: #7a88a5; opacity: 1; }
.space-composer-footer { margin-top: 10px; color: var(--muted); font-size: 11px; text-align: center; }
.space-sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
@media (hover: none) { .space-record-actions { opacity: 1; } }
@media (max-width: 1000px) {
  .review-space-layout { grid-template-columns: 180px minmax(0, 1fr); gap: 20px; }
  .space-sidebar { padding-right: 12px; }
  .space-sidebar-title { padding-inline: 4px; }
  .space-sidebar-title h1 { font-size: 18px; }
  .space-thread { padding-inline: 12px; }
  .space-composer { max-width: calc(var(--space-column) + 24px); padding-inline: 12px; }
  .space-tool-log { width: calc(100% - 24px); }
}
@media (max-width: 700px) {
  .review-space-layout { display: flex; flex-direction: column; gap: 18px; }
  .space-sidebar { width: 100%; height: auto; min-height: 0; padding: 0 0 14px; border-right: 0; border-bottom: 1px solid var(--line); }
  .space-sidebar-title { margin-bottom: 12px; }
  .space-new { width: auto; align-self: flex-start; }
  .space-history { margin-top: 12px; display: flex; flex: none; overflow-x: auto; gap: 8px; }
  .space-history-label { display: none; }
  .space-history > .muted { padding-block: 4px; }
  .space-history-record { min-width: 170px; max-width: 210px; flex-shrink: 0; }
  .space-end { margin-top: 8px; }
  .space-chat { width: 100%; height: 80dvh; min-height: 500px; }
  .space-chat-heading { padding: 0 0 14px; }
  .space-thread, .space-thread.is-empty { padding: 24px 4px; }
  .space-welcome { padding-top: 0; }
  .space-welcome-mark { margin-bottom: 18px; }
  .space-welcome h3 { font-size: 25px; }
  .space-welcome p { font-size: 13px; }
  .space-composer { padding: 8px 2px 2px; }
  .space-input-shell { padding: 13px 12px 10px; }
  .space-input-shell textarea { font-size: 16px; min-height: 76px; }
  .space-tool-log { width: calc(100% - 8px); }
}
</style>
