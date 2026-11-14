<template>
  <section class="page-panel employee-result-submit">
    <header v-if="isLeaderWorkspace" class="result-page-header">
      <div>
        <h1 class="page-title">{{ activePlanType === 'WEEK' ? '周计划成果提交' : '月计划成果提交' }}</h1>
        <p class="page-subtitle">
          {{ activePlanType === 'WEEK' ? '对照已审批周计划填写完成情况、交付物和备注。' : '进入页面即可填写月度绩效表，计划字段只读，成果字段直接在表格内编辑。' }}
        </p>
      </div>
      <div class="result-header-actions">
        <el-radio-group :model-value="activePlanType" class="result-plan-type-switch" aria-label="成果计划类型" @change="changeResultPlanType">
          <el-radio-button value="MONTH">月计划成果</el-radio-button>
          <el-radio-button value="WEEK">周计划成果</el-radio-button>
        </el-radio-group>
        <el-tooltip content="刷新数据" placement="bottom">
          <el-button v-if="activePlanType === 'MONTH'" circle :icon="Refresh" :loading="loading" :disabled="submitting" aria-label="刷新数据" @click="loadOptions" />
        </el-tooltip>
      </div>
    </header>

    <el-alert v-if="errorMessage" class="dashboard-alert" type="warning" :closable="false" show-icon :title="errorMessage" />
    <el-skeleton v-if="loading" class="result-loading" :rows="10" animated />

    <section v-else-if="activePlanType === 'MONTH' && !errorMessage" class="result-month-board">
      <div class="month-board-toolbar">
        <div>
          <strong>月度绩效表</strong>
          <span>共 {{ targetMonthGroups.length }} 个月份，点击月份展开填写成果</span>
        </div>
        <div class="toolbar-actions">
          <template v-if="!isLeaderWorkspace">
            <el-radio-group :model-value="activePlanType" class="result-plan-type-switch" aria-label="成果计划类型" @change="changeResultPlanType">
              <el-radio-button value="MONTH">月计划成果</el-radio-button>
              <el-radio-button value="WEEK">周计划成果</el-radio-button>
            </el-radio-group>
            <el-tooltip content="刷新数据" placement="bottom">
              <el-button circle :icon="Refresh" :loading="loading" :disabled="submitting" aria-label="刷新数据" @click="loadOptions" />
            </el-tooltip>
          </template>
          <el-button link :disabled="allTargetMonthsExpanded" @click="expandAllTargetMonths">全部展开</el-button>
          <el-button link :disabled="allTargetMonthsCollapsed" @click="collapseAllTargetMonths">全部收起</el-button>
        </div>
      </div>

      <div v-if="targetMonthGroups.length" class="target-month-list">
        <article v-for="group in pagedTargetMonthGroups" :key="group.key" class="target-month-group">
          <div class="target-month-header">
            <button
              type="button"
              class="target-month-toggle"
              :aria-expanded="expandedTargetMonthKeys.has(group.key)"
              :aria-controls="`result-submit-month-${group.key}`"
              @click="toggleTargetMonth(group.key)"
            >
              <el-icon><ArrowDown v-if="expandedTargetMonthKeys.has(group.key)" /><ArrowRight v-else /></el-icon>
              <strong>{{ group.label }}</strong>
              <span>{{ group.rows.length }} 项工作计划</span>
            </button>
            <div class="target-month-actions">
              <el-button :icon="Printer" @click="printPerformance(group)">打印绩效</el-button>
            </div>
          </div>

          <div
            :id="`result-submit-month-${group.key}`"
            v-show="expandedTargetMonthKeys.has(group.key)"
            class="target-month-details"
          >
            <div class="performance-table-wrap">
              <table class="result-edit-table" aria-label="月计划成果编辑表">
                <thead>
                  <tr>
                    <th class="edit-seq">序号</th>
                    <th class="edit-work">工作计划内容</th>
                    <th class="edit-weight">权重</th>
                    <th class="edit-standard">考核标准</th>
                    <th class="edit-date">完成时间</th>
                    <th class="edit-completion">完成情况</th>
                    <th class="edit-remark">备注</th>
                    <th class="edit-value">预估价值</th>
                    <th class="edit-performance">个人整体绩效</th>
                    <th class="edit-evidence">成果提交（选填）</th>
                    <th class="edit-action">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(row, index) in group.rows"
                    :key="row.key"
                    :class="{ 'is-active': activeRow?.key === row.key, 'is-readonly': rowLocked(row) }"
                    @click="setActiveRow(row)"
                  >
                    <td class="cell-center">{{ row.sortNo || index + 1 }}</td>
                    <td>
                      <MonthPlanTaskContentView :content="row.taskContent" :fallback="row.taskName" />
                    </td>
                    <td class="cell-center">{{ numberText(row.performanceWeight) }}%</td>
                    <td><div class="standard-cell">{{ formatMonthPlanStandard(row.deliverable, row.quality) }}</div></td>
                    <td class="cell-center">{{ dateRangeText(row.estimatedStartDate, row.estimatedEndDate) }}</td>
                    <td class="sheet-edit-cell">
                      <el-input v-model="row.completionStatus" type="textarea" :rows="3" maxlength="1000" resize="none" placeholder="填写实际完成情况" :disabled="rowLocked(row)" @input="invalidateReview(row)" />
                    </td>
                    <td class="sheet-edit-cell">
                      <el-input v-model="row.remark" type="textarea" :rows="3" maxlength="2000" resize="none" placeholder="填写完成度说明" :disabled="rowLocked(row)" @input="invalidateReview(row)" />
                    </td>
                    <td class="cell-number">{{ moneyText(row.estimatedValue) }}</td>
                    <td class="sheet-edit-cell sheet-edit-cell--number">
                      <el-input-number v-model="row.personalPerformance" :min="0" :max="9999999999.99" :precision="2" :controls="false" placeholder="本人填写" :disabled="rowLocked(row)" @change="invalidateReview(row)" />
                    </td>
                    <td class="cell-center">
                      <label v-if="!rowLocked(row)" class="file-picker" :for="`result-file-table-${row.id}`"><el-icon><Paperclip /></el-icon>{{ row.file ? '已选择' : '选择文件' }}</label>
                      <span v-else-if="row.hasEvidence" class="evidence-state is-uploaded">已上传</span>
                      <span v-else class="muted-text">未上传</span>
                      <input :id="`result-file-table-${row.id}`" class="visually-hidden" type="file" :accept="uploadAccept" :disabled="rowLocked(row)" @change="handleNativeFileChange(row, $event)" />
                      <small v-if="row.file" class="selected-file-name">{{ row.file.name }}</small>
                    </td>
                    <td class="cell-center">
                      <el-tag :type="versionStatusMeta(row.status).type" effect="plain">{{ versionStatusMeta(row.status).label }}</el-tag>
                    </td>
                  </tr>
                </tbody>
                <tfoot>
                  <tr>
                    <td colspan="7">合计</td>
                    <td class="cell-number">{{ moneyText(group.rows.reduce((sum, row) => sum + Number(row.estimatedValue || 0), 0)) }}</td>
                    <td class="cell-number">{{ moneyText(group.rows.reduce((sum, row) => sum + Number(row.personalPerformance || 0), 0)) }}</td>
                    <td></td>
                    <td></td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>
        </article>

        <div v-if="targetMonthGroups.length > targetMonthPageSize" class="target-month-pagination">
          <el-pagination
            v-model:current-page="targetMonthPage"
            :page-size="targetMonthPageSize"
            layout="prev, pager, next, total"
            :total="targetMonthGroups.length"
          />
        </div>
      </div>

      <div v-else class="result-target-empty">
        <el-icon><Check /></el-icon>
        <strong>暂无可填写成果的月计划</strong>
        <span>月计划审批通过后，会在这里形成月度绩效表。</span>
      </div>
    </section>

    <section v-else-if="activePlanType === 'WEEK'" class="week-result-stage">
      <div v-if="!isLeaderWorkspace" class="result-inline-toolbar">
        <el-radio-group :model-value="activePlanType" class="result-plan-type-switch" aria-label="成果计划类型" @change="changeResultPlanType">
          <el-radio-button value="MONTH">月计划成果</el-radio-button>
          <el-radio-button value="WEEK">周计划成果</el-radio-button>
        </el-radio-group>
      </div>
      <EmployeeWeekResultWorkspace />
    </section>

    <aside v-if="activePlanType === 'MONTH' && editableRows.length" class="ai-check-dock" tabindex="0" aria-label="AI检查面板">
      <div class="ai-dock-handle"><el-icon><MagicStick /></el-icon><span>AI检查</span></div>
      <div class="ai-dock-content">
        <div class="ai-dock-title">
          <div><strong>AI成果检查</strong><span>{{ activeRow ? `当前：${activeRow.taskName}` : '请选择一项任务' }}</span></div>
        </div>
        <AiReviewPanel
          class="result-ai-panel"
          :review="activeRow?.aiReview || null"
          :stale="Boolean(activeRow?.aiReviewStale)"
          :show-completion-range="false"
          title="成果检查报告"
          empty-text="填写完成情况后，可先检查再提交当前成果。附件为选填项。"
        >
          <template #actions>
            <el-button v-if="aiChecking" type="danger" plain @click="stopAiCheck">停止生成</el-button>
            <el-button v-else type="primary" plain :disabled="!activeRow || rowLocked(activeRow)" @click="checkActiveRow">立即检查</el-button>
          </template>
        </AiReviewPanel>
        <button class="ai-submit-command" type="button" :disabled="!activeRow || rowLocked(activeRow) || submitting" @click="activeRow && requestSubmitRow(activeRow)">
          <span>AI检查并提交</span>
          <small>仅提交当前成果</small>
        </button>
      </div>
    </aside>

    <section v-if="activePlanType === 'MONTH' && printGroup" class="performance-print-sheet" aria-hidden="true">
      <h1>{{ monthPerformancePrintTitle(printGroup.label) }}</h1>
      <div class="performance-print-meta">
        <span>姓名：<strong>{{ printGroup.rows[0]?.employeeName || '' }}</strong></span>
        <span>项目：<strong>{{ printGroup.rows[0]?.projectName || '' }}</strong></span>
        <span>岗位：<strong>{{ printGroup.rows[0]?.positionName || '' }}</strong></span>
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
          <tr v-for="target in printGroup.rows" :key="target.key">
            <td class="print-center print-seq-column">{{ target.sortNo }}</td>
            <td>
              <MonthPlanTaskContentView :content="target.taskContent" :fallback="target.taskName" />
            </td>
            <td class="print-center print-weight-column">{{ numberText(target.performanceWeight) }}%</td>
            <td class="print-standard-cell">
              <template v-if="hasPrintText(target.deliverable) || hasPrintText(target.quality)">
                <div v-if="hasPrintText(target.deliverable)" class="print-standard-line">
                  <strong>交付物：</strong><span>{{ printFieldText(target.deliverable) }}</span>
                </div>
                <div v-if="hasPrintText(target.quality)" class="print-standard-line">
                  <strong>质量：</strong><span>{{ printFieldText(target.quality) }}</span>
                </div>
              </template>
              <template v-else>&nbsp;</template>
            </td>
            <td class="print-center print-date-column">{{ printDateRangeText(target.estimatedStartDate, target.estimatedEndDate) }}</td>
            <td>{{ target.latestVersion?.completionStatus || '' }}</td>
            <td>{{ target.latestVersion?.remark || '' }}</td>
            <td class="print-center print-value-column">{{ printMoneyText(target.estimatedValue) }}</td>
            <td class="print-center print-ratio-column">{{ printCompletionRatioText(target) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="7">预估价值合计</td>
            <td class="print-center print-value-column">{{ printMoneyText(totalEstimatedValue(printGroup.rows)) }}</td>
            <td class="print-center print-ratio-column">/</td>
          </tr>
          <tr>
            <td colspan="7">个人本月发放绩效合计</td>
            <td class="print-center print-value-column">/</td>
            <td class="print-center print-ratio-column">{{ printCompletionRatioTotalText(printGroup.rows) }}</td>
          </tr>
          <tr class="print-confirm-block-row">
            <td colspan="9">
              <div class="print-confirm-block">
                <p>本人核对表格内月度工作计划目标及月底执行情况，确认信息真实完整无误，知晓并认可表格兼具目标确定与绩效考核用途，自愿接受绩效评估结果，承担计划未达预期的相应绩效后果，对以上内容无异议。</p>
                <div class="signature-row-content"><span class="signature-label">员工本人签字捺印：</span><span class="signature-line"></span><span class="signature-date">时间：____年__月__日</span></div>
                <div class="signature-row-content"><span class="signature-label">项目经理签字：</span><span class="signature-line"></span><span class="signature-date">时间：____年__月__日</span></div>
                <div class="signature-row-content"><span class="signature-label">部门负责人签字：</span><span class="signature-line"></span><span class="signature-date">时间：____年__月__日</span></div>
              </div>
            </td>
          </tr>
        </tfoot>
      </table>
    </section>

    <AiReviewConfirmDialog
      v-if="activePlanType === 'MONTH'"
      v-model="aiDialogVisible"
      :review="activeRow?.aiReview || null"
      :confirming="confirmingSubmit"
      :show-completion-range="false"
      @confirm="confirmSubmitResult"
    />
  </section>
</template>

<script setup lang="ts">
import { ArrowDown, ArrowRight, Check, MagicStick, Paperclip, Printer, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getEmployeeResultSubmitOptionsApi,
  submitEmployeeResultApi,
  type EmployeeResultSubmitOptionsResp,
} from '@/api/employee'
import { previewResultAiReviewApi, type AiReview } from '@/api/aiReview'
import AiReviewPanel from '@/components/AiReviewPanel.vue'
import AiReviewConfirmDialog from '@/components/AiReviewConfirmDialog.vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { notifyAiReviewResult } from '@/utils/aiReviewFeedback'
import { useCancelableAiRequest } from '@/composables/useCancelableAiRequest'
import { groupRowsByMonth, paginateMonthGroups } from '@/utils/monthGroups'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'
import EmployeeWeekResultWorkspace from './components/EmployeeWeekResultWorkspace.vue'

const RESULT_COMPLETION_RATE = 100
type ResultVersion = EmployeeResultSubmitOptionsResp['resultVersions'][number]
type MonthPlanItem = EmployeeResultSubmitOptionsResp['monthPlanItemOptions'][number]

interface PerformancePreviewRow extends MonthPlanItem {
  key: string
  planMonth: string
  planTitle: string
  employeeName: string
  projectName: string
  positionName: string
  latestVersion?: ResultVersion
  status: ResultVersion['status'] | 'not_submitted'
}

interface EditableResultRow extends PerformancePreviewRow {
  completionStatus: string
  remark: string
  personalPerformance: number | undefined
  hasEvidence: boolean
  file: File | null
  aiReview: AiReview | null
  aiReviewStale: boolean
}

type ResultPlanType = 'MONTH' | 'WEEK'
type TargetMonthGroup = { key: string; label: string; rows: EditableResultRow[] }

const route = useRoute()
const router = useRouter()
const isLeaderWorkspace = computed(() => route.path.startsWith('/leader/'))
const resultSubmitPath = computed(() => isLeaderWorkspace.value ? '/leader/results/submit' : '/employee/results/submit')
const activePlanType = computed<ResultPlanType>(() => String(route.query.planType || '').toUpperCase() === 'WEEK' ? 'WEEK' : 'MONTH')

const loading = ref(false)
const submitting = ref(false)
const confirmingSubmit = ref(false)
const aiDialogVisible = ref(false)
const errorMessage = ref('')
const targetMonthPage = ref(1)
const targetMonthPageSize = 10
const expandedTargetMonthKeys = reactive(new Set<string>())
const editableRows = ref<EditableResultRow[]>([])
const activeRowKey = ref('')
const printGroup = ref<TargetMonthGroup | null>(null)
const { running: aiChecking, run: runAiCheck, stop: stopAiCheckRequest } = useCancelableAiRequest()

const options = reactive<EmployeeResultSubmitOptionsResp>({
  monthPlanOptions: [], monthPlanItemOptions: [], acceptedFileTypes: ['pdf', 'doc', 'docx', 'zip', 'png', 'jpg', 'jpeg', 'webp'], maxFileSizeMb: 20, resultVersions: [],
})
let loadOptionsRequestId = 0

const activeRow = computed(() => editableRows.value.find((row) => row.key === activeRowKey.value) || editableRows.value[0] || null)
const uploadAccept = computed(() => options.acceptedFileTypes.map((type) => `.${type.toLowerCase()}`).join(','))

const previewRows = computed<PerformancePreviewRow[]>(() => {
  const planMap = new Map(options.monthPlanOptions.map((plan) => [plan.id, plan]))
  return options.monthPlanItemOptions.flatMap((item) => {
    const plan = planMap.get(item.monthPlanId)
    if (!plan) return []
    const latestVersion = latestResultVersionFor(item.monthPlanId, item.id)
    return [{
      ...item,
      key: `${item.monthPlanId}-${item.id}`,
      planMonth: plan.planMonth,
      planTitle: plan.title,
      employeeName: plan.employeeName,
      projectName: plan.projectName,
      positionName: plan.positionName,
      latestVersion,
      status: latestVersion?.status ?? 'not_submitted',
    }]
  })
})
const allTargetMonthsExpanded = computed(() => targetMonthGroups.value.length > 0 && targetMonthGroups.value.every((group) => expandedTargetMonthKeys.has(group.key)))
const allTargetMonthsCollapsed = computed(() => targetMonthGroups.value.every((group) => !expandedTargetMonthKeys.has(group.key)))

function readInitialMonthPlanId() {
  const rawId = route.query.monthPlanId
  const id = Number(Array.isArray(rawId) ? rawId[0] : rawId)
  return Number.isInteger(id) && id > 0 ? id : null
}

function readInitialMonthPlanItemId() {
  const rawId = route.query.monthPlanItemId
  const id = Number(Array.isArray(rawId) ? rawId[0] : rawId)
  return Number.isInteger(id) && id > 0 ? id : null
}

function latestResultVersionFor(monthPlanId: number, monthPlanItemId: number) {
  return options.resultVersions.find((item) => item.monthPlanId === monthPlanId && item.monthPlanItemId === monthPlanItemId)
}

const targetMonthGroups = computed(() => groupRowsByMonth(editableRows.value, (target) => target.planMonth))
const pagedTargetMonthGroups = computed(() => paginateMonthGroups(targetMonthGroups.value, targetMonthPage.value, targetMonthPageSize))

function rebuildEditableRows() {
  editableRows.value = previewRows.value.map((row) => ({
    ...row,
    completionStatus: row.latestVersion?.completionStatus || row.latestVersion?.description || '',
    remark: row.latestVersion?.remark || '',
    personalPerformance: row.latestVersion?.personalPerformance,
    hasEvidence: Boolean(row.latestVersion?.hasEvidence),
    file: null,
    aiReview: null,
    aiReviewStale: false,
  }))
  const initialMonthPlanId = readInitialMonthPlanId()
  const initialItemId = readInitialMonthPlanItemId()
  const initialRow = editableRows.value.find((row) => initialItemId && row.id === initialItemId)
    || editableRows.value.find((row) => initialMonthPlanId && row.monthPlanId === initialMonthPlanId && !rowLocked(row))
    || editableRows.value.find((row) => initialMonthPlanId && row.monthPlanId === initialMonthPlanId)
    || editableRows.value.find((row) => !rowLocked(row))
    || editableRows.value[0]
  if (initialRow) {
    activeRowKey.value = initialRow.key
  }
}

function resetTargetMonthView() {
  targetMonthPage.value = 1
  expandedTargetMonthKeys.clear()
  const initialMonthPlanId = readInitialMonthPlanId()
  const initialItemId = readInitialMonthPlanItemId()
  if (!initialMonthPlanId && !initialItemId) return

  const focusRow = editableRows.value.find((row) => initialItemId && row.id === initialItemId)
    || editableRows.value.find((row) => initialMonthPlanId && row.monthPlanId === initialMonthPlanId)
  if (!focusRow) return

  const groupIndex = targetMonthGroups.value.findIndex((group) => group.key === focusRow.planMonth)
  if (groupIndex < 0) return
  targetMonthPage.value = Math.floor(groupIndex / targetMonthPageSize) + 1
  expandedTargetMonthKeys.add(focusRow.planMonth)
}

function toggleTargetMonth(key: string) { expandedTargetMonthKeys.has(key) ? expandedTargetMonthKeys.delete(key) : expandedTargetMonthKeys.add(key) }
function expandAllTargetMonths() { targetMonthGroups.value.forEach((group) => expandedTargetMonthKeys.add(group.key)) }
function collapseAllTargetMonths() { expandedTargetMonthKeys.clear() }

function rowLocked(row: EditableResultRow | null) { return !row || row.status === 'submitted' || row.status === 'confirmed' }
function setActiveRow(row: EditableResultRow) { activeRowKey.value = row.key }
function invalidateReview(row: EditableResultRow) { if (row.aiReview) row.aiReviewStale = true }

async function printPerformance(group: TargetMonthGroup) {
  printGroup.value = group
  await nextTick()
  window.print()
}

function changeResultPlanType(value: string | number | boolean | undefined) {
  errorMessage.value = ''
  const planType: ResultPlanType = value === 'WEEK' ? 'WEEK' : 'MONTH'
  void router.push({ path: resultSubmitPath.value, query: planType === 'WEEK' ? { planType } : undefined })
}

function monthLabel(value: string) {
  const match = /^(\d{4})-(\d{1,2})/.exec(value || '')
  return match ? `${match[1]}年${Number(match[2])}月` : value
}
function monthPerformancePrintTitle(label: string) {
  const match = /^(\d{4})年(\d{1,2})月$/.exec(label || '')
  return match ? `${match[1]} 年 ${Number(match[2])} 月绩效考核表` : `${label}绩效考核表`
}
function numberText(value?: number) { return Number(value || 0).toFixed(Number(value || 0) % 1 === 0 ? 0 : 2) }
function moneyText(value?: number) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }
function printMoneyText(value?: number) {
  const numericValue = Number(value || 0)
  return Number.isFinite(numericValue) ? numericValue.toLocaleString('zh-CN', { maximumFractionDigits: 2, useGrouping: false }) : ''
}
function dateRangeText(start?: string, end?: string) { return formatMonthPlanPeriod(start, end) }
function printDateRangeText(start?: string, end?: string) { return formatMonthPlanPeriod(start, end, '') }
function hasPrintText(value?: string | null) { return String(value ?? '').trim().length > 0 }
function printFieldText(value?: string | null) { return String(value ?? '').trim() }
function completionRatio(row: PerformancePreviewRow) {
  const weight = Number(row.performanceWeight || 0)
  const rate = Number(row.latestVersion?.completionRate ?? RESULT_COMPLETION_RATE)
  if (!Number.isFinite(weight) || !Number.isFinite(rate)) return 0
  return weight * rate / 10000
}
function printRatioText(value: number) {
  if (!Number.isFinite(value)) return ''
  return value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}
function printCompletionRatioText(row: PerformancePreviewRow) { return printRatioText(completionRatio(row)) }
function totalEstimatedValue(rows: PerformancePreviewRow[]) { return rows.reduce((sum, row) => sum + Number(row.estimatedValue || 0), 0) }
function printCompletionRatioTotalText(rows: PerformancePreviewRow[]) {
  return printRatioText(rows.reduce((sum, row) => sum + completionRatio(row), 0))
}
function versionStatusMeta(status: string) {
  return ({
    draft: { label: '草稿', type: 'info' as const }, submitted: { label: '待审批', type: 'warning' as const }, confirmed: { label: '已通过', type: 'success' as const }, rejected: { label: '已驳回', type: 'danger' as const }, not_submitted: { label: '未提交', type: 'info' as const },
  }[status] ?? { label: status, type: 'info' as const })
}

function getFileExt(fileName: string) { return fileName.split('.').pop()?.toLowerCase() ?? '' }
function validateFile(file: File) {
  if (!options.acceptedFileTypes.map((type) => type.toLowerCase()).includes(getFileExt(file.name))) {
    ElMessage.warning('文件类型不支持，请上传 PDF、Word、Zip 或图片文件')
    return false
  }
  if (file.size > options.maxFileSizeMb * 1024 * 1024) {
    ElMessage.warning(`文件大小不能超过 ${options.maxFileSizeMb} MB`)
    return false
  }
  return true
}

function handleNativeFileChange(row: EditableResultRow, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] || null
  if (file && !validateFile(file)) {
    input.value = ''
    return
  }
  row.file = file
  setActiveRow(row)
  invalidateReview(row)
}

