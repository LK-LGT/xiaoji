<template>
  <section class="change-review" aria-label="AI 月计划变更预览">
    <header class="review-heading">
      <el-button :icon="ArrowLeft" text @click="emit('close')">返回助手</el-button>
    </header>

    <div class="suggestion-list">
      <article v-for="(item, index) in suggestion?.items || []" :key="`suggestion-${index}`" class="suggestion-card" :class="{ 'is-selected': selectedIndexes.includes(index) }">
        <header>
          <el-checkbox :model-value="selectedIndexes.includes(index)" @change="toggleItem(index, Boolean($event))">
            任务 {{ index + 1 }}
          </el-checkbox>
          <el-tag v-if="showCurrent && itemChanged(index)" size="small" type="warning" effect="plain">有修改</el-tag>
          <el-tag v-else-if="operationMode === 'SUPPLEMENT'" size="small" type="success" effect="plain">新增</el-tag>
        </header>
        <div v-if="showCurrent && currentItems[index]" class="current-copy">
          <span>修改前</span>
          <p>{{ currentItems[index].taskName || '未命名任务' }} · {{ currentItems[index].deliverable || '未填写交付物' }}</p>
        </div>
        <h3>{{ item.taskName || '未命名任务' }}</h3>
        <MonthPlanTaskContentView class="task-content" :content="item.taskContent" empty-text="未填写任务内容" />
        <dl>
          <div><dt>交付物</dt><dd>{{ item.deliverable || '-' }}</dd></div>
          <div><dt>质量标准</dt><dd>{{ item.quality || '-' }}</dd></div>
          <div><dt>预估时间</dt><dd>{{ item.estimatedStartDate || '-' }} 至 {{ item.estimatedEndDate || '-' }}</dd></div>
          <div><dt>权重</dt><dd>{{ Number(item.performanceWeight || 0) }}%</dd></div>
          <div><dt>预估价值</dt><dd>¥{{ Number(item.estimatedValue || 0).toLocaleString('zh-CN') }}</dd></div>
        </dl>
      </article>
      <el-empty v-if="!suggestion?.items.length" description="AI 没有返回可应用的任务" :image-size="60" />
    </div>

    <details v-if="suggestion?.warnings?.length" class="warning-summary">
      <summary>AI 参考提醒 {{ suggestion.warnings.length }} 条</summary>
      <div class="warning-list">
        <p v-for="warning in suggestion.warnings" :key="warning">{{ warning }}</p>
      </div>
    </details>

    <details v-if="suggestion?.knowledgeReferences?.length" class="knowledge-summary">
      <summary>本次按分类召回 {{ suggestion.knowledgeReferences.length }} 份企业知识</summary>
      <div class="knowledge-list">
        <div v-for="item in suggestion.knowledgeReferences" :key="`${item.knowledgeId}@${item.version}`">
          <strong>{{ item.title || item.knowledgeId }}</strong>
          <span>{{ item.knowledgeId }}<template v-if="item.version"> · v{{ item.version }}</template> · {{ bindingLabel(item.binding) }}</span>
        </div>
      </div>
    </details>

    <footer class="review-actions">
      <el-button @click="emit('ignore')">放弃本次建议</el-button>
      <el-button type="primary" :disabled="!canApply" @click="applyToCurrentPlan">应用到当前计划</el-button>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowLeft } from '@element-plus/icons-vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import type { AiGenerateResponse, AiPlanItem, MonthPlanAiOperationMode } from '@/api/employeeAi'

type PreviewOperationMode = Exclude<MonthPlanAiOperationMode, 'AUTO' | 'CLARIFY'>

export interface AiPlanDraftItem extends AiPlanItem {
  /** BUG-R6-14：月计划条目 ID 是字符串主键。 */
  id?: string
}

const props = defineProps<{
  visible: boolean
  operationMode: PreviewOperationMode
  suggestion?: AiGenerateResponse
  currentItems: AiPlanDraftItem[]
}>()
const emit = defineEmits<{
  close: []
  ignore: []
  apply: [value: { mode: 'replace' | 'append'; indexes: number[]; items: AiPlanDraftItem[]; adoptedWithEdit: false }]
}>()

const selectedIndexes = ref<number[]>([])
const showCurrent = computed(() => props.operationMode === 'OPTIMIZE' || props.operationMode === 'REVISE_REJECTION')
const selectedItems = computed(() => selectedIndexes.value
  .map((index) => ({ index, item: props.suggestion?.items[index] }))
  .filter((entry): entry is { index: number; item: AiPlanItem } => Boolean(entry.item)))
const canApply = computed(() => selectedItems.value.length > 0)

watch(
  () => [props.visible, props.suggestion?.suggestionId] as const,
  ([visible]) => { if (visible) resetSelection() },
  { immediate: true },
)

