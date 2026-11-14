<template>
  <el-drawer
    :model-value="modelValue"
    class="plan-ai-review-drawer"
    :title="`${planTypeLabel}AI检查报告`"
    size="min(820px, calc(100vw - 24px))"
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="plan-ai-review-drawer__body">
      <header v-if="subject || context" class="plan-ai-review-drawer__identity">
        <span>{{ planTypeLabel }}</span>
        <strong>{{ subject || `${planTypeLabel}检查结果` }}</strong>
        <small v-if="context">{{ context }}</small>
      </header>
      <el-skeleton v-if="loading" :rows="7" animated />
      <AiReviewPanel
        v-else
        :review="review"
        :stale="stale"
        :title="`AI${planTypeLabel}检查`"
        :show-capability="false"
        :show-completion-range="false"
        :empty-text="`该${planTypeLabel}尚未生成AI检查报告。`"
      />
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { AiReview } from '@/api/aiReview'
import AiReviewPanel from '@/components/AiReviewPanel.vue'

const props = withDefaults(defineProps<{
  modelValue: boolean
  planType: 'DAY' | 'WEEK' | 'MONTH'
  review?: AiReview | null
  loading?: boolean
  stale?: boolean
  subject?: string
  context?: string
}>(), {
  review: null,
  loading: false,
  stale: false,
  subject: '',
  context: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const planTypeLabel = computed(() => ({
  DAY: '日计划',
  WEEK: '周计划',
  MONTH: '月计划',
})[props.planType])
</script>

<style scoped>
.plan-ai-review-drawer__body {
  display: grid;
  gap: 14px;
}

.plan-ai-review-drawer__identity {
  display: grid;
  gap: 4px;
  padding: 0 0 14px;
  border-bottom: 1px solid var(--line);
}

.plan-ai-review-drawer__identity span {
  color: var(--blue);
  font-size: 11px;
  font-weight: 800;
}

.plan-ai-review-drawer__identity strong {
  color: var(--ink);
  font-size: 17px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}

.plan-ai-review-drawer__identity small {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}
</style>
