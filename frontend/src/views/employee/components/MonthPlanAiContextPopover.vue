<template>
  <el-popover
    placement="bottom-end"
    width="min(520px, calc(100vw - 24px))"
    trigger="click"
    popper-class="month-ai-context-popper"
  >
    <template #reference>
      <el-button
        :icon="Collection"
        circle
        title="参考资料"
        :aria-label="`参考资料，已选 ${selectedCount} 项`"
        @click="emit('refresh')"
      />
    </template>

    <section class="context-popover" aria-label="参考资料">
      <header>
        <div><strong>参考资料</strong><span>已选 {{ selectedCount }}/{{ MONTH_PLAN_CONTEXT_TYPES.length }}</span></div>
        <el-checkbox :model-value="allSelected" :indeterminate="selectionIndeterminate" :disabled="disabled" @change="toggleAll">全选</el-checkbox>
      </header>

      <div v-if="contextInfo?.generationProfileLabel" class="profile-hint">
        <span>当前生成方向</span><strong>{{ contextInfo.generationProfileLabel }}</strong>
      </div>

      <div class="context-list">
        <div v-for="option in contextOptions" :key="option.type" class="context-option" :class="{ 'has-children': isGrouped(option.type) }">
          <div class="context-copy">
            <el-checkbox
              :model-value="contextChecked(option.type)"
              :indeterminate="contextIndeterminate(option.type)"
              :disabled="disabled || (isGrouped(option.type) && !option.available)"
              @change="toggleContext(option.type, $event)"
            >{{ option.label }}</el-checkbox>
            <span v-if="option.available && option.summary" class="context-summary" :title="option.summary">
              {{ option.summary }}
            </span>
          </div>
          <div class="context-tools">
            <span class="context-status" :class="{ unavailable: !option.available }">
              {{ option.available ? '可用' : '暂无' }}
            </span>
            <el-tooltip v-if="isGrouped(option.type)" :content="`${groupExpanded(option.type) ? '收起' : '展开'}${option.label}`" placement="top">
              <el-button
                text
                circle
                :icon="groupExpanded(option.type) ? ArrowDown : ArrowRight"
                :aria-label="`${groupExpanded(option.type) ? '收起' : '展开'}${option.label}`"
                @click="toggleExpanded(option.type)"
              />
            </el-tooltip>
          </div>
          <el-input
            v-if="option.type === 'JOB_DESCRIPTION'"
            :model-value="jobDescription"
            maxlength="500"
            placeholder="补充岗位说明"
            :disabled="disabled || !selectedContextTypes.includes('JOB_DESCRIPTION')"
            @update:model-value="emit('update:jobDescription', String($event || ''))"
          />
          <div v-if="option.type === 'HISTORY_PLANS' && historyExpanded" class="suboption-list" aria-label="历史计划月份">
            <el-checkbox
              v-for="item in historyMonthOptions"
              :key="item.month"
              :model-value="selectedHistoryMonths.includes(item.month)"
              :disabled="disabled || !item.available"
              @change="toggleHistoryMonth(item.month, $event)"
            >
              <span class="suboption-copy"><strong>{{ item.label }}</strong><small>{{ item.available ? `${item.planCount} 份计划` : '暂无已审批计划' }}</small></span>
            </el-checkbox>
          </div>
          <div v-if="option.type === 'ENTERPRISE_KNOWLEDGE' && knowledgeExpanded" class="suboption-list" aria-label="企业知识分类">
            <el-checkbox
              v-for="item in knowledgeTypeOptions"
              :key="item.type"
              :model-value="selectedKnowledgeTypes.includes(item.type)"
              :disabled="disabled || !item.available"
              @change="toggleKnowledgeType(item.type, $event)"
            >
              <span class="suboption-copy"><strong>{{ item.label }}</strong><small>{{ item.available ? `${item.documentCount} 份文档` : '暂无可引用文档' }}</small></span>
            </el-checkbox>
          </div>
        </div>
      </div>

      <section v-if="enterpriseKnowledgeSelected" class="knowledge-match" aria-label="智能匹配的企业知识">
        <header>
          <div><strong>智能匹配知识</strong><span>{{ knowledgeReferences.length }} 份</span></div>
          <el-button link type="primary" :icon="RefreshRight" :loading="refreshing" :disabled="disabled" @click="emit('refresh')">按当前描述刷新</el-button>
        </header>
        <ul v-if="knowledgeReferences.length">
          <li v-for="item in knowledgeReferences" :key="`${item.knowledgeId}@${item.version}`">
            <div><strong>{{ item.title || item.knowledgeId }}</strong><span>{{ item.knowledgeId }}<template v-if="item.version"> · v{{ item.version }}</template></span></div>
            <span :class="`binding--${item.binding.toLowerCase()}`">{{ bindingLabel(item.binding) }}</span>
          </li>
        </ul>
        <p v-else>{{ refreshing ? '正在按分类和当前描述匹配知识…' : '当前描述暂未匹配到适用知识，生成时不会引用无关文档。' }}</p>
      </section>

      <footer v-if="contextInfo">
        <span>今日剩余</span>
        <div class="quota-list">
          <strong>生成 {{ contextInfo.remainingCalls.generate ?? 0 }}</strong>
          <strong>优化 {{ contextInfo.remainingCalls.optimize ?? 0 }}</strong>
          <strong>检查 {{ contextInfo.remainingCalls.check ?? 0 }}</strong>
        </div>
      </footer>
    </section>
  </el-popover>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ArrowDown, ArrowRight, Collection, RefreshRight } from '@element-plus/icons-vue'