function resetSelection() {
  selectedIndexes.value = props.suggestion?.items.map((_, index) => index) || []
}

function toggleItem(index: number, selected: boolean) {
  selectedIndexes.value = selected
    ? [...new Set([...selectedIndexes.value, index])].sort((a, b) => a - b)
    : selectedIndexes.value.filter((value) => value !== index)
}

function normalizeItem(item: AiPlanItem, current?: AiPlanDraftItem): AiPlanDraftItem {
  return {
    id: current?.id,
    workType: item.workType ?? 'UNKNOWN',
    taskName: String(item.taskName ?? ''),
    taskContent: String(item.taskContent ?? ''),
    deliverable: String(item.deliverable ?? ''),
    quality: String(item.quality ?? ''),
    estimatedStartDate: String(item.estimatedStartDate ?? ''),
    estimatedEndDate: String(item.estimatedEndDate ?? ''),
    performanceWeight: Number(item.performanceWeight ?? 0),
    estimatedValue: Number(item.estimatedValue ?? 0),
  }
}

function itemChanged(index: number) {
  const suggestion = props.suggestion?.items[index]
  const current = props.currentItems[index]
  if (!suggestion || !current) return true
  const { id: _id, ...currentWithoutId } = current
  return JSON.stringify(normalizeItem(suggestion)) !== JSON.stringify(normalizeItem(currentWithoutId))
}

function applyToCurrentPlan() {
  const append = props.operationMode === 'SUPPLEMENT'
  emit('apply', {
    mode: append ? 'append' : 'replace',
    indexes: [...selectedIndexes.value],
    items: selectedItems.value.map(({ item }) => normalizeItem(item)),
    adoptedWithEdit: false,
  })
}

function bindingLabel(value: string) {
  return ({ NORMATIVE: '正式规范', REFERENCE: '参考资料', EXAMPLE: '示例案例' } as Record<string, string>)[value] || value || '知识资料'
}
</script>

<style scoped>
.change-review { display:grid; gap:16px; min-width:0; }
.review-heading { display:flex; align-items:center; min-height:32px; }
.current-copy span { color:#64748b; font-size:10px; font-weight:700; }
.current-copy p { margin:4px 0 0; overflow-wrap:anywhere; color:#334155; font-size:12px; line-height:1.55; }
.suggestion-list { display:grid; gap:10px; max-height:46vh; overflow:auto; padding-right:3px; }
.suggestion-card { padding:14px; border:1px solid #dfe5e3; border-left:3px solid transparent; border-radius:7px; background:#fff; }
.suggestion-card.is-selected { border-left-color:#2d776c; background:#fbfdfc; }
.suggestion-card > header { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.suggestion-card h3 { margin:12px 0 5px; color:#203a35; font-size:15px; }
.task-content { margin:0; color:#52645f; font-size:12px; line-height:1.6; }
.current-copy { margin-top:10px; padding:8px 10px; border-left:2px solid #d7dfdc; background:#f6f8f7; }
.suggestion-card dl { display:grid; grid-template-columns:1.4fr 1.4fr .8fr .6fr .8fr; gap:8px; margin:12px 0 0; }
.suggestion-card dl div { min-width:0; padding:8px; background:#f7f9f8; }
.suggestion-card dt { color:#71817c; font-size:10px; }
.suggestion-card dd { margin:4px 0 0; overflow-wrap:anywhere; color:#33413d; font-size:11px; line-height:1.45; }
.warning-summary { color:#6b5a3d; font-size:12px; }
.warning-summary summary { cursor:pointer; }
.warning-list { display:grid; gap:6px; max-height:140px; margin-top:8px; overflow:auto; }
.warning-list p { margin:0; padding:8px 10px; background:#fff8e8; }
.knowledge-summary { color:#40544e; font-size:12px; }
.knowledge-summary summary { cursor:pointer; }
.knowledge-list { display:grid; gap:1px; max-height:150px; margin-top:8px; overflow:auto; background:#dfe7e4; }
.knowledge-list div { display:flex; align-items:center; justify-content:space-between; gap:12px; min-width:0; padding:9px 10px; background:#f7faf8; }
.knowledge-list strong { overflow:hidden; color:#33413d; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.knowledge-list span { flex:none; color:#71817c; font-size:10px; }
.review-actions { position:sticky; bottom:-16px; z-index:2; display:flex; align-items:center; justify-content:flex-end; gap:10px; padding:12px 0 4px; border-top:1px solid #e2e8e5; background:#fff; }

@media (max-width:760px) {
  .suggestion-card dl { grid-template-columns:1fr 1fr; }
  .review-actions { align-items:stretch; flex-direction:column-reverse; }
  .review-actions .el-button { width:100%; margin:0; }
}
</style>
