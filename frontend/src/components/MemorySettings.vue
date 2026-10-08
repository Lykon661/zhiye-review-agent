<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { AgentMemory, LearningMemory, MemoryPreference } from '../types'

const props = defineProps<{ courseId: string; courseName: string; sessionId: string; active: boolean }>()
const emit = defineEmits<{ close: []; busy: [value: boolean] }>()
const data = ref<AgentMemory | null>(null)
const tab = ref<'preferences' | 'learning' | 'session'>('preferences')
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const feedback = ref('')
const draft = ref('')
const scope = ref<'global' | 'course'>('global')
const editing = ref('')
const editDraft = ref('')
const summaryDraft = ref('')
const panel = ref<HTMLElement | null>(null)
const busy = computed(() => loading.value || saving.value)
const disabled = computed(() => busy.value || props.active)
let generation = 0
let readSerial = 0
let disposed = false
let controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
const base = () => '/courses/' + props.courseId + '/memory'
const valid = (token: number) => !disposed && token === generation
const date = (value: string) => new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
function status(item: LearningMemory) {
  return item.status === 'needs_review' ? '需要巩固' : item.status === 'recent_correct' ? '近期答对' : '关注过'
}
function basis(value: string | null) {
  return value === 'model' ? '模型参考答案' : value === 'embedded' ? '原题自带答案' : value === 'uploaded' ? '上传答案' : ''
}
watch(busy, value => emit('busy', value), { immediate: true })
watch(() => [props.courseId, props.sessionId], () => {
  ++generation; ++readSerial; controller.abort(); controller = new AbortController()
  if (timer) clearTimeout(timer)
  data.value = null; editing.value = ''; draft.value = ''; summaryDraft.value = ''; feedback.value = ''
  void load()
}, { immediate: true })