import {
  MONTH_PLAN_CONTEXT_LABELS,
  MONTH_PLAN_CONTEXT_TYPES,
  type AiContextResponse,
  type MonthPlanContextType,
} from '@/api/employeeAi'

const props = withDefaults(defineProps<{
  contextInfo?: AiContextResponse
  selectedContextTypes: MonthPlanContextType[]
  selectedHistoryMonths: string[]
  selectedKnowledgeTypes: string[]
  jobDescription: string
  disabled?: boolean
  refreshing?: boolean
}>(), { disabled: false, refreshing: false })

const emit = defineEmits<{
  'update:selectedContextTypes': [value: MonthPlanContextType[]]
  'update:selectedHistoryMonths': [value: string[]]
  'update:selectedKnowledgeTypes': [value: string[]]
  'update:jobDescription': [value: string]
  refresh: []
}>()

const historyExpanded = ref(true)
const knowledgeExpanded = ref(true)
const historyMonthOptions = computed(() => props.contextInfo?.historyMonthOptions || [])
const knowledgeTypeOptions = computed(() => props.contextInfo?.knowledgeTypeOptions || [])
const availableHistoryMonths = computed(() => historyMonthOptions.value.filter((item) => item.available).map((item) => item.month))
const availableKnowledgeTypes = computed(() => knowledgeTypeOptions.value.filter((item) => item.available).map((item) => item.type))
const selectableContextTypes = computed(() => MONTH_PLAN_CONTEXT_TYPES.filter((type) => {
  if (type === 'HISTORY_PLANS') return availableHistoryMonths.value.length > 0
  if (type === 'ENTERPRISE_KNOWLEDGE') return availableKnowledgeTypes.value.length > 0
  return true
}))
const selectedCount = computed(() => props.selectedContextTypes.length)
const allSelected = computed(() => selectableContextTypes.value.every((type) => props.selectedContextTypes.includes(type)))
const selectionIndeterminate = computed(() => selectedCount.value > 0 && !allSelected.value)
const enterpriseKnowledgeSelected = computed(() => props.selectedContextTypes.includes('ENTERPRISE_KNOWLEDGE'))
const knowledgeReferences = computed(() => (props.contextInfo?.knowledgeReferences || [])
  .filter((item) => props.selectedKnowledgeTypes.includes(item.knowledgeType)))
const contextOptions = computed(() => MONTH_PLAN_CONTEXT_TYPES.map((type) => {
  const remote = props.contextInfo?.contextOptions?.find((item) => item.type === type)
  const jobSummary = props.jobDescription.trim()
  const group = groupState(type)
  return {
    type,
    label: remote?.label || MONTH_PLAN_CONTEXT_LABELS[type],
    available: group ? group.available.length > 0 : type === 'JOB_DESCRIPTION'
      ? Boolean(jobSummary)
      : remote?.available ?? Boolean(props.contextInfo?.availableContext.includes(MONTH_PLAN_CONTEXT_LABELS[type])),
    summary: group
      ? `已选 ${group.selected.length}/${group.total} ${type === 'HISTORY_PLANS' ? '个月' : '类'}`
      : type === 'JOB_DESCRIPTION' ? props.jobDescription.trim() : String(remote?.summary || '').trim(),
  }
}))

