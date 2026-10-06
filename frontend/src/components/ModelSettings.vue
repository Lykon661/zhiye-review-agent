<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import type { CatalogModel, CatalogProvider, ModelConfiguration } from '../types'

const props = defineProps<{ active: boolean }>()
const emit = defineEmits<{ close: []; saved: [configuration: ModelConfiguration] }>()
const panel = ref<HTMLFormElement | null>(null)
const loading = ref(true)
const catalogLoading = ref(false)
const busy = ref<'save' | 'check' | ''>('')
const loadError = ref('')
const catalogError = ref('')
const error = ref('')
const feedback = ref('')
const catalog = ref<CatalogProvider[]>([])
const stored = ref<ModelConfiguration | null>(null)
const apiKey = ref('')
const removeKey = ref(false)
const showKey = ref(false)
const form = reactive({ provider: 'custom', api: 'openai-completions' as ModelConfiguration['api'],
  base_url: '', model: '', allow_without_key: false, timeout_seconds: 90,
  context_window: 32768, max_tokens: 8192, reasoning: false, supports_images: false })
const availableModels = computed(() => catalog.value.find(provider => provider.id === form.provider)?.models || [])
const selectedCatalogModel = computed(() => availableModels.value.find(model => model.id === form.model))
const retainKey = computed(() => !!stored.value?.has_api_key && stored.value.provider === form.provider
  && stored.value.api === form.api && stored.value.base_url.replace(/\/+$/, '') === form.base_url.trim().replace(/\/+$/, ''))
const disabled = computed(() => loading.value || !!busy.value)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''

function close() { if (!busy.value) emit('close') }
function configurationError(cause: unknown) {
  if (cause instanceof ApiError && (cause.status === 404 || cause.status === 405)) {
    return '当前连接的后端还没有模型配置接口。请运行项目的 run.cmd 重新启动后端，再点击“重新读取配置”；若仍提示此错误，请检查前端连接的 API 地址。'
  }
  if (cause instanceof Error && (cause.name === 'TimeoutError' || cause.name === 'AbortError')) {
    return '读取模型配置超时，请确认后端已启动后重试。'
  }
  return cause instanceof Error ? cause.message : String(cause)
}
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
  if (event.key !== 'Tab') return
  const targets = panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), summary')
  const visible = Array.from(targets || []).filter(target => target.getClientRects().length)
  if (!visible.length) { event.preventDefault(); return }
  const first = visible[0]!, last = visible[visible.length - 1]!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
