<template>
  <section class="month-diff-view" aria-label="月计划版本差异">
    <el-empty
      v-if="diff.unchanged"
      :description="emptyText"
      :image-size="58"
    />

    <template v-else>
      <header class="diff-overview">
        <div class="diff-overview__copy">
          <strong>{{ heading }}</strong>
          <span>仅列出本次发生变化的内容</span>
        </div>
        <dl class="diff-counts" aria-label="变更数量">
          <div class="diff-count diff-count--modified"><dt>修改</dt><dd>{{ diff.counts.modified }}</dd></div>
          <div class="diff-count diff-count--added"><dt>新增</dt><dd>{{ diff.counts.added }}</dd></div>
          <div class="diff-count diff-count--removed"><dt>删除</dt><dd>{{ diff.counts.removed }}</dd></div>
        </dl>
      </header>

      <div class="diff-toolbar">
        <div class="diff-filter-scroll">
          <el-segmented v-model="activeFilter" :options="filterOptions" aria-label="筛选变更类型" />
        </div>
        <el-button
          text
          :icon="allVisibleExpanded ? ArrowUp : ArrowDown"
          :disabled="visibleChanges.length === 0"
          @click="toggleAllVisible"
        >
          {{ allVisibleExpanded ? '收起全部' : '展开全部' }}
        </el-button>
      </div>

      <div
        v-if="visibleChanges.length"
        class="split-diff-header"
        aria-hidden="true"
      >
        <span>变更字段</span>
        <span class="split-diff-column split-diff-column--before">
          <el-icon><Minus /></el-icon>{{ beforeLabel }}
        </span>
        <span class="split-diff-column split-diff-column--after">
          <el-icon><Plus /></el-icon>{{ afterLabel }}
        </span>
      </div>

      <div class="month-diff-list">
        <article
          v-for="change in visibleChanges"
          :key="change.key"
          class="month-diff-item"
          :class="`month-diff-item--${change.type}`"
        >
          <header class="month-diff-item__header">
            <span class="change-kind" :class="`change-kind--${change.type}`">
              <el-icon>
                <Plus v-if="change.type === 'added'" />
                <Minus v-else-if="change.type === 'removed'" />
                <EditPen v-else />
              </el-icon>
              {{ changeLabels[change.type] }}
            </span>
            <div class="change-heading">
              <strong>{{ change.title }}</strong>
              <span>{{ changeSummary(change) }}</span>
            </div>
            <el-tooltip :content="isExpanded(change.key) ? '收起详情' : '展开详情'" placement="top">
              <button
                type="button"
                class="change-toggle"
                :aria-label="`${isExpanded(change.key) ? '收起' : '展开'}${change.title}的变更详情`"
                :aria-expanded="isExpanded(change.key)"
                @click="toggleChange(change.key)"
              >
                <el-icon><ArrowUp v-if="isExpanded(change.key)" /><ArrowDown v-else /></el-icon>
              </button>
            </el-tooltip>
          </header>

          <div v-show="isExpanded(change.key)" class="month-diff-item__body">
            <div v-if="change.type === 'modified'" class="field-change-list">
              <div v-for="field in change.fields" :key="field.key" class="split-diff-row">
                <span class="field-change-label">{{ field.label }}</span>
                <div class="split-diff-cell split-diff-cell--before">
                  <span class="split-diff-mobile-label"><el-icon><Minus /></el-icon>{{ beforeLabel }}</span>
                  <p>{{ field.before }}</p>
                </div>
                <div class="split-diff-cell split-diff-cell--after">
                  <span class="split-diff-mobile-label"><el-icon><Plus /></el-icon>{{ afterLabel }}</span>
                  <p>{{ field.after }}</p>
                </div>
              </div>
            </div>

            <div v-else class="task-change-details">
              <div
                v-for="entry in taskDetailEntries(change.after ?? change.before)"
                :key="entry.label"
                class="split-diff-row"
              >
                <span class="field-change-label">{{ entry.label }}</span>
                <template v-if="change.type === 'added'">
                  <div class="split-diff-cell split-diff-cell--missing">
                    <span class="split-diff-empty">此版本无此内容</span>
                  </div>
                  <div class="split-diff-cell split-diff-cell--after">
                    <span class="split-diff-mobile-label"><el-icon><Plus /></el-icon>{{ afterLabel }}</span>
                    <p>{{ entry.value }}</p>
                  </div>
                </template>
                <template v-else>
                  <div class="split-diff-cell split-diff-cell--before">
                    <span class="split-diff-mobile-label"><el-icon><Minus /></el-icon>{{ beforeLabel }}</span>
                    <p>{{ entry.value }}</p>
                  </div>
                  <div class="split-diff-cell split-diff-cell--missing">
                    <span class="split-diff-empty">此版本无此内容</span>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </article>

        <el-empty
          v-if="visibleChanges.length === 0"
          description="该类型暂无变化"
          :image-size="48"
        />
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, EditPen, Minus, Plus } from '@element-plus/icons-vue'
import type {
  MonthPlanDiff,
  MonthPlanItemChange,
  NormalizedMonthPlanItem,
} from './planDraftVersionComparison'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'
// ⚠️ **纯逻辑已抽到 `@/utils/monthPlanVersionDiff`**（CI-5 阶段 2 第五轮）⇒ 这里只做转发。
import {
  areAllVisibleMonthPlanDiffExpanded,
  buildDefaultExpandedMonthPlanDiffKeys,
  buildMonthPlanDiffFilterOptions,
  buildMonthPlanDiffTaskDetailEntries,
  isMonthPlanDiffExpanded,
  MONTH_PLAN_DIFF_CHANGE_LABELS,
  resolveMonthPlanDiffChangeSummary,
  selectVisibleMonthPlanDiffChanges,
  toggleAllVisibleMonthPlanDiffChanges,
  toggleMonthPlanDiffChange,
} from '@/utils/monthPlanVersionDiff'

