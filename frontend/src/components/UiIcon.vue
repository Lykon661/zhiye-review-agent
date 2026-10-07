<script setup lang="ts">
type IconName = 'library' | 'outline' | 'practice' | 'wrong' | 'settings' | 'upload' | 'chat'
defineProps<{ name: IconName }>()
const paths: Record<IconName, string> = {
  library: 'M4 4h5v16H4z M9 7h5v13H9z M17 4l4 15-4 1-4-15z',
  outline: 'M7 3h10a2 2 0 0 1 2 2v16l-7-4-7 4V5a2 2 0 0 1 2-2z M9 8h6 M9 12h4',
  practice: 'M9 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-4 M9 15l3-1 9-9-2-2-9 9-1 3z M17 5l2 2',
  wrong: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3z',
  settings: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
  upload: 'M12 16V3 M7 8l5-5 5 5 M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4',
  chat: 'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2z M7 9h10 M7 13h6',
}
</script>

<template>
  <svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path :d="paths[name]" /></svg>
</template>

<style scoped>
.ui-icon { width: 20px; height: 20px; flex-shrink: 0; vertical-align: middle; }
</style>