function applyModel(model: CatalogModel) {
  form.model = model.id; form.api = model.api; form.base_url = model.base_url
  form.context_window = model.context_window
  form.max_tokens = Math.min(model.max_tokens, 8192)
  form.reasoning = model.reasoning
  form.supports_images = model.supports_images === true
}
function providerChanged() {
  apiKey.value = ''; removeKey.value = false; form.allow_without_key = false
  const first = availableModels.value[0]
  if (first) applyModel(first)
  else { form.model = ''; form.base_url = ''; form.api = 'openai-completions'; form.reasoning = false; form.supports_images = false }
}
function modelChanged() {
  const known = availableModels.value.find(model => model.id === form.model)
  if (known) applyModel(known)
  else form.supports_images = false
}
function payload() {
  return { ...form, base_url: form.base_url.trim(), model: form.model.trim(),
    api_key: removeKey.value ? '' : apiKey.value.trim() || (retainKey.value ? null : '') }
}
async function submit(action: 'save' | 'check') {
  if (disabled.value || (action === 'save' && props.active) || !panel.value?.reportValidity()) return
  error.value = ''; feedback.value = ''; busy.value = action
  try {
    if (action === 'save') {
      const result = await api<ModelConfiguration>('/system/model-config', { method: 'PUT', body: JSON.stringify(payload()) })
      const { has_api_key, configured, source, ...values } = result
      stored.value = result; Object.assign(form, values); apiKey.value = ''; removeKey.value = false; loadError.value = ''
      feedback.value = '配置已保存并立即生效，聊天和复习笔记将使用这个模型。'
      emit('saved', result)
    } else {
      const result = await api<{ message: string }>('/system/model-config/check', { method: 'POST', body: JSON.stringify(payload()) })
      feedback.value = `${result.message} 当前填写的信息尚未保存时，请点击“保存并接入”。`
    }
  } catch (cause) { error.value = configurationError(cause) }
  finally { busy.value = '' }
}
watch([form, apiKey, removeKey], () => { feedback.value = ''; error.value = '' }, { deep: true, flush: 'sync' })
async function loadConfiguration(replaceForm = false) {
  if (busy.value || catalogLoading.value) return
  loading.value = true; catalogLoading.value = true; loadError.value = ''; catalogError.value = ''
  await Promise.allSettled([
    api<ModelConfiguration>('/system/model-config', { signal: AbortSignal.timeout(10000) })
      .then(configuration => {
        stored.value = configuration
        if (replaceForm) {
          const { has_api_key, configured, source, ...values } = configuration
          Object.assign(form, values)
        }
      })
      .catch(cause => { loadError.value = configurationError(cause) })
      .finally(() => { loading.value = false }),
    api<CatalogProvider[]>('/system/model-catalog', { signal: AbortSignal.timeout(45000) })
      .then(providers => { catalog.value = providers })
      .catch(cause => { catalogError.value = configurationError(cause) })
      .finally(() => { catalogLoading.value = false }),
  ])
  if (replaceForm && selectedCatalogModel.value) form.supports_images = selectedCatalogModel.value.supports_images === true
}
onMounted(async () => {
  previousFocus = document.activeElement as HTMLElement | null
  previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'
  document.addEventListener('keydown', key)
  panel.value?.focus()
  await loadConfiguration(true)
  panel.value?.querySelector<HTMLElement>('select')?.focus()
})
onUnmounted(() => {
  document.removeEventListener('keydown', key); document.body.style.overflow = previousOverflow
  apiKey.value = ''
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>

<template>
  <div class="management-overlay" @click.self="close">
    <form ref="panel" class="management-dialog model-settings" role="dialog" aria-modal="true" aria-labelledby="model-settings-title" tabindex="-1" @submit.prevent="submit('save')">
      <div class="section-top"><div><h2 id="model-settings-title">模型配置</h2><p class="muted">为复习问答与笔记生成接入 AI 模型</p></div><button type="button" class="icon-button" aria-label="关闭模型配置" :disabled="!!busy" @click="close">×</button></div>
      <p class="model-current">{{ loading ? '正在读取当前配置…' : loadError ? '尚未读取到当前配置' : stored?.configured ? `当前模型：${stored.model}` : '尚未接入模型' }}<span v-if="stored?.source === 'environment'"> · 来自原有 .env 配置</span></p>
      <p class="muted model-help">配置保存到本机后端，保存后立即生效。API Key 不写入浏览器存储，已有密钥不会回显。</p>
      <p v-if="loading" role="status">正在读取配置…</p>
      <p v-if="loadError" class="document-error" role="alert">{{ loadError }} 表单仍可填写，检查连接和保存需要后端接口可用。</p>
      <p v-if="catalogError && !loadError" class="document-error">模型目录读取失败：{{ catalogError }} 仍可填写自定义接口。</p>
      <p v-if="catalogLoading && !loading" class="muted" role="status">模型目录正在加载，可以先填写自定义接口。</p>
      <button v-if="loadError || catalogError" type="button" class="text-button" :disabled="loading || catalogLoading || !!busy" @click="loadConfiguration()">{{ loading || catalogLoading ? '读取中…' : '重新读取配置' }}</button>
      <fieldset :disabled="disabled" class="model-fields">
        <label>服务商<select v-model="form.provider" @change="providerChanged"><option value="custom">自定义 / OpenAI 兼容 / 本地模型</option><option v-if="form.provider !== 'custom' && !catalog.some(provider => provider.id === form.provider)" :value="form.provider">{{ form.provider }}</option><option v-for="provider in catalog" :key="provider.id" :value="provider.id">{{ provider.name }}</option></select></label>
        <label>模型名称<input v-model="form.model" list="model-catalog" placeholder="服务商提供的模型 ID" required maxlength="200" @change="modelChanged" /><datalist id="model-catalog"><option v-for="model in availableModels" :key="model.id" :value="model.id">{{ model.name }}{{ model.supports_images ? ' · 支持图片' : '' }}</option></datalist><small>可从目录选择，也可直接填写服务商提供的模型 ID。</small></label>
        <label class="model-checkbox"><input v-model="form.supports_images" type="checkbox" :disabled="!!selectedCatalogModel" />支持图片输入（视觉模型）</label>
        <small>{{ selectedCatalogModel ? (form.supports_images ? '该模型支持图片，带图练习会自动发送题图。' : '该模型只支持文字；解答带图题目需要选择支持图片的模型。') : '自定义或手动填写的模型，请确认服务支持图片后勾选；勾选不会让纯文本模型获得看图能力。' }}</small>
        <label>接口协议<select v-model="form.api"><option value="openai-completions">OpenAI Chat Completions（兼容接口）</option><option value="openai-responses">OpenAI Responses</option><option value="anthropic-messages">Anthropic Messages</option><option value="google-generative-ai">Google Gemini</option></select></label>
        <label>接口根地址<input v-model="form.base_url" type="url" placeholder="例如 https://api.deepseek.com/v1" required maxlength="2000" /><small>填写服务商的根地址，兼容接口通常带 /v1；不要填写 /chat/completions 等完整请求路径。</small></label>
        <label>API Key<span class="model-key-input"><input v-model="apiKey" :type="showKey ? 'text' : 'password'" autocomplete="off" spellcheck="false" :disabled="removeKey" :placeholder="retainKey ? '已保存密钥；留空保留，输入新密钥替换' : '填写服务商 API Key'" /><button type="button" class="text-button" @click="showKey = !showKey">{{ showKey ? '隐藏' : '显示' }}</button></span></label>
        <label v-if="retainKey" class="model-checkbox"><input v-model="removeKey" type="checkbox" />清除已有密钥（免密钥服务需同时勾选下方选项）</label>
        <label v-if="form.provider === 'custom'" class="model-checkbox"><input v-model="form.allow_without_key" type="checkbox" />无需 API Key（用于 Ollama 等本地服务）</label>
        <details class="model-advanced"><summary>高级设置</summary><div class="model-number-grid"><label>请求超时（秒）<input v-model.number="form.timeout_seconds" type="number" min="10" max="600" required /></label><label>上下文窗口（tokens）<input v-model.number="form.context_window" type="number" min="4096" max="2000000" required /></label><label>最大输出（tokens）<input v-model.number="form.max_tokens" type="number" min="256" :max="Math.min(form.context_window, 200000)" required /></label></div><label class="model-checkbox"><input v-model="form.reasoning" type="checkbox" />模型具备推理能力（使用服务商默认思考设置）</label><small>上下文和输出上限应与服务商支持的数值一致。</small></details>
      </fieldset>
      <p v-if="active" class="document-error">正在回答、生成练习解析或处理复习任务，请完成后再保存模型配置。</p>
      <p v-if="error" class="document-error" role="alert">{{ error }}</p>
      <p v-if="feedback" class="model-feedback" role="status">{{ feedback }}</p>
      <p class="muted model-help">“检查连接”会发送一次简短请求，可能产生少量费用；保存配置不会调用模型。</p>
      <div class="management-dialog-actions"><button type="button" class="button subtle" :disabled="disabled" @click="submit('check')">{{ busy === 'check' ? '连接中…' : '检查连接' }}</button><button class="button primary" :disabled="disabled || active">{{ busy === 'save' ? '保存中…' : '保存并接入' }}</button></div>
    </form>
  </div>
</template>
