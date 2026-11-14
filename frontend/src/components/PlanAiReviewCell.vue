<template>
  <div class="plan-ai-review-cell">
    <AiReviewStatusTag v-if="showStatus" :review="review" :loading="loading" :stale="stale" />
    <el-button v-if="review" link type="primary" size="small" @click.stop="emit('view')">查看报告</el-button>
  </div>
</template>

<script setup lang="ts">
import type { AiReview } from '@/api/aiReview'
import AiReviewStatusTag from '@/components/AiReviewStatusTag.vue'

withDefaults(defineProps<{
  review?: AiReview | null
  loading?: boolean
  stale?: boolean
  showStatus?: boolean
}>(), {
  review: null,
  loading: false,
  stale: false,
  showStatus: false,
})

const emit = defineEmits<{
  view: []
}>()
</script>

<style scoped>
.plan-ai-review-cell {
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 8px;
  min-width: 0;
}

.plan-ai-review-cell .el-button {
  height: auto;
  margin: 0;
  padding: 1px 0;
  white-space: nowrap;
}
</style>
