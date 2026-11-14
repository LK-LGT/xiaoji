<template>
  <section class="month-result-print-sheet" aria-hidden="true">
    <h1>{{ performancePrintTitle(archive.planMonth) }}</h1>
    <div class="performance-print-meta">
      <span>姓名：<strong>{{ archive.employeeName || '' }}</strong></span>
      <span>项目：<strong>{{ archive.projectName || '' }}</strong></span>
      <span>岗位：<strong>{{ archive.positionName || '' }}</strong></span>
    </div>
    <table class="performance-print-table">
      <thead>
        <tr>
          <th class="print-seq-column">序号</th>
          <th class="print-work-column">工作计划内容</th>
          <th class="print-weight-column">权重<br>（%）</th>
          <th class="print-standard-column">考核标准（交付物、质量）</th>
          <th class="print-date-column">完成时间段<br>（从开始到结束时间）</th>
          <th class="print-completion-column">完成情况（完成度详细说明）</th>
          <th class="print-remark-column">备注（其他情况说明）</th>
          <th class="print-value-column">预估价值（对于每一条工作预估价值）</th>
          <th class="print-ratio-column">占比完成度</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="task in archive.tasks" :key="task.key">
          <td class="print-center print-seq-column">{{ task.sortNo }}</td>
          <td>
            <MonthPlanTaskContentView :content="task.taskContent" :fallback="task.taskName" />
          </td>
          <td class="print-center print-weight-column">{{ numberText(task.performanceWeight) }}%</td>
          <td class="print-standard-cell">
            <template v-if="hasPrintText(task.deliverable) || hasPrintText(task.quality)">
              <div v-if="hasPrintText(task.deliverable)" class="print-standard-line">
                <strong>交付物：</strong><span>{{ printFieldText(task.deliverable) }}</span>
              </div>
              <div v-if="hasPrintText(task.quality)" class="print-standard-line">
                <strong>质量：</strong><span>{{ printFieldText(task.quality) }}</span>
              </div>
            </template>
            <template v-else>&nbsp;</template>
          </td>
          <td class="print-center print-date-column">{{ periodText(task) }}</td>
          <td>{{ completionStatusText(task) }}</td>
          <td>{{ remarkText(task) }}</td>
          <td class="print-center print-value-column">{{ moneyText(task.estimatedValue) }}</td>
          <td class="print-center print-ratio-column">{{ completionRatioText(task) }}</td>
        </tr>
      </tbody>
      <tfoot>
        <tr>
          <td colspan="7">预估价值合计</td>
          <td class="print-center print-value-column">{{ moneyText(estimatedValueTotal) }}</td>
          <td class="print-center print-ratio-column">/</td>
        </tr>
        <tr>
          <td colspan="7">个人本月发放绩效合计</td>
          <td class="print-center print-value-column">/</td>
          <td class="print-center print-ratio-column">{{ completionRatioTotalText }}</td>
        </tr>
      </tfoot>
    </table>
    <div class="performance-print-confirmation">
      <p>本人核对表格内月度工作计划目标及月底执行情况，确认信息真实完整无误，知晓并认可表格兼具目标确定与绩效考核用途，自愿接受绩效评估结果，承担计划未达预期的相应绩效后果，对以上内容无异议。</p>
      <div class="signature-row"><span class="signature-label">员工本人签字捺印：</span><span class="signature-line"></span><span class="signature-date">时间：____年__月__日</span></div>
      <div class="signature-row"><span class="signature-label">项目经理签字：</span><span class="signature-line"></span><span class="signature-date">时间：____年__月__日</span></div>
      <div class="signature-row"><span class="signature-label">部门负责人签字：</span><span class="signature-line"></span><span class="signature-date">时间：____年__月__日</span></div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { EmployeeResultDetailResp } from '@/api/employee'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { formatMonthPlanPeriod } from '@/utils/monthPlanPeriod'
import type { MonthResultArchive, MonthResultArchiveTask } from '../resultArchiveModel'

const RESULT_COMPLETION_RATE = 100

const props = defineProps<{
  archive: MonthResultArchive
  details: Record<number, EmployeeResultDetailResp>
  failedTaskIds: number[]
  printedAt: string
}>()

const estimatedValueTotal = computed(() => props.archive.tasks.reduce((sum, task) => sum + Number(task.estimatedValue || 0), 0))
const completionRatioTotalText = computed(() => ratioText(props.archive.tasks.reduce((sum, task) => sum + completionRatio(task), 0)))