type DiffFilter = 'ALL' | MonthPlanItemChange['type']

const props = withDefaults(defineProps<{
  diff: MonthPlanDiff
  heading?: string
  beforeLabel?: string
  afterLabel?: string
  emptyText?: string
}>(), {
  heading: '指定版本到当前版本',
  beforeLabel: '历史版本',
  afterLabel: '当前版本',
  emptyText: '当前计划与所选版本一致',
})

const activeFilter = ref<DiffFilter>('ALL')
const expandedKeys = ref(new Set<string>())
// ⚠️ **纯逻辑已抽到 `@/utils/monthPlanVersionDiff`**（CI-5 阶段 2 第五轮）⇒ 这里只做转发。
const changeLabels = MONTH_PLAN_DIFF_CHANGE_LABELS
const filterOptions = computed(() => buildMonthPlanDiffFilterOptions(props.diff))
const visibleChanges = computed(() => selectVisibleMonthPlanDiffChanges(props.diff.items, activeFilter.value))
const allVisibleExpanded = computed(() => areAllVisibleMonthPlanDiffExpanded(visibleChanges.value, expandedKeys.value))

watch(() => props.diff, (diff) => {
  activeFilter.value = 'ALL'
  expandedKeys.value = buildDefaultExpandedMonthPlanDiffKeys(diff)
}, { immediate: true })

function isExpanded(key: string) {
  return isMonthPlanDiffExpanded(expandedKeys.value, key)
}

function toggleChange(key: string) {
  expandedKeys.value = toggleMonthPlanDiffChange(expandedKeys.value, key)
}

function toggleAllVisible() {
  expandedKeys.value = toggleAllVisibleMonthPlanDiffChanges(visibleChanges.value, expandedKeys.value)
}

function changeSummary(change: MonthPlanItemChange) {
  return resolveMonthPlanDiffChangeSummary(change, {
    afterLabel: props.afterLabel,
    beforeLabel: props.beforeLabel,
  })
}

function taskDetailEntries(item?: NormalizedMonthPlanItem | null) {
  return buildMonthPlanDiffTaskDetailEntries(item, {
    formatStandard: formatMonthPlanStandard,
    formatPeriod: formatMonthPlanPeriod,
  })
}
</script>

