<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { WrongQuestion } from '../types'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{ courseId: string; revision: number }>()
const emit = defineEmits<{ busy: [value: boolean]; change: [] }>()
const questions = ref<WrongQuestion[]>([])
const loading = ref(false)
const saving = ref(false)
const deleting = ref('')
const error = ref('')
const formOpen = ref(false)
const stem = ref('')
const ownAnswer = ref('')
const answer = ref('')
const busy = computed(() => loading.value || saving.value || !!deleting.value)
const automaticCount = computed(() => questions.value.filter(q => q.reason === 'incorrect').length)
const referenceCount = computed(() => questions.value.filter(q => q.reason === 'reference').length)
let generation = 0
let disposed = false
let controller = new AbortController()
watch(busy, value => emit('busy', value), { immediate: true })
watch(() => props.courseId, () => {
  ++generation; controller.abort(); controller = new AbortController()
  questions.value = []; stem.value = ''; ownAnswer.value = ''; answer.value = ''; formOpen.value = false
  void load()
}, { immediate: true })
watch(() => props.revision, () => { if (!saving.value && !deleting.value) void load() })
function active(token: number) { return !disposed && token === generation }
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason) }
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
async function load() {
  if (!props.courseId) return
  const token = ++generation
  loading.value = true; error.value = ''
  try {
    const value = await api<WrongQuestion[]>(`/courses/${props.courseId}/wrong-questions`, { signal: controller.signal })
    if (active(token)) questions.value = value
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
      emit('change')
    }
  } catch (reason) { if (active(token)) error.value = message(reason) }
  finally { if (active(token)) deleting.value = '' }
}
onUnmounted(() => { disposed = true; controller.abort(); emit('busy', false) })
</script>

<template>
  <section class="panel wrong-panel">
    <div class="section-top">
      <div><p class="eyebrow">REVIEW / 错题本</p><h2>把需要再练的题留下来</h2><p class="muted">与原文答案或模型答案不一致的选择题自动收录，并标明来源；也可以手动标记或直接录入。</p></div>
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
      <div class="wrong-summary"><span>共 {{ questions.length }} 道 · 按原文答案自动收录 {{ automaticCount }} 道 · 与模型答案不一致 {{ referenceCount }} 道 · 手动加入 {{ questions.length - automaticCount - referenceCount }} 道</span><span>按最近收录排序</span></div>
      <div class="wrong-list">
        <article v-for="(item, index) in questions" :key="item.id" class="wrong-card">
          <div class="wrong-card-top"><div class="wrong-meta"><strong>#{{ index + 1 }}</strong><span class="badge neutral">{{ item.reason === 'incorrect' ? '按原文答案自动收录' : item.reason === 'reference' ? '与模型答案不一致' : '手动加入' }}</span><span>{{ item.question.question_type === 'single_choice' ? '单选题' : item.question.question_type === 'multiple_choice' ? '多选题' : '其他题型' }}</span><span>{{ date(item.created_at) }}</span></div><button class="text-button danger" :disabled="busy" :aria-label="`删除第 ${index + 1} 道错题`" @click="remove(item)">{{ deleting === item.id ? '删除中…' : '删除错题' }}</button></div>
          <p class="wrong-source">{{ item.question.filename }}<span v-if="item.question.section"> · {{ item.question.section }}</span><span v-if="item.question.number"> · 原题 {{ item.question.number }}</span><span v-if="item.question.locator.label"> · {{ item.question.locator.label }}</span></p>
          <MarkdownContent :content="item.question.stem" />
          <div v-if="item.question.options.length" class="wrong-options"><div v-for="option in item.question.options" :key="option.key" :class="['wrong-option', { chosen: item.selected.includes(option.key) }]"><strong>{{ option.key }}</strong><MarkdownContent :content="option.text" /></div></div>
          <details v-if="item.question.translation" class="wrong-details"><summary>查看中文翻译</summary><MarkdownContent :content="item.question.translation.stem" /><div v-for="option in item.question.translation.options" :key="option.key" class="wrong-option"><strong>{{ option.key }}</strong><MarkdownContent :content="option.text" /></div></details>
          <div v-if="item.selected.length || item.own_answer" class="wrong-own-answer"><p v-if="item.selected.length">当时选择：{{ item.selected.join('、') }}</p><template v-if="item.own_answer"><p>你的作答</p><MarkdownContent :content="item.own_answer" /></template></div>
          <details v-if="item.resolution.answer" class="wrong-details"><summary>查看参考答案与解析</summary><p class="wrong-source">{{ item.resolution.origin === 'uploaded' ? '上传答案' : item.resolution.origin === 'embedded' ? '原题自带答案' : item.resolution.origin === 'model' ? '模型参考答案' : '手动填写' }}<span v-if="item.resolution.source"> · {{ item.resolution.source }}</span></p><p v-if="item.resolution.choices?.length" class="wrong-correct">{{ item.resolution.origin === 'model' ? '模型参考选项' : '原文答案选项' }}：{{ item.resolution.choices.join('、') }}</p><MarkdownContent :content="item.resolution.answer" hide-citations /></details>
          <p v-else class="muted wrong-no-answer">{{ item.source_document_id ? '暂未保存参考答案。可在在线练习中显示答案后自动补充。' : '暂未保存参考答案。' }}</p>
        </article>
      </div>
    </template>
  </section>
