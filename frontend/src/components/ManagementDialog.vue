<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
const props = defineProps<{ title: string; description: string; initialName?: string; confirmText: string; danger?: boolean; busy: boolean; error: string }>()
const emit = defineEmits<{ close: []; submit: [name: string] }>()
const name = ref(props.initialName ?? '')
const panel = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
let previousOverflow = ''
function close() { if (!props.busy) emit('close') }
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
  if (event.key !== 'Tab') return
  const targets = panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)')
  if (!targets?.length) { event.preventDefault(); return }
  const first = targets[0], last = targets[targets.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
onMounted(() => {
  previousFocus = document.activeElement as HTMLElement | null
  previousOverflow = document.body.style.overflow
  document.body.style.overflow = 'hidden'
  const input = panel.value?.querySelector<HTMLInputElement>('input')
  if (input) { input.focus(); input.select() }
  else panel.value?.querySelector<HTMLButtonElement>('button')?.focus()
  document.addEventListener('keydown', key)
})
onUnmounted(() => {
  document.removeEventListener('keydown', key)
  document.body.style.overflow = previousOverflow
  if (previousFocus?.isConnected) previousFocus.focus()
})
</script>
<template>
  <div class="management-overlay" @click.self="close">
    <form ref="panel" class="management-dialog" role="dialog" aria-modal="true" aria-labelledby="management-title" aria-describedby="management-description" tabindex="-1" @submit.prevent="emit('submit', name.trim())">
      <h2 id="management-title">{{ title }}</h2>
      <p id="management-description" class="muted">{{ description }}</p>
      <label v-if="initialName !== undefined" class="management-name">名称<input v-model="name" maxlength="100" :disabled="busy" required /></label>
      <p v-if="error" class="document-error" role="alert">{{ error }}</p>
      <div class="management-dialog-actions"><button type="button" class="button subtle" :disabled="busy" @click="close">取消</button><button :class="['button', danger ? 'management-delete' : 'primary']" :disabled="busy || (initialName !== undefined && !name.trim())">{{ busy ? '处理中…' : confirmText }}</button></div>
    </form>
  </div>
</template>