<style scoped>
.month-diff-view { display:grid; align-content:start; color:#263632; }
.diff-overview { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:15px 16px; border:1px solid #dde5e2; border-bottom:0; border-radius:6px 6px 0 0; background:#f7f9f8; }
.diff-overview__copy { display:grid; gap:3px; min-width:0; }
.diff-overview__copy strong { color:#263632; font-size:15px; line-height:1.4; }
.diff-overview__copy span { color:#687873; font-size:12px; line-height:1.5; }
.diff-counts { display:flex; align-items:stretch; gap:1px; margin:0; overflow:hidden; border:1px solid #dde5e2; border-radius:5px; background:#dde5e2; }
.diff-count { display:flex; align-items:center; gap:9px; min-width:76px; padding:7px 10px; background:#fff; }
.diff-count dt { font-size:12px; font-weight:650; }
.diff-count dd { margin:0; font:700 16px "Cascadia Mono",monospace; }
.diff-count--modified dt,.diff-count--modified dd { color:#a96812; }
.diff-count--added dt,.diff-count--added dd { color:#237a57; }
.diff-count--removed dt,.diff-count--removed dd { color:#b54747; }
.diff-toolbar { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 12px; border-inline:1px solid #dde5e2; background:#fff; }
.diff-filter-scroll { min-width:0; overflow-x:auto; scrollbar-width:thin; }
.diff-filter-scroll :deep(.el-segmented) { min-width:max-content; background:#f1f4f3; }
.diff-toolbar .el-button { flex:0 0 auto; }
.split-diff-header,.split-diff-row { display:grid; grid-template-columns:112px minmax(0,1fr) minmax(0,1fr); }
.split-diff-header { min-height:42px; border:1px solid #d8e1de; border-bottom:0; background:#f1f4f3; color:#52645f; font-size:12px; font-weight:700; }
.split-diff-header > span { display:flex; align-items:center; gap:6px; min-width:0; padding:9px 12px; }
.split-diff-header > span + span { border-left:1px solid #d8e1de; }
.split-diff-column--before { color:#a53c3c; }
.split-diff-column--after { color:#1f704f; }
.month-diff-list { overflow:hidden; border:1px solid #dde5e2; border-radius:0 0 6px 6px; background:#fff; }
.month-diff-item { position:relative; border-top:1px solid #dfe7e4; background:#fff; }
.month-diff-item:first-child { border-top:0; }
.month-diff-item::before { position:absolute; inset:0 auto 0 0; width:3px; content:""; }
.month-diff-item--modified::before { background:#a96812; }
.month-diff-item--added::before { background:#237a57; }
.month-diff-item--removed::before { background:#b54747; }
.month-diff-item__header { display:grid; grid-template-columns:82px minmax(0,1fr) 34px; align-items:center; gap:12px; min-height:62px; padding:10px 12px 10px 15px; }
.change-kind { display:inline-flex; align-items:center; gap:5px; width:max-content; font-size:12px; font-weight:700; }
.change-kind--modified { color:#a96812; }.change-kind--added { color:#237a57; }.change-kind--removed { color:#b54747; }
.change-heading { display:grid; gap:3px; min-width:0; }
.month-diff-item__header strong { overflow-wrap:anywhere; color:#263632; font-size:14px; line-height:1.45; }
.change-heading span { overflow:hidden; color:#687873; font-size:12px; line-height:1.45; text-overflow:ellipsis; white-space:nowrap; }
.change-toggle { display:grid; width:32px; height:32px; padding:0; place-items:center; border:0; border-radius:4px; background:transparent; color:#52645f; cursor:pointer; }
.change-toggle:hover { background:#eef2f0; color:#263632; }
.change-toggle:focus-visible { outline:2px solid #237a57; outline-offset:2px; }
.month-diff-item__body { border-top:1px solid #dfe7e4; }
.split-diff-row { min-width:0; }
.split-diff-row + .split-diff-row { border-top:1px solid #dfe7e4; }
.field-change-label { display:flex; align-items:flex-start; min-width:0; padding:13px 12px 13px 16px; background:#f7f9f8; color:#52645f; font-size:12px; font-weight:700; line-height:1.5; }
.split-diff-cell { min-width:0; min-height:48px; padding:12px 14px; border-left:1px solid #d8e1de; }
.split-diff-cell--before { background:#fff1f1; }
.split-diff-cell--after { background:#eef8f3; }
.split-diff-cell--missing { display:grid; place-items:center; background:#fafbfb; color:#8a9793; }
.split-diff-empty { font-size:12px; line-height:1.5; }
.split-diff-mobile-label { display:none; align-items:center; gap:5px; margin-bottom:5px; font-size:12px; font-weight:700; }
.split-diff-cell--before .split-diff-mobile-label { color:#a53c3c; }
.split-diff-cell--after .split-diff-mobile-label { color:#1f704f; }
.split-diff-cell p { margin:0; overflow-wrap:anywhere; color:#263632; font-size:14px; line-height:1.55; white-space:pre-wrap; }
.month-diff-view :deep(.el-empty) { padding:36px 0 18px; }
@media (max-width:760px) {
  .diff-overview { align-items:flex-start; flex-direction:column; padding:13px; }
  .diff-counts { width:100%; }
  .diff-count { flex:1 1 0; min-width:0; justify-content:center; padding-inline:6px; }
  .diff-toolbar { align-items:flex-start; }
  .month-diff-item__header { grid-template-columns:74px minmax(0,1fr) 32px; gap:8px; padding-left:12px; }
  .change-heading span { white-space:normal; }
  .split-diff-header { display:none; }
  .month-diff-list { border-top:1px solid #dde5e2; }
  .split-diff-row { grid-template-columns:1fr; }
  .field-change-label { padding:9px 12px; border-bottom:1px solid #dfe7e4; }
  .split-diff-cell { min-height:0; padding:10px 12px 11px; border-left:0; }
  .split-diff-cell + .split-diff-cell { border-top:1px solid #d8e1de; }
  .split-diff-cell--missing { min-height:48px; place-items:start; }
  .split-diff-mobile-label { display:flex; }
}
</style>