</template>

<style scoped>
.wrong-form{display:grid;gap:10px;background:#fafcf6;border:1px solid #e2ebd9;border-radius:10px;padding:20px;margin-bottom:24px}.wrong-form label{font-size:12px;color:#7c926c}.wrong-form label:not(:first-child){margin-top:8px}.wrong-form textarea{width:100%;resize:vertical;line-height:1.8}.wrong-form-actions{display:flex;gap:16px;align-items:center;margin-top:8px}.wrong-summary{display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;margin:20px 0;font-size:12px;color:#829674}.wrong-list{display:grid;gap:18px}.wrong-card{border:1px solid #e2ebd9;border-radius:10px;padding:22px;min-width:0;background:#fcfdf9}.wrong-card-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:12px}.wrong-meta{display:flex;align-items:center;flex-wrap:wrap;gap:10px;font-size:11px;color:#97a48c}.wrong-card-top>.text-button{flex-shrink:0}.wrong-source{font-size:11px;color:#8f9e83;margin:8px 0 16px;overflow-wrap:anywhere}.wrong-options{display:grid;gap:8px;margin:18px 0}.wrong-option{display:flex;align-items:flex-start;gap:12px;padding:10px 14px;border:1px solid #e5ecdD;border-radius:8px;min-width:0}.wrong-option.chosen{background:#f9ece6;border-color:#dec0b4}.wrong-option>strong{font-size:13px;color:#74915d;padding-top:2px}.wrong-option>.markdown-content{flex:1;min-width:0}.wrong-own-answer{border-left:3px solid #dec0b4;background:#fcf5ef;padding:12px 16px;margin:16px 0}.wrong-own-answer>p{font-size:12px;color:#a16953;margin:0 0 8px}.wrong-details{border-top:1px solid #e2ebd9;padding-top:14px;margin-top:18px}.wrong-details summary{cursor:pointer;font-size:12px;color:#74915d;margin-bottom:12px}.wrong-details[open]{padding-bottom:8px}.wrong-correct{font-size:12px;color:#74915d;margin-bottom:12px}.wrong-no-answer{font-size:11px;margin-top:18px}
@media(max-width:600px){.wrong-card{padding:16px}.wrong-form{padding:14px}.wrong-meta{font-size:10px;gap:7px}.wrong-source{font-size:10px}.wrong-summary{font-size:10px}.wrong-card-top>.text-button{font-size:10px}}
</style>
