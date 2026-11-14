<template>
  <el-tag
    class="ai-review-status-tag"
    :type="meta.type"
    :effect="review ? 'light' : 'plain'"
    size="small"
    :title="review?.result.summary || meta.label"
  >
    {{ meta.label }}
  </el-tag>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AiReview } from '@/api/aiReview'

const props = withDefaults(defineProps<{
  review?: AiReview | null
  loading?: boolean
  stale?: boolean
}>(), {
  review: null,
  loading: false,
  stale: false,
})

const meta = computed(() => {
  const review = props.review
  if (props.loading) return { label: '检查读取中', type: 'info' as const }
  if (!review) return { label: '未检查', type: 'info' as const }
  if (props.stale || review.stale) return { label: '检查已过期', type: 'warning' as const }
  if (review.status === 'MODEL_FAILED') return { label: '检查失败', type: 'danger' as const }
  if (review.status === 'RULE_ONLY') return { label: '规则检查', type: 'warning' as const }
  if (review.overallRisk === 'HIGH') return { label: '高风险', type: 'danger' as const }
  const hasIssues = Boolean(review.result.issues?.length)
  const hasUnknownDimensions = Boolean(review.result.analysisDimensions?.some((item) => item.status === 'UNKNOWN'))
  if (review.overallRisk === 'MEDIUM' || hasIssues || hasUnknownDimensions) {
    return { label: '需要核对', type: 'warning' as const }
  }
  return { label: '检查通过', type: 'success' as const }
})
</script>

<style scoped>
.ai-review-status-tag { min-width: 64px; justify-content: center; }
</style>