function validateRow(row: EditableResultRow) {
  if (rowLocked(row)) { ElMessage.warning('该成果已进入审批流程，不能重复提交'); return false }
  if (!row.completionStatus.trim()) { ElMessage.warning('请填写完成情况'); return false }
  if (!row.remark.trim()) { ElMessage.warning('请填写备注'); return false }
  if (row.personalPerformance == null || !Number.isFinite(Number(row.personalPerformance))) { ElMessage.warning('请填写个人整体绩效'); return false }
  return !row.file || validateFile(row.file)
}

function buildResultPayload(row: EditableResultRow, reviewId?: number) {
  const payload = new FormData()
  payload.append('monthPlanId', String(row.monthPlanId))
  payload.append('monthPlanItemId', String(row.id))
  payload.append('completionRate', String(RESULT_COMPLETION_RATE))
  payload.append('completionStatus', row.completionStatus.trim())
  payload.append('remark', row.remark.trim())
  payload.append('personalPerformance', String(row.personalPerformance))
  payload.append('description', row.completionStatus.trim())
  if (row.file) payload.append('file', row.file)
  if (reviewId) payload.append('aiReviewId', String(reviewId))
  return payload
}

async function runReview(row: EditableResultRow) {
  if (!validateRow(row)) return null
  setActiveRow(row)
  errorMessage.value = ''
  const result = await runAiCheck(({ requestId, signal }) => previewResultAiReviewApi(buildResultPayload(row), { requestId, signal }))
  if (!result) return null
  row.aiReview = result
  row.aiReviewStale = false
  notifyAiReviewResult(result, 'AI成果检查已完成')
  return result
}

