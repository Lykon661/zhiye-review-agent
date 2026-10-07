<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import type { Source } from '../types'
import { kindLabels } from '../types'
const props = defineProps<{ source: Source | null; loading: boolean; error: string }>()
// Remove legacy parser review markers without modifying stored source files.
const sourceText = computed(() => (props.source?.text || '')
  .replace(/\[Office 公式部分转换，需核对\]\s*\$\$[\s\S]*?\$\$/g, '')
  .replace(/\[图片公式自动识别，需核对\]/g, '[图片公式自动识别]'))
const emit = defineEmits<{ close: [] }>()
const drawer = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') emit('close')
  if (event.key === 'Tab') {
    const targets = drawer.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]')
    if (!targets?.length) return
    const first = targets[0], last = targets[targets.length - 1]
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
  }
}
onMounted(() => {
  previousFocus = document.activeElement as HTMLElement | null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  drawer.value?.querySelector<HTMLButtonElement>('button')?.focus()
  document.addEventListener('keydown', key)
})
onUnmounted(() => {
  document.removeEventListener('keydown', key)
  document.body.style.overflow = previousOverflow
  previousFocus?.focus()
})
</script>
<template>
  <div class="drawer-overlay" @click.self="emit('close')">
    <aside ref="drawer" class="source-drawer" role="dialog" aria-modal="true" aria-label="资料原文">
      <div class="section-top"><div><h2>原文预览</h2></div><button class="icon-button" aria-label="关闭原文预览" @click="emit('close')">×</button></div>
      <p v-if="loading" class="muted">正在读取原文…</p>
      <div v-else-if="error" class="notice error" role="alert">{{ error }}</div>
      <template v-else-if="source">
        <div class="source-heading"><span class="file-icon">{{ kindLabels[source.kind] }}</span><div><h3>{{ source.filename }}</h3><p class="muted">{{ source.locator.label }}<span v-if="source.year"> · {{ source.year }} 年</span></p></div></div>
        <div class="source-text">{{ sourceText }}</div>
      </template>
    </aside>
  </div>
</template>
