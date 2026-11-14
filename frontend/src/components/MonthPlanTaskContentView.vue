<template>
  <div class="month-plan-task-content-view" :class="{ 'is-structured': structured }">
    <template v-if="structured">
      <strong>{{ title }}</strong>
      <span v-for="(line, index) in bodyLines" :key="`${index}-${line}`">{{ line }}</span>
    </template>
    <span v-else>{{ displayText }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  content?: string | null
  fallback?: string | null
  emptyText?: string
}>(), {
  fallback: '',
  emptyText: '未填写工作计划内容',
})

const lines = computed(() => String(props.content || props.fallback || '')
  .replace(/\r\n?/g, '\n')
  .split('\n')
  .map((line) => line.trim())
  .filter(Boolean))
const structured = computed(() => lines.value.length > 1)
const title = computed(() => lines.value[0] || '')
const bodyLines = computed(() => lines.value.slice(1))
const displayText = computed(() => lines.value.join('\n') || props.emptyText)
</script>

<style scoped>
.month-plan-task-content-view {
  margin: 0;
  color: inherit;
  font: inherit;
  line-height: inherit;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.month-plan-task-content-view strong,
.month-plan-task-content-view span {
  display: block;
}

.month-plan-task-content-view strong {
  margin-bottom: 2px;
  font-weight: 750;
}
</style>