async function checkActiveRow() {
  if (!activeRow.value || aiChecking.value || submitting.value) return
  try { await runReview(activeRow.value) } catch (error) { errorMessage.value = error instanceof Error ? error.message : '成果AI检查失败，请稍后重试' }
}

async function requestSubmitRow(row: EditableResultRow) {
  if (submitting.value || aiChecking.value || !validateRow(row)) return
  setActiveRow(row)
  submitting.value = true
  errorMessage.value = ''
  try {
    if (!row.aiReview || row.aiReviewStale) {
      row.aiReview = await previewResultAiReviewApi(buildResultPayload(row))
      row.aiReviewStale = false
    }
    aiDialogVisible.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '成果AI检查失败，请稍后重试'
  } finally { submitting.value = false }
}

async function stopAiCheck() {
  const confirmed = await stopAiCheckRequest()
  confirmed ? ElMessage.info('已停止本次生成') : ElMessage.warning('界面已停止等待，服务端取消尚未确认')
}

async function confirmSubmitResult() {
  const row = activeRow.value
  if (!row?.aiReview || confirmingSubmit.value) return
  confirmingSubmit.value = true
  errorMessage.value = ''
  try {
    const result = await submitEmployeeResultApi(buildResultPayload(row, row.aiReview.id))
    aiDialogVisible.value = false
    ElMessage.success(`成果已提交，版本号 ${result.versionNo}`)
    await loadOptions()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '成果提交失败，请稍后重试'
  } finally { confirmingSubmit.value = false }
}