function performancePrintTitle(value: string) {
  const match = /^(\d{4})-(\d{1,2})/.exec(value || '')
  return match ? `${match[1]} 年 ${Number(match[2])} 月绩效考核表` : `${value}绩效考核表`
}
function numberText(value?: number) { return Number(value || 0).toFixed(Number(value || 0) % 1 === 0 ? 0 : 2) }
function moneyText(value?: number) {
  const numericValue = Number(value || 0)
  return Number.isFinite(numericValue) ? numericValue.toLocaleString('zh-CN', { maximumFractionDigits: 2, useGrouping: false }) : ''
}
function periodText(task: MonthResultArchiveTask) { return formatMonthPlanPeriod(task.estimatedStartDate, task.estimatedEndDate, '') }
function hasPrintText(value?: string | null) { return String(value ?? '').trim().length > 0 }
function printFieldText(value?: string | null) { return String(value ?? '').trim() }
function completionStatusText(task: MonthResultArchiveTask) {
  return props.details[task.id]?.completionStatus || task.latestVersion?.completionStatus || ''
}
function remarkText(task: MonthResultArchiveTask) {
  return props.details[task.id]?.remark || task.latestVersion?.remark || ''
}
function completionRatio(task: MonthResultArchiveTask) {
  const weight = Number(task.performanceWeight || 0)
  const rate = Number(task.latestVersion?.completionRate ?? RESULT_COMPLETION_RATE)
  if (!Number.isFinite(weight) || !Number.isFinite(rate)) return 0
  return weight * rate / 10000
}
function ratioText(value: number) {
  if (!Number.isFinite(value)) return ''
  return value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}
function completionRatioText(task: MonthResultArchiveTask) { return ratioText(completionRatio(task)) }
</script>

<style scoped>
.month-result-print-sheet { display:none; }
@media print {
  @page { size:A4 landscape; margin:10mm 8mm; }
  :global(html),
  :global(body),
  :global(#app),
  :global(.app-shell),
  :global(.main) { display:block!important; width:100%!important; height:auto!important; min-height:0!important; margin:0!important; padding:0!important; overflow:visible!important; background:#fff!important; }
  :global(.sidebar),:global(.topbar) { display:none!important; }
  :global(body *) { visibility:hidden!important; }
  :global(.month-archive-detail) { min-height:0!important; margin:0!important; padding:0!important; border:0!important; background:#fff!important; box-shadow:none!important; }
  :global(.month-archive-detail > :not(.month-result-print-sheet)) { display:none!important; }
  .month-result-print-sheet,.month-result-print-sheet * { visibility:visible!important; }
  .month-result-print-sheet { position:static; display:block; width:100%; margin:0; padding:0; color:#000; background:#fff; font-family:"SimSun","Microsoft YaHei",sans-serif; break-after:avoid; page-break-after:avoid; }
  .month-result-print-sheet h1 { width:max-content; max-width:100%; margin:0 auto 4px; padding:0 18px 1px; border-bottom:1px solid #000; font-size:18px; font-weight:700; line-height:1.25; text-align:center; }
  .performance-print-meta { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin:0 0 4px; font-size:10px; line-height:1.35; }
  .performance-print-meta span { display:flex; align-items:flex-end; gap:4px; min-width:0; }
  .performance-print-meta span:nth-child(2) { justify-content:center; text-align:center; }
  .performance-print-meta span:last-child { justify-content:flex-end; text-align:right; }
  .performance-print-meta strong { display:inline-block; min-width:76px; padding:0 8px 1px; border-bottom:1px solid #000; font-weight:400; line-height:1.1; text-align:center; }
  .performance-print-table { width:100%; border-collapse:collapse; table-layout:fixed; color:#000; font-size:8.5px; line-height:1.32; break-after:avoid; page-break-after:avoid; }
  .performance-print-table th,.performance-print-table td { padding:3px; border:1px solid #000; overflow-wrap:anywhere; vertical-align:middle; }
  .performance-print-table th { background:#fff; font-weight:700; text-align:center; }
  .performance-print-table td:nth-child(5) { white-space:nowrap; }
  .performance-print-table tbody tr { break-inside:avoid; page-break-inside:avoid; }
  .performance-print-table thead { display:table-header-group; }
  .performance-print-table tfoot { display:table-row-group; }
  .performance-print-table tfoot td { padding:2px 3px; font-weight:700; text-align:center; }
  .performance-print-table .print-seq-column { width:4%; white-space:nowrap; }
  .performance-print-table .print-work-column { width:17%; }
  .performance-print-table .print-weight-column { width:5%; white-space:nowrap; }
  .performance-print-table .print-standard-column { width:22%; }
  .performance-print-table .print-standard-cell { line-height:1.28; }
  .performance-print-table .print-standard-line { display:block; }
  .performance-print-table .print-standard-line + .print-standard-line { margin-top:1px; }
  .performance-print-table .print-standard-line strong { font-weight:700; }
  .performance-print-table .print-date-column { width:10%; white-space:normal; }
  .performance-print-table .print-completion-column { width:21%; }
  .performance-print-table .print-remark-column { width:6%; }
  .performance-print-table .print-value-column { width:8%; }
  .performance-print-table .print-ratio-column { width:7%; }
  .performance-print-table .print-center { text-align:center; }
  .performance-print-confirmation { margin-top:8px; font-size:10px; line-height:1.7; }
  .performance-print-confirmation p { margin:0 0 10px; font-size:10px; line-height:1.65; text-align:left; }
  .performance-print-confirmation .signature-row { display:grid; grid-template-columns:max-content 34mm 1fr; align-items:end; column-gap:4px; margin-top:8px; }
  .performance-print-confirmation .signature-line { display:block; height:1.2em; border-bottom:1px solid #000; }
  .performance-print-confirmation .signature-date { justify-self:center; min-width:42mm; text-align:left; }
}
</style>