function toggleAll(value: boolean | string | number) {
  const selected = Boolean(value)
  emit('update:selectedContextTypes', selected ? [...selectableContextTypes.value] : [])
  emit('update:selectedHistoryMonths', selected ? [...availableHistoryMonths.value] : [])
  emit('update:selectedKnowledgeTypes', selected ? [...availableKnowledgeTypes.value] : [])
}

function isGrouped(type: MonthPlanContextType) {
  return type === 'HISTORY_PLANS' || type === 'ENTERPRISE_KNOWLEDGE'
}

function groupState(type: MonthPlanContextType) {
  if (type === 'HISTORY_PLANS') {
    return { available: availableHistoryMonths.value, selected: props.selectedHistoryMonths, total: historyMonthOptions.value.length }
  }
  if (type === 'ENTERPRISE_KNOWLEDGE') {
    return { available: availableKnowledgeTypes.value, selected: props.selectedKnowledgeTypes, total: knowledgeTypeOptions.value.length }
  }
  return undefined
}

function contextChecked(type: MonthPlanContextType) {
  const group = groupState(type)
  return group
    ? group.available.length > 0 && group.available.every((value) => group.selected.includes(value))
    : props.selectedContextTypes.includes(type)
}

function contextIndeterminate(type: MonthPlanContextType) {
  const group = groupState(type)
  if (!group) return false
  const selected = group.available.filter((value) => group.selected.includes(value)).length
  return selected > 0 && selected < group.available.length
}

function toggleContext(type: MonthPlanContextType, value: boolean | string | number) {
  if (type === 'HISTORY_PLANS') {
    toggleHistoryGroup(Boolean(value))
    return
  }
  if (type === 'ENTERPRISE_KNOWLEDGE') {
    toggleKnowledgeGroup(Boolean(value))
    return
  }
  setParentContext(type, Boolean(value))
}

function toggleHistoryGroup(selected: boolean) {
  const values = selected ? [...availableHistoryMonths.value] : []
  emit('update:selectedHistoryMonths', values)
  setParentContext('HISTORY_PLANS', values.length > 0)
}

function toggleKnowledgeGroup(selected: boolean) {
  const values = selected ? [...availableKnowledgeTypes.value] : []
  emit('update:selectedKnowledgeTypes', values)
  setParentContext('ENTERPRISE_KNOWLEDGE', values.length > 0)
}

function toggleHistoryMonth(month: string, value: boolean | string | number) {
  const selected = updateSelection(props.selectedHistoryMonths, month, Boolean(value))
  emit('update:selectedHistoryMonths', selected)
  setParentContext('HISTORY_PLANS', selected.length > 0)
}

function toggleKnowledgeType(type: string, value: boolean | string | number) {
  const selected = updateSelection(props.selectedKnowledgeTypes, type, Boolean(value))
  emit('update:selectedKnowledgeTypes', selected)
  setParentContext('ENTERPRISE_KNOWLEDGE', selected.length > 0)
}

function updateSelection(values: string[], value: string, selected: boolean) {
  const next = new Set(values)
  if (selected) next.add(value)
  else next.delete(value)
  return [...next]
}

function setParentContext(type: MonthPlanContextType, selected: boolean) {
  const next = new Set(props.selectedContextTypes)
  if (selected) next.add(type)
  else next.delete(type)
  emit('update:selectedContextTypes', MONTH_PLAN_CONTEXT_TYPES.filter((item) => next.has(item)))
}

function groupExpanded(type: MonthPlanContextType) {
  return type === 'HISTORY_PLANS' ? historyExpanded.value : knowledgeExpanded.value
}

function toggleExpanded(type: MonthPlanContextType) {
  if (type === 'HISTORY_PLANS') historyExpanded.value = !historyExpanded.value
  if (type === 'ENTERPRISE_KNOWLEDGE') knowledgeExpanded.value = !knowledgeExpanded.value
}

function bindingLabel(value: string) {
  return ({ NORMATIVE: '正式规范', REFERENCE: '参考资料', EXAMPLE: '示例案例' } as Record<string, string>)[value] || value || '知识资料'
}
</script>