async function loadOptions() {
  const requestId = ++loadOptionsRequestId
  const routeMonthPlanId = readInitialMonthPlanId()
  loading.value = true
  errorMessage.value = ''
  try {
    const data = await getEmployeeResultSubmitOptionsApi()
    if (requestId !== loadOptionsRequestId) return
    Object.assign(options, data)
    if (routeMonthPlanId && !options.monthPlanOptions.some((plan) => plan.id === routeMonthPlanId)) {
      errorMessage.value = '当前月计划不存在或尚未审批通过'
      editableRows.value = []
      return
    }
    rebuildEditableRows()
    resetTargetMonthView()
  } catch (error) {
    if (requestId !== loadOptionsRequestId) return
    errorMessage.value = error instanceof Error ? error.message : '成果数据加载失败，请稍后重试'
  } finally {
    if (requestId === loadOptionsRequestId) loading.value = false
  }
}

onMounted(() => { if (activePlanType.value === 'MONTH') void loadOptions() })
watch(activePlanType, (planType) => {
  if (planType === 'MONTH' && !options.monthPlanOptions.length) void loadOptions()
})
</script>

<style scoped>
.employee-result-submit { --result-accent:#2d776c; --result-accent-soft:#edf5f1; --result-line:#d6dfda; max-width:100%; overflow-x:hidden; }
.result-page-header,.result-header-actions,.month-board-toolbar,.target-month-header,.toolbar-actions { display:flex; align-items:center; }
.result-page-header { justify-content:space-between; gap:24px; padding-bottom:12px; border-bottom:1px solid var(--line); }
.result-page-header .page-title { margin:0 0 3px; font-size:16px; letter-spacing:0; }
.result-page-header .page-subtitle { margin:0; font-size:12px; }
.result-header-actions { gap:10px; }
.result-plan-type-switch { padding:3px; border:1px solid var(--result-line); border-radius:7px; background:#eef2ef; }
.result-plan-type-switch :deep(.el-radio-button__inner) { min-height:32px; padding:7px 12px; border:0!important; border-radius:5px!important; color:#566a63; background:transparent; box-shadow:none!important; font-weight:700; }
.result-plan-type-switch :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) { color:var(--result-accent); background:#fff; box-shadow:0 1px 4px rgb(45 76 65 / 14%)!important; }
.result-loading { margin-top:20px; }
.result-inline-toolbar { display:flex; justify-content:flex-end; margin-bottom:14px; }
.result-month-board { margin-top:18px; }
.month-board-toolbar { justify-content:space-between; gap:16px; min-height:58px; padding:10px 14px; border:1px solid var(--result-line); border-radius:8px 8px 0 0; background:#f6f8f5; }
.month-board-toolbar > div:first-child { display:grid; gap:3px; }
.month-board-toolbar strong { font-size:16px; }
.month-board-toolbar span { color:var(--muted); font-size:13px; }
.toolbar-actions { gap:2px; }
.toolbar-actions .el-button { margin:0; }
.target-month-list { border:1px solid var(--result-line); border-top:0; background:#fff; }
.target-month-group + .target-month-group { border-top:1px solid var(--result-line); }
.target-month-header { justify-content:space-between; min-height:58px; padding:7px 12px; background:#f8faf7; }
.target-month-toggle { display:grid; grid-template-columns:20px auto 1fr; align-items:center; gap:9px; min-width:0; padding:8px 6px; border:0; color:var(--ink); background:transparent; text-align:left; cursor:pointer; }
.target-month-toggle .el-icon { color:var(--result-accent); }
.target-month-toggle strong { font-size:16px; }
.target-month-toggle span { color:var(--muted); font-size:13px; }
.target-month-actions { display:flex; flex:0 0 auto; gap:8px; }
.target-month-actions .el-button { margin:0; }
.target-month-details { border-top:1px solid var(--result-line); }
.performance-table-wrap,.result-edit-table-wrap { width:100%; overflow:auto; }
.performance-table { width:100%; min-width:1260px; border-collapse:collapse; table-layout:fixed; color:#263a34; background:#fff; font-size:13px; }
.performance-table th,.performance-table td { padding:10px 9px; border-right:1px solid #cfd9d3; border-bottom:1px solid #d9e1dc; vertical-align:middle; line-height:1.55; overflow-wrap:anywhere; }
.performance-table th:last-child,.performance-table td:last-child { border-right:0; }
.performance-table th { color:#344c44; background:#eef3ef; font-weight:750; text-align:center; }
.performance-table tbody tr:hover { background:#f6faf8; }
.performance-table tfoot td { background:#f4f7f4; font-weight:750; text-align:right; }
.col-seq{width:50px}.col-work{width:210px}.col-weight{width:72px}.col-standard{width:220px}.col-date{width:150px}.col-completion{width:180px}.col-remark{width:160px}.col-value{width:100px}.col-performance{width:120px}.col-evidence{width:95px}
.standard-cell { white-space:pre-wrap; }
.cell-center { text-align:center; }
.cell-number { text-align:right; font-variant-numeric:tabular-nums; }
.evidence-state { display:inline-block; min-width:42px; min-height:20px; }
.evidence-state.is-uploaded { padding:2px 7px; border-radius:4px; color:#226b55; background:#e6f3ed; font-size:12px; }
.target-month-pagination { display:flex; justify-content:flex-end; padding:14px; border-top:1px solid var(--result-line); }
.result-target-empty { display:grid; min-height:220px; place-items:center; align-content:center; gap:8px; color:var(--muted); border:1px solid var(--result-line); background:#fff; text-align:center; }
.result-target-empty .el-icon { color:var(--result-accent); font-size:28px; }
.result-target-empty strong { color:var(--ink); }

.result-edit-table-wrap { border:1px solid var(--result-line); border-top:0; }
.result-edit-table { width:100%; min-width:1660px; border-collapse:collapse; table-layout:fixed; color:#263a34; background:#fff; font-size:13px; }
.result-edit-table th,.result-edit-table td { padding:8px; border-right:1px solid #d6dfda; border-bottom:1px solid #dce4df; vertical-align:top; }
.result-edit-table th { height:46px; color:#3d544d; background:#eef3ef; font-weight:750; text-align:center; vertical-align:middle; }
.result-edit-table th:last-child,.result-edit-table td:last-child { border-right:0; }
.result-edit-table tr.is-active td { background:#f3f8f5; }
.result-edit-table tr.is-readonly td { background:#f7f8f6; }
.result-edit-table .sheet-edit-cell { padding:0; background:#fff; }
.result-edit-table tr.is-active .sheet-edit-cell { background:#fbfdfb; }
.result-edit-table .sheet-edit-cell:focus-within { background:#f3f8f5; box-shadow:inset 0 0 0 1px #7ca397; }
.result-edit-table .sheet-edit-cell :deep(.el-textarea),.result-edit-table .sheet-edit-cell :deep(.el-textarea__inner) { height:100%; }
.result-edit-table .sheet-edit-cell :deep(.el-textarea__inner) { min-height:100px!important; padding:8px; border-radius:0; background:transparent; box-shadow:none; color:#263a34; font-size:12px; line-height:1.55; resize:none; }
.result-edit-table .sheet-edit-cell :deep(.el-textarea__inner:focus) { box-shadow:none; }
.result-edit-table .sheet-edit-cell :deep(.el-input-number),.result-edit-table .sheet-edit-cell :deep(.el-input-number .el-input) { width:100%; height:100%; }
.result-edit-table .sheet-edit-cell :deep(.el-input__wrapper) { min-height:100px; padding:0 7px; border-radius:0; background:transparent; box-shadow:none; }
.result-edit-table .sheet-edit-cell :deep(.el-input__inner) { color:#263a34; font-size:12px; text-align:right; }
.result-edit-table .sheet-edit-cell :deep(.el-input__wrapper.is-focus) { box-shadow:none; }
.result-edit-table :deep(.el-input-number),.result-edit-table :deep(.el-input-number .el-input) { width:100%; }
.edit-seq{width:48px}.edit-work{width:230px}.edit-weight{width:66px}.edit-standard{width:210px}.edit-date{width:140px}.edit-completion{width:230px}.edit-remark{width:210px}.edit-value{width:100px}.edit-performance{width:150px}.edit-evidence{width:145px}.edit-action{width:110px}
.file-picker { display:inline-flex; align-items:center; justify-content:center; gap:5px; min-height:32px; padding:5px 9px; border:1px solid #b9cbc3; border-radius:5px; color:var(--result-accent); background:#f5faf7; cursor:pointer; }
.file-picker:hover { border-color:var(--result-accent); background:#edf6f1; }
.file-picker.is-large { min-height:38px; }
.selected-file-name { display:block; margin-top:6px; overflow:hidden; color:var(--muted); font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.muted-text { color:var(--muted); font-size:12px; }
.visually-hidden { position:absolute; width:1px; height:1px; padding:0; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }

.ai-check-dock { position:fixed; z-index:40; top:132px; right:0; display:grid; grid-template-columns:48px 360px; width:48px; max-height:calc(100vh - 158px); overflow:hidden; border:1px solid #bfcfc7; border-right:0; border-radius:8px 0 0 8px; background:#fff; transition:width 220ms cubic-bezier(.22,1,.36,1); }
.ai-check-dock:hover,.ai-check-dock:focus-within { width:408px; }
.ai-dock-handle { display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px; min-height:156px; color:#fff; background:#23483f; cursor:pointer; }
.ai-dock-handle span { writing-mode:vertical-rl; font-size:13px; letter-spacing:0; }
.ai-dock-content { width:360px; max-height:calc(100vh - 160px); overflow:auto; padding:14px; }
.ai-dock-title { margin-bottom:10px; padding-bottom:10px; border-bottom:1px solid var(--result-line); }
.ai-dock-title > div { display:grid; gap:3px; }
.ai-dock-title strong { font-size:16px; }
.ai-dock-title span { overflow:hidden; color:var(--muted); font-size:12px; text-overflow:ellipsis; white-space:nowrap; }
.result-ai-panel { margin:0; border-radius:6px; box-shadow:none; }
.ai-submit-command { display:flex; align-items:center; justify-content:space-between; width:100%; min-height:48px; margin-top:10px; padding:8px 12px; border:0; border-radius:6px; color:#fff; background:#23483f; cursor:pointer; }
.ai-submit-command small { color:#c9ddd5; }
.ai-submit-command:disabled { cursor:not-allowed; opacity:.5; }
.performance-print-sheet { display:none; }

@media (max-width:1100px) {
  .ai-check-dock { top:auto; bottom:18px; max-height:70vh; }
}
@media (max-width:760px) {
  .result-page-header,.month-board-toolbar,.target-month-header { align-items:flex-start; flex-direction:column; }
  .target-month-toggle { width:100%; }
  .target-month-actions { width:100%; }
  .target-month-actions .el-button { flex:1; }
  .ai-check-dock:hover,.ai-check-dock:focus-within { width:min(408px,calc(100vw - 12px)); }
}
@media (prefers-reduced-motion:reduce) { .ai-check-dock { transition:none; } }

@page { size:A4 landscape; margin:0; }

@media print {
  :global(html),
  :global(body),
  :global(#app),
  :global(.app-shell),
  :global(.main) {
    display:block!important;
    width:100%!important;
    height:auto!important;
    min-height:0!important;
    margin:0!important;
    padding:0!important;
    overflow:visible!important;
    background:#fff!important;
  }

  :global(.sidebar),
  :global(.topbar) {
    display:none!important;
  }

  :global(body *) { visibility:hidden!important; }

  .employee-result-submit {
    min-height:0!important;
    margin:0!important;
    padding:0!important;
    border:0!important;
    background:#fff!important;
    box-shadow:none!important;
  }

  .employee-result-submit > :not(.performance-print-sheet) { display:none!important; }

  .performance-print-sheet,.performance-print-sheet * { visibility:visible!important; }
  .performance-print-sheet { position:static; display:block; box-sizing:border-box; width:100%; margin:0; padding:10mm 8mm; color:#000; background:#fff; font-family:"SimSun","Microsoft YaHei",sans-serif; break-after:avoid; page-break-after:avoid; }
  .performance-print-sheet h1 { width:max-content; max-width:100%; margin:0 auto 4px; padding:0 18px 1px; border-bottom:1px solid #000; font-size:18px; font-weight:700; line-height:1.25; text-align:center; }
  .performance-print-meta { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin:0 0 4px; font-size:10px; line-height:1.35; }
  .performance-print-meta span { display:flex; align-items:flex-end; gap:4px; min-width:0; }
  .performance-print-meta span:nth-child(2) { justify-content:center; text-align:center; }
  .performance-print-meta span:last-child { justify-content:flex-end; text-align:right; }
  .performance-print-meta strong { display:inline-block; min-width:76px; padding:0 8px 1px; border-bottom:1px solid #000; font-weight:400; line-height:1.1; text-align:center; }
  .performance-print-table { width:100%; border-collapse:collapse; table-layout:fixed; color:#000; font-size:8.5px; line-height:1.32; break-after:avoid; page-break-after:avoid; }
  .performance-print-table th,.performance-print-table td { padding:3px 3px; border:1px solid #000; overflow-wrap:anywhere; vertical-align:middle; }
  .performance-print-table th { background:#fff; font-weight:700; text-align:center; }
  .performance-print-table td:nth-child(5) { white-space:nowrap; }
  .performance-print-table tbody tr { break-inside:avoid; page-break-inside:avoid; }
  .performance-print-table thead { display:table-header-group; }
  .performance-print-table tfoot { display:table-row-group; }
  .performance-print-table tfoot td { padding:2px 3px; font-weight:700; text-align:center; }
  .performance-print-table tfoot td:first-child { text-align:center; }
  .performance-print-table .print-confirm-block-row td { padding:0; font-size:9.5px; font-weight:400; line-height:1.55; text-align:left; vertical-align:top; }
  .performance-print-table .print-confirm-block { min-height:27mm; padding:5px 3px 3px; }
  .performance-print-table .print-confirm-block p { margin:0 0 6px; }
  .performance-print-table .signature-row-content { display:grid; grid-template-columns:max-content 40mm 1fr; align-items:end; column-gap:4px; min-height:7mm; }
  .performance-print-table .signature-line { display:block; height:1.2em; border-bottom:1px solid #000; }
  .performance-print-table .signature-date { justify-self:center; min-width:42mm; text-align:left; }
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
  .performance-print-table .print-number { text-align:right; font-variant-numeric:tabular-nums; }
  .print-hidden { display:none!important; }
}
</style>
