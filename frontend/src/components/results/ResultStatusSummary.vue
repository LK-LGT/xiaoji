<template>
  <div class="result-status-summary" aria-label="成果状态统计">
    <button type="button" :class="{ 'is-active': active === 'TODO' }" @click="$emit('select', 'TODO')">
      <strong>{{ counts.todo }}</strong><span>待填写</span>
    </button>
    <button type="button" :class="{ 'is-active': active === 'RETURNED' }" @click="$emit('select', 'RETURNED')">
      <strong>{{ counts.returned }}</strong><span>已退回</span>
    </button>
    <button type="button" :class="{ 'is-active': active === 'IN_REVIEW' }" @click="$emit('select', 'IN_REVIEW')">
      <strong>{{ counts.inReview }}</strong><span>审批中</span>
    </button>
    <button type="button" :class="{ 'is-active': active === 'DONE' }" @click="$emit('select', 'DONE')">
      <strong>{{ counts.done }}</strong><span>已完成</span>
    </button>
  </div>
</template>

<script setup lang="ts">
import type { ResultDisplayStatus } from '@/views/employee/resultStatus'

defineProps<{
  counts: { todo: number; returned: number; inReview: number; done: number }
  active?: ResultDisplayStatus
}>()
defineEmits<{ select: [status: ResultDisplayStatus] }>()
</script>