async function load(silent = false) {
  if (!props.courseId || saving.value) return
  const token = generation, serial = ++readSerial
  if (!silent) { loading.value = true; error.value = '' }
  try {
    const result = await api<AgentMemory>(base() + (props.sessionId ? '?session_id=' + encodeURIComponent(props.sessionId) : ''), { signal: controller.signal })
    if (valid(token) && serial === readSerial) {
      data.value = result
      if (!editing.value) summaryDraft.value = result.session?.summary || ''
      if (timer) clearTimeout(timer)
      if (result.updating) timer = setTimeout(() => void load(true), 3000)
    }
  } catch (reason) {
    if (valid(token) && serial === readSerial) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally { if (valid(token) && serial === readSerial && !silent) loading.value = false }
}
async function mutate(path: string, method: string, body?: unknown) {
  if (disabled.value) return
  const token = generation
  ++readSerial
  if (timer) clearTimeout(timer)
  saving.value = true; error.value = ''; feedback.value = ''
  try {
    await api(base() + path, { method, body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal })
    if (valid(token)) { editing.value = ''; draft.value = ''; feedback.value = method === 'DELETE' ? '已忘记这条记忆。' : '记忆已保存。' }
  } catch (reason) {
    if (valid(token)) error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    if (valid(token)) { saving.value = false; await load(true) }
  }
}
function editPreference(item: MemoryPreference) {
  editing.value = item.id; editDraft.value = item.content
}
function editLearning(item: LearningMemory) {
  editing.value = item.id; editDraft.value = item.topic
}
function saveSummary(clear = false) {
  if (!props.sessionId) return
  void mutate('/sessions/' + props.sessionId, 'PUT', { summary: clear ? '' : summaryDraft.value.trim(), revision: data.value?.session?.revision || 0 })
}
function close() { if (!busy.value) emit('close') }
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
  if (event.key !== 'Tab') return
  const targets = panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary')
  const visible = Array.from(targets || []).filter(item => item.getClientRects().length > 0)
  if (!visible.length) { event.preventDefault(); return }
  const first = visible[0], last = visible[visible.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(async () => {
  previousFocus = document.activeElement as HTMLElement | null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', key)
  await nextTick()
  panel.value?.focus()
})
onUnmounted(() => {
  disposed = true; controller.abort(); if (timer) clearTimeout(timer)
  document.removeEventListener('keydown', key)
  document.body.style.overflow = previousOverflow
  if (previousFocus?.isConnected) previousFocus.focus()
  emit('busy', false)
})
</script>

<template>
  <div class="management-overlay" @click.self="close">
    <section ref="panel" class="management-dialog memory-dialog" role="dialog" aria-modal="true" aria-labelledby="memory-title" tabindex="-1">
      <header class="memory-header"><div><h2 id="memory-title">学习记忆</h2><p class="muted">{{ courseName }} · 保存到本机</p></div><button type="button" class="icon-button" aria-label="关闭记忆" :disabled="busy" @click="close">×</button></header>
      <div class="memory-tabs" aria-label="记忆分类">
        <button type="button" :class="{ selected: tab === 'preferences' }" :aria-pressed="tab === 'preferences'" @click="tab = 'preferences'">学习偏好</button>
        <button type="button" :class="{ selected: tab === 'learning' }" :aria-pressed="tab === 'learning'" @click="tab = 'learning'">课程学习</button>
        <button type="button" :class="{ selected: tab === 'session' }" :aria-pressed="tab === 'session'" @click="tab = 'session'">当前会话</button>
      </div>
      <p v-if="active" class="muted">正在回答，结束后可以编辑记忆。</p>
      <p v-if="loading" role="status" class="muted">正在读取记忆…</p>
      <p v-if="error" role="alert" class="document-error">{{ error }}</p>
      <p v-if="feedback" role="status" class="memory-feedback">{{ feedback }}</p>
      <template v-if="tab === 'preferences'">
        <p class="muted memory-help">“以后讲算法先举例”等明确的长期要求会自动保存。全局偏好跨课程使用，课程偏好只用于当前课程。</p>
        <form class="memory-add" @submit.prevent="mutate('/preferences', 'POST', { content: draft.trim(), scope })">
          <label for="new-memory">添加学习偏好</label>
          <textarea id="new-memory" v-model="draft" maxlength="400" rows="2" :disabled="disabled" placeholder="例如：讲解算法时，先带我走一遍具体输入。" required></textarea>
          <div class="memory-actions"><select v-model="scope" aria-label="偏好使用范围" :disabled="disabled"><option value="global">所有课程</option><option value="course">仅当前课程</option></select><button class="button primary small" :disabled="disabled || !draft.trim()">保存偏好</button></div>
        </form>
        <p v-if="data && !data.preferences.length" class="muted memory-empty">还没有保存学习偏好。</p>
        <article v-for="item in data?.preferences || []" :key="item.id" class="memory-card">
          <div class="memory-meta"><span class="badge neutral">{{ item.scope === 'global' ? '所有课程' : '当前课程' }}</span><span>{{ date(item.updated_at) }}</span></div>
          <form v-if="editing === item.id" @submit.prevent="mutate('/preferences/' + item.id, 'PATCH', { content: editDraft.trim(), scope: item.scope })"><textarea v-model="editDraft" rows="3" maxlength="400" aria-label="修改偏好内容" :disabled="disabled" required></textarea><div class="memory-actions"><button class="button primary small" :disabled="disabled || !editDraft.trim()">保存</button><button type="button" class="text-button" :disabled="busy" @click="editing = ''">取消</button></div></form>
          <template v-else><p class="memory-text">{{ item.content }}</p><p v-if="item.source_quote" class="memory-quote">你的原话：{{ item.source_quote }}</p><div class="memory-actions"><button type="button" class="text-button" :disabled="disabled" @click="editPreference(item)">修改</button><button type="button" class="text-button danger" :disabled="disabled" @click="mutate('/preferences/' + item.id, 'DELETE')">忘记</button></div></template>
        </article>
      </template>
      <template v-else-if="tab === 'learning'">
        <p class="muted memory-help">作答和讨论按知识点整理。与模型参考答案不同也记为错误；近期答对后更新状态。提问本身不会记为错误。</p>
        <p v-if="data && !data.learning.length" class="muted memory-empty">还没有这门课的学习记忆。开始练习或讨论后会自动整理。</p>
        <article v-for="item in data?.learning || []" :key="item.id" class="memory-card">
          <div class="memory-meta"><span :class="['memory-status', item.status]">{{ status(item) }}</span><span>{{ date(item.updated_at) }}</span></div>
          <form v-if="editing === item.id" @submit.prevent="mutate('/learning/' + item.id, 'PATCH', { topic: editDraft.trim() })"><input v-model="editDraft" maxlength="80" aria-label="知识点名称" :disabled="disabled" required /><div class="memory-actions"><button class="button primary small" :disabled="disabled || !editDraft.trim()">保存名称</button><button type="button" class="text-button" :disabled="busy" @click="editing = ''">取消</button></div></form>
          <template v-else><h3>{{ item.topic }}</h3><p class="muted memory-help">答错 {{ item.incorrect_count }} 次 · 答对 {{ item.correct_count }} 次<span v-if="item.question_count"> · 涉及 {{ item.question_count }} 道题</span></p><p v-if="basis(item.grading_basis)" class="memory-quote">最近判题依据：{{ basis(item.grading_basis) }}</p><div class="memory-actions"><button type="button" class="text-button" :disabled="disabled" @click="editLearning(item)">修改知识点</button><button type="button" class="text-button danger" :disabled="disabled" @click="mutate('/learning/' + item.id, 'DELETE')">忘记</button></div></template>
          <details><summary>最近记录</summary><div v-for="event in item.recent_events" :key="event.id" class="memory-event"><p>{{ date(event.created_at) }} · {{ event.correct === false ? '答错' : event.correct === true ? '答对' : event.kind === 'discussion' ? '讨论' : '自行对照' }}<span v-if="basis(event.grading_basis)"> · {{ basis(event.grading_basis) }}</span></p><p>{{ event.filename }}<span v-if="event.number"> · 原题 {{ event.number }}</span></p><p class="memory-excerpt">{{ event.question || event.stem }}</p></div></details>
        </article>
      </template>
      <template v-else>
        <p class="muted memory-help">较早的聊天会逐步整理成摘要，回答时结合近期消息继续话题。原始聊天记录仍然保留。</p>
        <p v-if="!sessionId" class="muted memory-empty">选择一个复习会话后查看摘要。</p>
        <form v-else-if="editing === 'summary'" @submit.prevent="saveSummary()"><textarea v-model="summaryDraft" rows="8" maxlength="1800" aria-label="会话摘要" :disabled="disabled"></textarea><div class="memory-actions"><button class="button primary small" :disabled="disabled">保存摘要</button><button type="button" class="text-button" :disabled="busy" @click="editing = ''">取消</button></div></form>
        <template v-else-if="data?.session?.summary"><p class="memory-text memory-summary">{{ data.session.summary }}</p><div class="memory-actions"><button type="button" class="text-button" :disabled="disabled" @click="summaryDraft = data?.session?.summary || ''; editing = 'summary'">修改摘要</button><button type="button" class="text-button danger" :disabled="disabled" @click="saveSummary(true)">忘记摘要</button></div></template>
        <p v-else class="muted memory-empty">这段会话还没有摘要，聊天较长时会在后台自动整理。</p>
      </template>
      <footer class="memory-footer"><span class="muted">{{ data?.updating ? '正在后台整理记忆…' : '记忆用于调整讲解，知识回答仍依据课程资料。' }}</span><button type="button" class="text-button" :disabled="busy" @click="load()">刷新</button></footer>
    </section>
  </div>
</template>

<style scoped>
.memory-dialog { width: min(720px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); overflow-y: auto; }
.memory-header, .memory-meta, .memory-actions, .memory-footer { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.memory-header, .memory-footer { justify-content: space-between; }
.memory-header h2 { margin: 0; }
.memory-header p { margin: 8px 0 0; font-size: 13px; }
.memory-tabs { display: flex; gap: 8px; border-bottom: 1px solid var(--line); margin: 24px 0 16px; padding-bottom: 10px; }
.memory-tabs button { background: transparent; border: 0; color: var(--muted); padding: 8px 12px; border-radius: 6px; cursor: pointer; }
.memory-tabs .selected { background: var(--soft); color: var(--accent); }
.memory-help, .memory-quote, .memory-event { font-size: 13px; line-height: 1.9; }
.memory-add { display: grid; gap: 10px; padding: 16px; margin: 20px 0; background: var(--surface-alt); border: 1px solid var(--line); border-radius: 8px; }
.memory-add label { font-size: 13px; }
.memory-card { padding: 20px 0; border-bottom: 1px solid var(--line); }
.memory-meta { font-size: 12px; color: var(--muted); margin-bottom: 12px; }
.memory-text { line-height: 1.9; overflow-wrap: anywhere; }
.memory-quote { color: var(--muted); overflow-wrap: anywhere; }
.memory-summary { white-space: pre-wrap; }
.memory-card h3 { font-size: 16px; margin: 8px 0; }
.memory-card details { margin-top: 14px; font-size: 13px; }
.memory-card summary { color: var(--accent); cursor: pointer; }
.memory-event { border-top: 1px solid var(--line); color: var(--muted); padding: 10px 0; }
.memory-event p { margin: 4px 0; }
.memory-excerpt { max-height: 110px; overflow: auto; white-space: pre-wrap; }
.memory-actions { margin-top: 10px; }
.memory-dialog textarea, .memory-dialog input { width: 100%; line-height: 1.8; }
.memory-dialog textarea { resize: vertical; }
.memory-status { padding: 3px 8px; border-radius: 5px; background: var(--soft); color: var(--accent); }
.memory-status.needs_review { background: var(--danger-soft); color: var(--danger); }
.memory-status.recent_correct { background: var(--success-soft); color: var(--success); }
.memory-empty { padding: 22px 0; line-height: 1.9; }
.memory-feedback { font-size: 13px; color: var(--success); }
.memory-footer { margin-top: 24px; font-size: 12px; border-top: 1px solid var(--line); padding-top: 16px; }
</style>
