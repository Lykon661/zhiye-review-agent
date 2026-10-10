<script setup lang="ts">
import { computed } from 'vue'
import type { TokenUsage as Usage } from '../types'

const props = defineProps<{ usage?: Usage | null }>()
const labels: Record<string, string> = { planning: '行动规划', retrieval: '检索与重排', answer: '回答与出题', other: '其他调用' }
const number = (value: number | null | undefined) => value == null ? '未返回' : value.toLocaleString('zh-CN')
const headline = computed(() => {
  const usage = props.usage
  if (!usage) return 'Tokens 未记录'
  if (usage.status === 'unavailable') return 'Tokens 服务商未返回有效用量'
  if (usage.status === 'none') return '本次 0 Tokens'
  return `${usage.status === 'partial' ? '本次已知' : '本次'} ${number(usage.total_tokens)} Tokens`
})
const detail = computed(() => {
  const usage = props.usage
  if (!usage) return ''
  if (usage.status === 'none') return '无模型调用'
  if (usage.status === 'unavailable') return `${usage.model_calls} 次调用`
  return `输入 ${number(usage.input_tokens)} · 输出 ${number(usage.output_tokens)} · ${usage.model_calls} 次调用`
})
</script>

<template>
  <div class="token-usage">
    <details v-if="usage && usage.model_calls > 0">
      <summary><span>{{ headline }}</span><span class="token-usage-detail">{{ detail }}</span><span v-if="usage.status === 'partial'" class="token-usage-partial">部分用量未返回</span></summary>
      <div class="token-usage-panel">
        <table><caption class="token-usage-caption">本次回复的模型用量</caption><thead><tr><th scope="col">环节</th><th scope="col">输入</th><th scope="col">输出</th><th scope="col">调用</th></tr></thead>
          <tbody><tr v-for="stage in usage.stages" :key="stage.stage"><th scope="row">{{ labels[stage.stage] || '其他调用' }}<small v-if="stage.status === 'partial'">部分已知</small></th><td>{{ number(stage.input_tokens) }}</td><td>{{ number(stage.output_tokens) }}</td><td>{{ stage.model_calls }}</td></tr></tbody>
        </table>
        <p v-if="usage.unreported_calls">{{ usage.unreported_calls }} 次调用没有返回可核实的用量，未按 0 计算。</p>
        <p v-if="usage.cache_read_tokens || usage.cache_write_tokens">缓存读取 {{ number(usage.cache_read_tokens) }} · 缓存写入 {{ number(usage.cache_write_tokens) }}，已包含在输入中。</p>
        <p v-if="usage.reasoning_tokens != null">已报告的推理 {{ number(usage.reasoning_tokens) }} Tokens，已包含在输出中。</p>
        <p>统计本次回复触发的规划、检索和生成调用，不含后台记忆整理。用量来自服务商，未使用字数估算。</p>
      </div>
    </details>
    <p v-else :title="!usage ? '这条消息没有保存用量记录，无法补算实际 Tokens。' : undefined"><span>{{ headline }}</span><span v-if="detail" class="token-usage-detail">{{ detail }}</span></p>
    <p v-if="usage?.note" class="token-usage-note">{{ usage.note }}</p>
  </div>
</template>

<style scoped>
.token-usage { margin-top: 14px; color: var(--muted); font-size: 11px; line-height: 1.7; font-variant-numeric: tabular-nums; }
.token-usage summary { cursor: pointer; width: fit-content; max-width: 100%; }
.token-usage p { margin: 0; }
.token-usage-detail { margin-left: 10px; }
.token-usage-partial { margin-left: 10px; color: var(--accent); }
.token-usage-panel { margin-top: 9px; padding: 10px 14px; border: 1px solid var(--line); border-radius: 8px; max-width: 520px; background: var(--surface-alt); }
.token-usage table { width: 100%; border-collapse: collapse; font-size: inherit; }
.token-usage-caption { text-align: left; font-weight: 500; margin-bottom: 5px; }
.token-usage th, .token-usage td { padding: 4px 8px; text-align: right; font-weight: 400; }
.token-usage th:first-child { text-align: left; padding-left: 0; }
.token-usage th:last-child, .token-usage td:last-child { padding-right: 0; }
.token-usage small { display: block; font-size: 10px; }
.token-usage-panel p { margin-top: 7px; }
.token-usage .token-usage-note { margin-top: 3px; }
@media (max-width: 640px) { .token-usage-detail { display: inline-block; margin-left: 7px; } .token-usage-panel { padding: 8px 10px; } }
</style>
