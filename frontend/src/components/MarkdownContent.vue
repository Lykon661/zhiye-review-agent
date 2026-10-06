<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../markdown'
import type { Source } from '../types'
const props = withDefaults(defineProps<{ content: string; sources?: Source[]; hideCitations?: boolean }>(), { sources: () => [], hideCitations: false })
const emit = defineEmits<{ source: [id: string] }>()
const html = computed(() => renderMarkdown(props.hideCitations ? props.content.replace(/\[S[a-fA-F0-9]{32}\]/g, '').replace(/\[S[a-fA-F0-9]{0,32}$/, '') : props.content, props.sources))
function click(event: MouseEvent) {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-source-id]')
  if (button?.dataset.sourceId) emit('source', button.dataset.sourceId)
}
</script>
<template><div class="markdown-content" v-html="html" @click="click"></div></template>