<style scoped>
.context-popover { display:grid; gap:10px; min-width:0; max-height:min(720px,78vh); overflow:hidden; }
.context-popover header { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:2px 2px 12px; border-bottom:1px solid #e4ebe8; }
.context-popover header > div { display:flex; align-items:baseline; gap:9px; }
.context-popover header strong { color:#203a35; font-size:15px; }
.context-popover header span { color:#71817c; font-size:12px; }
.profile-hint { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:9px 10px; background:#eef5f1; }
.profile-hint span { color:#71817c; font-size:11px; }
.profile-hint strong { color:#31554d; font-size:12px; text-align:right; }
.context-list { display:grid; grid-template-columns:minmax(0,1fr); max-height:min(410px,48vh); overflow-y:auto; overflow-x:hidden; scrollbar-gutter:stable; }
.context-option { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:start; gap:6px 10px; min-width:0; padding:7px 2px; border-bottom:1px solid #edf1ef; }
.context-copy { display:flex; align-items:flex-start; gap:8px; min-width:0; }
.context-tools { display:flex; align-items:center; gap:2px; }
.context-tools :deep(.el-button) { width:24px; height:24px; padding:0; }
.context-option :deep(.el-checkbox) { flex:0 1 auto; align-items:flex-start; min-width:0; height:auto; margin-right:0; }
.context-option :deep(.el-checkbox__input) { margin-top:2px; }
.context-option :deep(.el-checkbox__label) { min-width:0; padding-right:0; font-size:13px; line-height:1.4; white-space:normal; overflow-wrap:anywhere; }
.context-summary { flex:1; min-width:0; overflow:hidden; color:#7b8985; font-size:12px; line-height:19px; text-overflow:ellipsis; white-space:nowrap; }
.context-status { align-self:start; min-width:44px; padding:2px 6px; border-radius:4px; background:#e8f2ee; color:#3f7463; font-size:11px; line-height:18px; text-align:center; white-space:nowrap; }
.context-status.unavailable { background:#fff5e8; color:#a26b2c; }
.context-option .el-input { grid-column:1 / -1; margin-top:1px; }
.suboption-list { grid-column:1 / -1; display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:2px 10px; margin:0 2px 0 24px; padding:7px 9px; background:#f7f9f8; border-left:2px solid #cbdad4; }
.suboption-list :deep(.el-checkbox) { width:100%; min-height:32px; margin-right:0; padding:3px 2px; }
.suboption-list :deep(.el-checkbox__label) { flex:1; min-width:0; padding-left:8px; }
.suboption-copy { display:flex; align-items:baseline; justify-content:space-between; gap:12px; min-width:0; width:100%; }
.suboption-copy strong { overflow:hidden; color:#40544e; font-size:12px; font-weight:600; text-overflow:ellipsis; white-space:nowrap; }
.suboption-copy small { flex:none; color:#82908b; font-size:11px; }
.knowledge-match { display:grid; min-height:0; padding-top:2px; border-top:1px solid #e4ebe8; }
.knowledge-match > header { padding:8px 0 6px; border-bottom:0; }
.knowledge-match > header > div { display:flex; align-items:baseline; gap:8px; }
.knowledge-match ul { display:grid; gap:0; max-height:150px; margin:0; padding:0; overflow:auto; list-style:none; }
.knowledge-match li { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:10px; min-width:0; padding:8px 2px; border-top:1px solid #edf1ef; }
.knowledge-match li > div { display:grid; min-width:0; gap:2px; }
.knowledge-match li strong { overflow:hidden; color:#33413d; font-size:13px; text-overflow:ellipsis; white-space:nowrap; }
.knowledge-match li span { color:#82908b; font-size:11px; }
.knowledge-match li > span { padding:2px 6px; border-radius:4px; background:#edf2f0; color:#60736d; white-space:nowrap; }
.knowledge-match li > span.binding--normative { background:#e7f2ed; color:#28634f; }
.knowledge-match p { margin:0; padding:7px 2px; color:#7b8985; font-size:12px; line-height:1.5; }
.context-popover footer { display:flex; align-items:center; gap:10px; padding:8px 2px 0; border-top:1px solid #e4ebe8; color:#71817c; font-size:11px; }
.context-popover footer > span { flex:none; }
.quota-list { display:flex; flex:1; justify-content:flex-end; gap:8px 18px; min-width:0; }
.quota-list strong { color:#40544e; white-space:nowrap; }
@media (max-width:420px) {
  .context-popover header { align-items:flex-start; }
  .context-popover header > div { display:grid; gap:2px; }
  .context-popover footer { align-items:flex-start; }
  .quota-list { flex-wrap:wrap; gap:6px 12px; }
  .suboption-list { grid-template-columns:1fr; }
}
</style>
