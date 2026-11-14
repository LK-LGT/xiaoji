<template>
  <main class="package-workspace">
    <header class="workspace-header">
      <div class="workspace-title-row">
        <el-button text :icon="ArrowLeft" @click="backToWorkbench">返回周期工作台</el-button>
        <div v-if="activePackage" class="workspace-title">
          <span>{{ activePackage.type === 'MONTH' ? '月度绩效成果' : '周度执行成果' }}</span>
          <strong>{{ activePackage.title }}</strong>
          <small>{{ activePackage.counts.completed }}/{{ activePackage.counts.total }}项已完成 · {{ saveStatusText }}</small>
        </div>
      </div>
      <div class="workspace-header-actions">
        <el-segmented v-model="viewMode" :options="viewModeOptions" aria-label="成果填写方式" />
        <el-button v-if="activePackage?.type === 'MONTH'" :icon="View" @click="previewVisible = true">预览汇总表</el-button>
      </div>
    </header>

    <el-alert v-if="errorMessage" class="workspace-error" type="warning" :closable="false" show-icon :title="errorMessage">
      <el-button link type="primary" @click="loadWorkspace">重新加载</el-button>
    </el-alert>
    <el-skeleton v-if="loading" class="workspace-loading" :rows="10" animated />

    <div v-else-if="activePackage && activeTask && activeDraft" class="workspace-layout" :class="{ 'is-sheet': viewMode === 'SHEET' }">
      <aside v-if="viewMode === 'FORM'" class="task-navigator">
        <div class="navigator-summary">
          <strong>任务导航</strong>
          <span>{{ filteredTasks.length }}/{{ activePackage.tasks.length }}</span>
        </div>
        <el-segmented v-model="taskFilter" class="task-filter" :options="taskFilterOptions" />
        <div class="task-list">
          <button
            v-for="task in filteredTasks"
            :key="task.key"
            type="button"
            class="task-nav-item"
            :class="{ 'is-active': task.key === activeTask.key }"
            @click="selectTask(task)"
          >
            <span class="task-state-dot" :class="`is-${task.status.toLowerCase()}`"></span>
            <span class="task-nav-copy"><strong>{{ task.taskName }}</strong><small>{{ statusMeta(task.status).label }}</small></span>
            <el-icon><ArrowRight /></el-icon>
          </button>
        </div>
      </aside>

      <section v-if="viewMode === 'FORM'" class="editor-pane">
        <MonthResultTaskEditor
          v-if="activeTask.type === 'MONTH'"
          ref="monthEditorRef"
          :key="activeTask.key"
          :task="activeTask"
          :draft="activeDraft"
          :accepted-file-types="monthOptions.acceptedFileTypes"
          :max-file-size-mb="monthOptions.maxFileSizeMb"
          @dirty="scheduleAutosave"
        />
        <WeekResultTaskEditor
          v-else
          ref="weekEditorRef"
          :key="activeTask.key"
          :task="activeTask"
          :draft="activeDraft"
          :accepted-file-types="weekOptions.acceptedFileTypes"
          :max-file-size-mb="weekOptions.maxFileSizeMb"
          @dirty="scheduleAutosave"
        />
      </section>

      <section v-else class="editor-pane sheet-editor-pane">
        <div class="sheet-toolbar">
          <div><strong>Excel表格填写</strong><span>计划字段只读，成果字段可直接编辑；修改会自动保存为草稿。</span></div>
          <el-tag size="small" effect="plain">{{ activePackage.tasks.length }} 项任务</el-tag>
        </div>
        <div class="sheet-table-wrap">
          <table class="package-result-sheet" :class="{ 'is-month-sheet': activePackage.type === 'MONTH' }">
            <thead>
              <tr>
                <th class="sheet-seq">序号</th><th class="sheet-task">工作计划内容</th>
                <template v-if="activePackage.type === 'MONTH'">
                  <th class="sheet-weight">权重</th><th class="sheet-standard">考核标准</th><th class="sheet-date">完成时间</th>
                  <th class="sheet-completion">完成情况</th><th class="sheet-remark">备注</th><th class="sheet-value">预估价值</th><th class="sheet-performance">个人整体绩效</th>
                </template>
                <template v-else>
                  <th class="sheet-plan">计划交付物</th><th class="sheet-completion">完成情况</th><th class="sheet-result">实际交付物</th><th class="sheet-remark">备注 / 补充说明</th>
                </template>
                <th class="sheet-evidence">证明提交（选填）</th><th class="sheet-status">{{ activePackage.type === 'MONTH' ? '操作' : '状态' }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(task, index) in activePackage.tasks" :key="task.key" :class="{ 'is-active': task.key === activeTask.key, 'is-readonly': !task.editable, 'is-dragging': draggingTaskKey === task.key }" @click="selectTask(task)" @dragenter.prevent="onSheetDragEnter(task)" @dragover.prevent="onSheetDragEnter(task)" @dragleave.prevent="onSheetDragLeave($event, task)" @drop.prevent="onSheetDrop($event, task)">
                <td class="sheet-center">{{ index + 1 }}</td>
                <td>
                  <MonthPlanTaskContentView v-if="task.type === 'MONTH'" :content="task.taskContent" :fallback="task.taskName" />
                  <template v-else>{{ task.taskContent || task.taskName }}<small v-if="activePackage.type === 'WEEK'">{{ task.periodLabel }}</small></template>
                </td>
                <template v-if="task.type === 'MONTH'">
                  <td class="sheet-center">{{ sheetWeight(task) }}%</td>
                  <td><div class="sheet-standard">{{ sheetStandard(task) }}</div></td>
                  <td class="sheet-center">{{ sheetDateRange(task) }}</td>
                </template>
                <template v-else>
                  <td>{{ task.plannedDeliverable || '未设置' }}</td>
                </template>
                <td class="sheet-editable-cell" @click.stop="focusSheetCell($event); selectTask(task)"><el-input v-model="drafts[task.key].completionStatus" type="textarea" :rows="2" resize="none" maxlength="1000" placeholder="填写完成情况" :disabled="!task.editable" @click.stop="selectTask(task)" @input="scheduleTaskAutosave(task)" /></td>
                <td v-if="task.type === 'WEEK'" class="sheet-editable-cell" @click.stop="focusSheetCell($event); selectTask(task)"><el-input v-model="drafts[task.key].deliverable" type="textarea" :rows="2" resize="none" maxlength="500" placeholder="填写实际交付物" :disabled="!task.editable" @click.stop="selectTask(task)" @input="scheduleTaskAutosave(task)" /></td>
                <td class="sheet-editable-cell" @click.stop="focusSheetCell($event); selectTask(task)"><el-input v-model="drafts[task.key].remark" type="textarea" :rows="2" resize="none" maxlength="2000" placeholder="补充说明" :disabled="!task.editable" @click.stop="selectTask(task)" @input="scheduleTaskAutosave(task)" /></td>
                <td v-if="task.type === 'MONTH'" class="sheet-value sheet-center">{{ sheetMoney(taskEstimatedValue(task)) }}</td>
                <td v-if="task.type === 'MONTH'" class="sheet-editable-cell" @click.stop="focusSheetCell($event); selectTask(task)"><el-input-number v-model="drafts[task.key].personalPerformance" :controls="false" :precision="2" placeholder="填写" :disabled="!task.editable" @click.stop="selectTask(task)" @change="scheduleTaskAutosave(task)" /></td>
                <td class="sheet-evidence-cell" @click.stop>
                  <label class="sheet-evidence-picker" :class="{ 'is-compact': hasSheetEvidence(task), 'is-disabled': !task.editable, 'is-dragging': draggingTaskKey === task.key }" :for="`sheet-result-file-${task.type}-${task.taskId}`">
                    <el-icon><Paperclip /></el-icon><span>{{ hasSheetEvidence(task) ? '更换文件' : '选择文件' }}</span>
                  </label>
                  <input :id="`sheet-result-file-${task.type}-${task.taskId}`" class="visually-hidden" type="file" :accept="sheetFileAccept(task)" :disabled="!task.editable" @change="onSheetFileChange($event, task)" />
                  <span v-if="drafts[task.key].file" class="sheet-evidence-selected" :title="drafts[task.key].file?.name || ''"><span class="sheet-evidence-name">{{ drafts[task.key].file?.name || '' }}</span><button type="button" aria-label="移除附件" @click.stop="removeSheetFile(task)"><el-icon><Close /></el-icon></button></span>
                  <span v-else-if="task.type === 'WEEK' && task.weekResult?.evidences.length" class="sheet-evidence-existing">已有 {{ task.weekResult.evidences.length }} 个附件</span>
                  <span v-else-if="task.type === 'MONTH' && task.monthVersion?.hasEvidence" class="sheet-evidence-existing">已有附件</span>
                </td>
                <td class="sheet-center"><ResultStatusTag :status="task.status" /><small v-if="task.type === 'WEEK' && task.editable && validationIssues(task).length" class="sheet-issue">缺少 {{ validationIssues(task).join('、') }}</small></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <footer class="workspace-action-bar">
        <div class="task-step-actions">
          <el-button :icon="ArrowLeft" :disabled="activeTaskIndex <= 0" aria-label="上一个任务" @click="moveTask(-1)" />
          <el-button :icon="ArrowRight" :disabled="activeTaskIndex >= activePackage.tasks.length - 1" aria-label="下一个任务" @click="moveTask(1)" />
        </div>
        <span class="save-state" :class="{ 'is-error': saveState === 'error' }">{{ saveStatusText }}</span>
        <div v-if="activeTask.editable" class="primary-actions">
          <el-button @click="saveCurrentDraft(true)">保存草稿</el-button>
          <el-button @click="saveAndNext">保存并下一个</el-button>
          <el-button v-if="activeTask.type === 'MONTH'" :icon="MagicStick" :loading="aiChecking" @click="checkCurrentTask">AI检查</el-button>
          <el-button type="primary" :loading="submitting" @click="submitCurrentTask">提交当前任务</el-button>
          <el-button plain type="primary" :loading="batchSubmitting" @click="openBatchDialog">批量提交</el-button>
        </div>
        <div v-else class="readonly-action">
          <ResultStatusTag :status="activeTask.status" />
          <span>{{ activeTask.status === 'SUBMITTED' ? '当前成果正在审批，暂不可修改' : '当前成果已锁定为只读' }}</span>
        </div>
      </footer>
    </div>

    <el-dialog v-model="batchDialogVisible" title="批量提交" class="result-dialog" :width="'var(--result-dialog-md)'">
      <div class="batch-summary">
        <div><strong>{{ batchReady.length }}</strong><span>可以提交</span></div>
        <div><strong>{{ batchIncomplete.length }}</strong><span>信息不完整</span></div>
        <div><strong>{{ batchLocked.length }}</strong><span>审批中或已完成</span></div>
      </div>
      <div v-if="batchIncomplete.length" class="batch-issues">
        <div v-for="item in batchIncomplete" :key="item.task.key">
          <span><strong>{{ item.task.taskName }}</strong><small>缺少：{{ item.issues.join('、') }}</small></span>
          <el-button link type="primary" @click="goCompleteTask(item.task)">去完善</el-button>
        </div>
      </div>
      <div v-if="batchResults.length" class="batch-results">
        <el-alert :type="batchResults.some((item) => !item.success) ? 'warning' : 'success'" :closable="false" show-icon>
          <template #title>成功提交 {{ batchResults.filter((item) => item.success).length }} 项，未提交 {{ batchResults.filter((item) => !item.success).length }} 项</template>
        </el-alert>
        <p v-for="item in batchResults.filter((row) => !row.success)" :key="item.task.key">{{ item.task.taskName }}：{{ item.message }}</p>
      </div>
      <template #footer>
        <el-button @click="batchDialogVisible = false">关闭</el-button>
        <el-button v-if="!batchResults.length" type="primary" :disabled="!batchReady.length" :loading="batchSubmitting" @click="submitBatch">提交{{ batchReady.length }}项</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="previewVisible" title="月度绩效成果汇总预览" class="result-dialog" :width="'var(--result-dialog-lg)'" top="5vh">
      <div v-if="activePackage" class="summary-preview-wrap">
        <div class="performance-preview-sheet">
          <h2>{{ performancePrintTitle(activePackage.periodKey) }}</h2>
          <div class="performance-preview-meta">
            <span>姓名：<strong>{{ printPlan?.employeeName || '' }}</strong></span>
            <span>项目：<strong>{{ printPlan?.projectName || '' }}</strong></span>
            <span>岗位：<strong>{{ printPlan?.positionName || '' }}</strong></span>
          </div>
          <table class="performance-preview-table">
            <thead><tr><th>序号</th><th>工作计划内容</th><th>权重（%）</th><th>考核标准（交付物、质量）</th><th>完成时间段</th><th>完成情况（完成度详细说明）</th><th>备注（其他情况说明）</th><th>预估价值</th><th>占比完成度</th></tr></thead>
            <tbody>
              <tr v-for="(task, index) in activePackage.tasks" :key="task.key" @dblclick="selectTask(task); previewVisible = false">
                <td>{{ index + 1 }}</td>
                <td><MonthPlanTaskContentView :content="task.taskContent" :fallback="task.taskName" /></td>
                <td>{{ printNumber(taskPerformanceWeight(task)) }}%</td>
                <td><div v-if="hasPrintText(task.plannedDeliverable)">交付物：{{ printFieldText(task.plannedDeliverable) }}</div><div v-if="hasPrintText(task.quality)">质量：{{ printFieldText(task.quality) }}</div><span v-if="!hasPrintText(task.plannedDeliverable) && !hasPrintText(task.quality)">-</span></td>
                <td>{{ printDateRange(task.startDate, task.endDate) || '-' }}</td><td>{{ printCompletionStatus(task) }}</td><td>{{ printRemark(task) }}</td>
                <td>{{ printMoney(taskEstimatedValue(task)) }}</td><td>{{ printCompletionRatio(task) }}</td>
              </tr>
            </tbody>
            <tfoot><tr><td colspan="7">预估价值合计</td><td>{{ printMoney(activePackage.tasks.reduce((sum, task) => sum + taskEstimatedValue(task), 0)) }}</td><td>/</td></tr><tr><td colspan="7">个人本月发放绩效合计</td><td>/</td><td>{{ printRatio(activePackage.tasks.reduce((sum, task) => sum + Number(printCompletionRatio(task) || 0), 0)) }}</td></tr></tfoot>
          </table>
          <div class="performance-preview-confirmation">
            <p>本人核对表格内月度工作计划目标及月底执行情况，确认信息真实完整无误，知晓并认可表格兼具目标确定与绩效考核用途，自愿接受绩效评估结果，承担计划未达预期的相应绩效后果，对以上内容无异议。</p>
            <div>员工本人签字捺印：________________　　时间：____年__月__日</div>
            <div>项目经理签字：________________　　　时间：____年__月__日</div>
            <div>部门负责人签字：________________　　时间：____年__月__日</div>
          </div>
        </div>
      </div>
      <template #footer><el-button :icon="Download" @click="downloadPreviewExcel">下载 Excel</el-button><el-button :icon="Printer" @click="printPreview">打印</el-button><el-button type="primary" @click="previewVisible = false">关闭</el-button></template>
    </el-dialog>

    <section v-if="printVisible && activePackage?.type === 'MONTH'" class="result-print-sheet" aria-hidden="true">
      <h1>{{ performancePrintTitle(activePackage.periodKey) }}</h1>
      <div class="performance-print-meta">
        <span>姓名：<strong>{{ printPlan?.employeeName || '' }}</strong></span>
        <span>项目：<strong>{{ printPlan?.projectName || '' }}</strong></span>
        <span>岗位：<strong>{{ printPlan?.positionName || '' }}</strong></span>
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
          <tr v-for="(task, index) in activePackage.tasks" :key="task.key">
            <td class="print-center print-seq-column">{{ index + 1 }}</td>
            <td>
              <MonthPlanTaskContentView :content="task.taskContent" :fallback="task.taskName" />
            </td>
            <td class="print-center print-weight-column">{{ printNumber(taskPerformanceWeight(task)) }}%</td>
            <td class="print-standard-cell">
              <template v-if="hasPrintText(task.plannedDeliverable) || hasPrintText(task.quality)">
                <div v-if="hasPrintText(task.plannedDeliverable)" class="print-standard-line"><strong>交付物：</strong><span>{{ printFieldText(task.plannedDeliverable) }}</span></div>
                <div v-if="hasPrintText(task.quality)" class="print-standard-line"><strong>质量：</strong><span>{{ printFieldText(task.quality) }}</span></div>
              </template>
              <template v-else>&nbsp;</template>
            </td>
            <td class="print-center print-date-column">{{ printDateRange(task.startDate, task.endDate) }}</td>
            <td>{{ printCompletionStatus(task) }}</td>
            <td>{{ printRemark(task) }}</td>
            <td class="print-center print-value-column">{{ printMoney(taskEstimatedValue(task)) }}</td>
            <td class="print-center print-ratio-column">{{ printCompletionRatio(task) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="7">预估价值合计</td>
            <td class="print-center print-value-column">{{ printMoney(activePackage.tasks.reduce((sum, task) => sum + taskEstimatedValue(task), 0)) }}</td>
            <td class="print-center print-ratio-column">/</td>
          </tr>
          <tr>
            <td colspan="7">个人本月发放绩效合计</td>
            <td class="print-center print-value-column">/</td>
            <td class="print-center print-ratio-column">{{ printRatio(activePackage.tasks.reduce((sum, task) => sum + Number(printCompletionRatio(task) || 0), 0)) }}</td>
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
  </main>
</template>

<script setup lang="ts">
import { ArrowLeft, ArrowRight, Close, Download, MagicStick, Paperclip, Printer, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { downloadEmployeeResultExcelApi, getEmployeeResultSubmitOptionsApi, submitEmployeeResultApi, type EmployeeResultSubmitOptionsResp } from '@/api/employee'
import { previewResultAiReviewApi } from '@/api/aiReview'
import { getWeekResultSubmitOptionsApi, submitWeekResultApi, type WeekResultSubmitOptions } from '@/api/weekResult'
import { currentMonthKey } from '@/utils/monthGroups'
import MonthResultTaskEditor from './components/MonthResultTaskEditor.vue'
import WeekResultTaskEditor from './components/WeekResultTaskEditor.vue'
import {
  buildResultWorkbench,
  statusMeta,
  type BatchSubmitItemResult,
  type ResultPackageType,
  type ResultTaskDraft,
  type ResultWorkbenchPackage,
  type ResultWorkbenchTask,
} from './resultWorkbenchModel'
import ResultStatusTag from '@/components/results/ResultStatusTag.vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'
import { saveBlob } from '@/utils/download'

type TaskFilter = 'all' | 'pending' | 'submitted' | 'completed'
type WorkspaceViewMode = 'FORM' | 'SHEET'

const route = useRoute()
const router = useRouter()
const resultBasePath = computed(() => route.path.startsWith('/leader/') ? '/leader/results' : '/employee/results')
const loading = ref(false)
const errorMessage = ref('')
const activePackage = ref<ResultWorkbenchPackage | null>(null)
const activeTaskKey = ref('')
const taskFilter = ref<TaskFilter>('all')
const viewMode = ref<WorkspaceViewMode>('SHEET')
const viewModeOptions = [{ label: 'Excel表格', value: 'SHEET' }, { label: '表单填写', value: 'FORM' }]
const drafts = reactive<Record<string, ResultTaskDraft>>({})
const saveState = ref<'saved' | 'dirty' | 'saving' | 'error'>('saved')
const lastSavedAt = ref('')
const submitting = ref(false)
const aiChecking = ref(false)
const batchSubmitting = ref(false)
const batchDialogVisible = ref(false)
const previewVisible = ref(false)
const printVisible = ref(false)
const batchResults = ref<BatchSubmitItemResult[]>([])
const monthEditorRef = ref<{ validate: () => boolean } | null>(null)
const weekEditorRef = ref<{ validate: () => boolean } | null>(null)
const draggingTaskKey = ref('')
const monthOptions = reactive<EmployeeResultSubmitOptionsResp>({ monthPlanOptions: [], monthPlanItemOptions: [], resultVersions: [], acceptedFileTypes: [], maxFileSizeMb: 20 })
const weekOptions = reactive<WeekResultSubmitOptions>({ plans: [], acceptedFileTypes: [], maxFileSizeMb: 20 })
let autosaveTimer: ReturnType<typeof setTimeout> | null = null

const packageType = computed<ResultPackageType>(() => String(route.params.type).toUpperCase() === 'WEEK' ? 'WEEK' : 'MONTH')
const planId = computed(() => Number(route.params.planId))
const activeTask = computed(() => activePackage.value?.tasks.find((task) => task.key === activeTaskKey.value) || activePackage.value?.tasks[0] || null)
const activeDraft = computed(() => activeTask.value ? drafts[activeTask.value.key] : null)
const printPlan = computed(() => activePackage.value?.type === 'MONTH'
  ? monthOptions.monthPlanOptions.find((plan) => plan.id === activePackage.value?.planId)
  : null)
const activeTaskIndex = computed(() => activePackage.value?.tasks.findIndex((task) => task.key === activeTask.value?.key) ?? -1)
const filteredTasks = computed(() => (activePackage.value?.tasks || []).filter((task) => {
  if (taskFilter.value === 'pending') return task.editable
  if (taskFilter.value === 'submitted') return task.status === 'SUBMITTED'
  if (taskFilter.value === 'completed') return task.status === 'CONFIRMED' || task.status === 'APPROVED'
  return true
}))
const taskFilterOptions = computed(() => [
  { label: `全部 ${activePackage.value?.tasks.length || 0}`, value: 'all' },
  { label: `待处理 ${activePackage.value?.counts.pending || 0}`, value: 'pending' },
  { label: `审批中 ${activePackage.value?.counts.submitted || 0}`, value: 'submitted' },
  { label: `已完成 ${activePackage.value?.counts.completed || 0}`, value: 'completed' },
])
const saveStatusText = computed(() => {
  if (saveState.value === 'dirty') return '有未保存修改'
  if (saveState.value === 'saving') return '正在保存...'
  if (saveState.value === 'error') return '保存失败，请重试'
  return lastSavedAt.value ? `已保存 ${lastSavedAt.value}` : '草稿将自动保存'
})

const batchReady = computed(() => (activePackage.value?.tasks || []).filter((task) => task.editable && validationIssues(task).length === 0))
const batchIncomplete = computed(() => (activePackage.value?.tasks || []).filter((task) => task.editable && validationIssues(task).length > 0).map((task) => ({ task, issues: validationIssues(task) })))
const batchLocked = computed(() => (activePackage.value?.tasks || []).filter((task) => !task.editable))

function storageKey(task: ResultWorkbenchTask) { return `employee-result-draft:${task.type}:${task.planId}:${task.taskId}` }
function initialDraft(task: ResultWorkbenchTask): ResultTaskDraft {
  const source = task.monthVersion
  const week = task.weekResult
  const base: ResultTaskDraft = {
    completionStatus: source?.completionStatus || source?.description || week?.completionStatus || week?.content || '',
    description: source?.description || source?.completionStatus || '',
    deliverable: week?.deliverable || task.plannedDeliverable || '',
    remark: source?.remark || week?.remark || '',
    personalPerformance: source?.personalPerformance ?? null,
    file: null,
    aiReview: null,
    aiReviewStale: false,
    dirty: false,
  }
  try {
    const saved = localStorage.getItem(storageKey(task))
    if (saved) Object.assign(base, JSON.parse(saved), { file: null, aiReview: null, dirty: false })
  } catch { /* Invalid local drafts are ignored. */ }
  return base
}

function serializeDraft(draft: ResultTaskDraft) {
  return JSON.stringify({ completionStatus: draft.completionStatus, description: draft.description, deliverable: draft.deliverable, remark: draft.remark, personalPerformance: draft.personalPerformance })
}

function scheduleTaskAutosave(task: ResultWorkbenchTask | null = activeTask.value) {
  const draft = task ? drafts[task.key] : null
  if (draft) draft.dirty = true
  saveState.value = 'dirty'
  if (autosaveTimer) clearTimeout(autosaveTimer)
  autosaveTimer = setTimeout(() => saveTaskDraft(task, false), 1200)
}

function scheduleAutosave() { scheduleTaskAutosave(activeTask.value) }

function sheetFileAccept(task: ResultWorkbenchTask) {
  const options = task.type === 'WEEK' ? weekOptions : monthOptions
  return options.acceptedFileTypes.map((type) => `.${type.toLowerCase()}`).join(',')
}

function monthItem(task: ResultWorkbenchTask) {
  return monthOptions.monthPlanItemOptions.find((item) => item.id === task.taskId)
}

function sheetWeight(task: ResultWorkbenchTask) {
  return Number(monthItem(task)?.performanceWeight || 0).toFixed(2).replace(/\.00$/, '')
}

function sheetStandard(task: ResultWorkbenchTask) {
  return [task.plannedDeliverable ? `交付物：${task.plannedDeliverable}` : '', task.quality ? `质量：${task.quality}` : '']
    .filter(Boolean)
    .join('；') || '未设置'
}

function sheetDateRange(task: ResultWorkbenchTask) {
  const format = (value?: string) => {
    if (!value) return ''
    const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
    return match ? `${Number(match[2])}.${Number(match[3])}` : value
  }
  return [format(task.startDate), format(task.endDate)].filter(Boolean).join('-') || '--'
}

function sheetMoney(value?: number) {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function hasSheetEvidence(task: ResultWorkbenchTask) {
  return Boolean(drafts[task.key]?.file)
    || (task.type === 'WEEK' ? Boolean(task.weekResult?.evidences.length) : Boolean(task.monthVersion?.hasEvidence))
}

function onSheetDragEnter(task: ResultWorkbenchTask) {
  if (task.editable) draggingTaskKey.value = task.key
}

function onSheetDragLeave(event: DragEvent, task: ResultWorkbenchTask) {
  if (draggingTaskKey.value !== task.key) return
  const relatedTarget = event.relatedTarget as Node | null
  if (relatedTarget && (event.currentTarget as HTMLElement).contains(relatedTarget)) return
  draggingTaskKey.value = ''
}

async function applySheetFile(file: File | null, task: ResultWorkbenchTask, input?: HTMLInputElement) {
  if (!file || !task.editable) return
  const options = task.type === 'WEEK' ? weekOptions : monthOptions
  const extension = file.name.split('.').pop()?.toLowerCase() || ''
  const accepted = options.acceptedFileTypes.map((type) => type.toLowerCase())
  if (!accepted.includes(extension)) {
    ElMessage.warning('文件类型不支持')
    if (input) input.value = ''
    return
  }
  if (file.size > options.maxFileSizeMb * 1024 * 1024) {
    ElMessage.warning(`文件大小不能超过 ${options.maxFileSizeMb} MB`)
    if (input) input.value = ''
    return
  }
  const hasExistingEvidence = Boolean(drafts[task.key].file)
    || (task.type === 'WEEK' ? Boolean(task.weekResult?.evidences.length) : Boolean(task.monthVersion?.hasEvidence))
  if (hasExistingEvidence) {
    try {
      await ElMessageBox.confirm('当前已有证明材料，是否替换为新文件？', '替换证明材料', {
        type: 'warning',
        confirmButtonText: '替换',
        cancelButtonText: '取消',
      })
    } catch {
      if (input) input.value = ''
      return
    }
  }
  drafts[task.key].file = file
  scheduleTaskAutosave(task)
}

function onSheetFileChange(event: Event, task: ResultWorkbenchTask) {
  const input = event.target as HTMLInputElement
  void applySheetFile(input.files?.[0] || null, task, input)
}

function onSheetDrop(event: DragEvent, task: ResultWorkbenchTask) {
  draggingTaskKey.value = ''
  void applySheetFile(event.dataTransfer?.files?.[0] || null, task)
}

function removeSheetFile(task: ResultWorkbenchTask) {
  if (!task.editable) return
  drafts[task.key].file = null
  const input = document.getElementById(`sheet-result-file-${task.type}-${task.taskId}`) as HTMLInputElement | null
  if (input) input.value = ''
  scheduleTaskAutosave(task)
}

function saveTaskDraft(task: ResultWorkbenchTask | null, notify: boolean) {
  const draft = activeDraft.value
  const targetDraft = task ? drafts[task.key] : draft
  if (!task || !targetDraft) return false
  saveState.value = 'saving'
  try {
    localStorage.setItem(storageKey(task), serializeDraft(targetDraft))
    targetDraft.dirty = false
    lastSavedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
    saveState.value = 'saved'
    if (notify) ElMessage.success('草稿已保存')
    return true
  } catch {
    saveState.value = 'error'
    if (notify) ElMessage.error('草稿保存失败，请重试')
    return false
  }
}

function saveCurrentDraft(notify: boolean) { return saveTaskDraft(activeTask.value, notify) }

function validationIssues(task: ResultWorkbenchTask) {
  const draft = drafts[task.key]
  if (!draft) return ['草稿内容']
  const issues: string[] = []
  if (!draft.completionStatus.trim()) issues.push('完成情况')
  if (task.type === 'MONTH') {
    if (draft.personalPerformance == null) issues.push('个人整体绩效')
  } else if (!draft.deliverable.trim()) issues.push('实际交付物')
  return issues
}

function validateCurrentTask(task: ResultWorkbenchTask) {
  if (viewMode.value === 'FORM') {
    return Boolean(task.type === 'MONTH' ? monthEditorRef.value?.validate() : weekEditorRef.value?.validate())
  }
  const issues = validationIssues(task)
  if (issues.length) ElMessage.warning(`请先填写${issues.join('、')}`)
  return issues.length === 0
}

function selectTask(task: ResultWorkbenchTask) {
  if (activeTask.value?.key === task.key) return true
  if (activeDraft.value?.dirty && !saveCurrentDraft(false)) return false
  activeTaskKey.value = task.key
  void router.replace({ query: { ...route.query, taskId: String(task.taskId) } })
  return true
}

function focusSheetCell(event: MouseEvent) {
  const cell = event.currentTarget as HTMLElement | null
  const control = cell?.querySelector('textarea:not(:disabled), input:not(:disabled)') as HTMLTextAreaElement | HTMLInputElement | null
  control?.focus()
}

function moveTask(offset: number) {
  const next = activePackage.value?.tasks[activeTaskIndex.value + offset]
  if (next) selectTask(next)
}

function saveAndNext() { if (saveCurrentDraft(true)) moveTask(1) }
function backToWorkbench() { void router.push({ path: resultBasePath.value, query: { view: 'workbench', month: String(route.query.month || activePackage.value?.periodKey || currentMonthKey()) } }) }

function buildMonthPayload(task: ResultWorkbenchTask, draft: ResultTaskDraft, reviewId?: number) {
  const payload = new FormData()
  payload.append('monthPlanId', String(task.planId)); payload.append('monthPlanItemId', String(task.taskId)); payload.append('completionRate', '100')
  payload.append('completionStatus', draft.completionStatus.trim()); payload.append('description', (draft.description || draft.completionStatus).trim()); payload.append('remark', draft.remark.trim())
  payload.append('personalPerformance', String(draft.personalPerformance)); if (draft.file) payload.append('file', draft.file); if (reviewId) payload.append('aiReviewId', String(reviewId))
  return payload
}

function buildWeekPayload(draft: ResultTaskDraft) {
  const payload = new FormData()
  payload.append('completionRate', '100'); payload.append('completionStatus', draft.completionStatus.trim()); payload.append('content', draft.completionStatus.trim())
  payload.append('deliverable', draft.deliverable.trim()); payload.append('remark', draft.remark.trim()); if (draft.file) payload.append('file', draft.file)
  return payload
}

async function checkTask(task: ResultWorkbenchTask) {
  const draft = drafts[task.key]
  if (task.type !== 'MONTH' || !draft) return null
  const review = await previewResultAiReviewApi(buildMonthPayload(task, draft))
  draft.aiReview = review
  draft.aiReviewStale = false
  return review
}

async function checkCurrentTask() {
  const task = activeTask.value; if (!task || !validateCurrentTask(task)) return
  aiChecking.value = true
  try { await checkTask(task); ElMessage.success('AI成果检查已完成') }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : 'AI成果检查失败') }
  finally { aiChecking.value = false }
}

async function submitTask(task: ResultWorkbenchTask) {
  const draft = drafts[task.key]
  if (!draft) throw new Error('成果草稿不存在')
  if (task.type === 'MONTH') {
    const reviewId = draft.aiReview && !draft.aiReviewStale ? draft.aiReview.id : undefined
    await submitEmployeeResultApi(buildMonthPayload(task, draft, reviewId))
  } else {
    await submitWeekResultApi(task.planId, task.taskId, buildWeekPayload(draft))
  }
  localStorage.removeItem(storageKey(task))
}

async function submitCurrentTask() {
  const task = activeTask.value
  if (!task) return
  if (!validateCurrentTask(task)) return
  try { await ElMessageBox.confirm('确认提交当前任务成果？提交后将进入审批流程。', '提交成果', { type: 'warning', confirmButtonText: '确认提交', cancelButtonText: '继续编辑' }) } catch { return }
  submitting.value = true
  try { await submitTask(task); ElMessage.success('成果已提交'); await loadWorkspace() }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '成果提交失败') }
  finally { submitting.value = false }
}

function openBatchDialog() { batchResults.value = []; batchDialogVisible.value = true }
function goCompleteTask(task: ResultWorkbenchTask) { batchDialogVisible.value = false; selectTask(task); void nextTick(() => document.querySelector('.editor-pane')?.scrollTo({ top: 0 })) }
async function submitBatch() {
  batchSubmitting.value = true
  const results: BatchSubmitItemResult[] = []
  for (const task of batchReady.value) {
    try { await submitTask(task); results.push({ task, success: true, message: '已提交' }) }
    catch (error) { results.push({ task, success: false, message: error instanceof Error ? error.message : '提交失败' }) }
  }
  batchResults.value = results
  batchSubmitting.value = false
  await loadWorkspace()
}

function performancePrintTitle(value: string) {
  const match = /^(\d{4})-(\d{1,2})/.exec(value || '')
  return match ? `${match[1]} 年 ${Number(match[2])} 月绩效考核表` : `${value}绩效考核表`
}
function printNumber(value?: number) {
  const number = Number(value || 0)
  return number.toFixed(number % 1 === 0 ? 0 : 2)
}
function printMoney(value?: number) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN', { maximumFractionDigits: 2, useGrouping: false }) : ''
}
function printDateRange(start?: string, end?: string) { return [start, end].filter(Boolean).join(' 至 ') }
function hasPrintText(value?: string | null) { return String(value ?? '').trim().length > 0 }
function printFieldText(value?: string | null) { return String(value ?? '').trim() }
function taskPerformanceWeight(task: ResultWorkbenchTask) {
  return monthOptions.monthPlanItemOptions.find((item) => item.id === task.taskId)?.performanceWeight || 0
}
function taskEstimatedValue(task: ResultWorkbenchTask) {
  return monthOptions.monthPlanItemOptions.find((item) => item.id === task.taskId)?.estimatedValue || 0
}
function printCompletionStatus(task: ResultWorkbenchTask) { return drafts[task.key]?.completionStatus || task.monthVersion?.completionStatus || '' }
function printRemark(task: ResultWorkbenchTask) { return drafts[task.key]?.remark || task.monthVersion?.remark || '' }
function printCompletionRatio(task: ResultWorkbenchTask) {
  const ratio = Number(taskPerformanceWeight(task)) / 100
  return printRatio(ratio)
}
function printRatio(value: number) { return Number.isFinite(value) ? value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '') : '' }

async function downloadPreviewExcel() {
  const packageData = activePackage.value
  if (!packageData || packageData.type !== 'MONTH') return
  try {
    const blob = await downloadEmployeeResultExcelApi({
      monthPlanId: packageData.planId,
      rows: packageData.tasks.map((task) => ({
        monthPlanItemId: task.taskId,
        completionStatus: printCompletionStatus(task),
        remark: printRemark(task),
        personalPerformance: drafts[task.key]?.personalPerformance ?? task.monthVersion?.personalPerformance ?? null,
      })),
    })
    saveBlob(blob, `${printPlan.value?.employeeName || '员工'}-${packageData.periodKey}-成果考核表.xlsx`)
    ElMessage.success('Excel 已下载')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'Excel 下载失败')
  }
}

async function printPreview() {
  previewVisible.value = false
  if (activePackage.value?.type !== 'MONTH') return
  printVisible.value = true
  await nextTick()
  window.print()
}

function clearPrintSheet() { printVisible.value = false }

async function loadWorkspace() {
  if (!Number.isInteger(planId.value) || planId.value <= 0) { errorMessage.value = '成果包参数无效'; return }
  loading.value = true; errorMessage.value = ''
  try {
    const [months, weeks] = await Promise.all([getEmployeeResultSubmitOptionsApi(), getWeekResultSubmitOptionsApi()])
    Object.assign(monthOptions, months); Object.assign(weekOptions, weeks)
    const inferredMonth = packageType.value === 'MONTH'
      ? months.monthPlanOptions.find((plan) => plan.id === planId.value)?.planMonth
      : weeks.plans.find((plan) => plan.weekPlanId === planId.value)?.weekStart.slice(0, 7)
    const data = buildResultWorkbench(monthOptions, weekOptions, inferredMonth || String(route.query.month || currentMonthKey()))
    activePackage.value = data.packages.find((item) => item.type === packageType.value && item.planId === planId.value) || null
    if (!activePackage.value) throw new Error('成果包不存在或对应计划尚未审批通过')
    for (const task of activePackage.value.tasks) drafts[task.key] = initialDraft(task)
    const requestedTaskId = Number(route.query.taskId)
    const selected = activePackage.value.tasks.find((task) => task.taskId === requestedTaskId)
      || activePackage.value.tasks.find((task) => task.status === 'REJECTED')
      || activePackage.value.tasks.find((task) => task.editable)
      || activePackage.value.tasks[0]
    activeTaskKey.value = selected?.key || ''
    saveState.value = 'saved'; lastSavedAt.value = ''
  } catch (error) { errorMessage.value = error instanceof Error ? error.message : '成果包加载失败' }
  finally { loading.value = false }
}

onBeforeRouteLeave(() => {
  if (activeDraft.value?.dirty && !saveCurrentDraft(false)) return false
  return true
})
onBeforeUnmount(() => {
  if (autosaveTimer) clearTimeout(autosaveTimer)
  window.removeEventListener('afterprint', clearPrintSheet)
})
onMounted(() => {
  window.addEventListener('afterprint', clearPrintSheet)
  void loadWorkspace()
})
</script>

<style scoped>
.package-workspace { position:relative; width:100%; max-width:1680px; min-width:0; min-height:calc(100vh - 110px); margin:0 auto; overflow-x:hidden; color:#263a34; background:#fff; }
.workspace-header { display:flex; align-items:center; justify-content:space-between; gap:20px; min-height:58px; padding:6px 4px 10px; border-bottom:1px solid #d8e1dc; }.workspace-title-row { display:flex; align-items:center; gap:18px; min-width:0; }.workspace-title { display:grid; gap:3px; min-width:0; padding-top:1px; }.workspace-title > span { color:#2d776c; font-size:11px; font-weight:750; }.workspace-title strong { overflow:hidden; color:#30443d; font-size:15px; text-overflow:ellipsis; white-space:nowrap; }.workspace-title small { color:#708078; font-size:11px; }.workspace-header-actions { display:flex; align-items:center; gap:10px; flex-shrink:0; }.workspace-header-actions :deep(.el-segmented__item-label) { font-size:12px; }
.workspace-error,.workspace-loading { margin-top:18px; }.workspace-layout { display:grid; grid-template-columns:280px minmax(0,1fr); min-height:calc(100vh - 220px); border-bottom:1px solid #d8e1dc; }
.workspace-layout.is-sheet { grid-template-columns:minmax(0,1fr); }
.task-navigator { min-width:0; border-right:1px solid #d8e1dc; background:#f7f9f7; }.navigator-summary { display:flex; align-items:center; justify-content:space-between; min-height:54px; padding:0 14px; }.navigator-summary span { color:#718078; font-size:12px; }.task-filter { width:calc(100% - 20px); margin:0 10px 10px; --el-segmented-item-selected-bg-color:#fff; --el-segmented-item-selected-color:#2d776c; }.task-filter :deep(.el-segmented__group) { width:100%; }.task-filter :deep(.el-segmented__item) { min-width:0; padding-inline:4px; }.task-filter :deep(.el-segmented__item-label) { padding:0 5px; font-size:11px; }
.task-list { border-top:1px solid #d8e1dc; }.task-nav-item { display:grid; grid-template-columns:10px minmax(0,1fr) 18px; align-items:center; gap:10px; width:100%; min-height:66px; padding:9px 12px; border:0; border-bottom:1px solid #dde4df; color:#31473f; background:transparent; text-align:left; cursor:pointer; }.task-nav-item:hover { background:#f0f5f2; }.task-nav-item.is-active { background:#fff; box-shadow:inset 3px 0 #2d776c; }.task-state-dot { width:8px; height:8px; border-radius:50%; background:#97a29d; }.task-state-dot.is-rejected { background:#b64d45; }.task-state-dot.is-draft,.task-state-dot.is-not_started { background:#c17a33; }.task-state-dot.is-submitted { background:#4c79a8; }.task-state-dot.is-confirmed,.task-state-dot.is-approved { background:#2d776c; }.task-nav-copy { display:grid; min-width:0; gap:4px; }.task-nav-copy strong { display:-webkit-box; overflow:hidden; font-size:13px; line-height:1.45; -webkit-box-orient:vertical; -webkit-line-clamp:2; }.task-nav-copy small { color:#75847d; }
.editor-pane { min-width:0; max-height:calc(100vh - 220px); overflow:auto; }.workspace-action-bar { position:absolute; right:0; bottom:0; left:280px; z-index:5; display:grid; grid-template-columns:auto 1fr auto; align-items:center; min-height:66px; padding:9px 18px; border-top:1px solid #d8e1dc; background:rgba(255,255,255,.97); box-shadow:0 -5px 14px rgba(38,58,52,.05); }.task-step-actions,.primary-actions,.readonly-action { display:flex; align-items:center; gap:9px; }.save-state { justify-self:center; color:#6f7e77; font-size:12px; }.save-state.is-error { color:#b64d45; }.readonly-action span { color:#687870; font-size:13px; }
.workspace-layout.is-sheet .editor-pane { max-height:calc(100vh - 190px); }.workspace-layout.is-sheet .workspace-action-bar { left:0; }
.sheet-editor-pane { min-width:0; padding:18px 20px 92px; }.sheet-toolbar { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:12px; }.sheet-toolbar > div { display:grid; gap:3px; }.sheet-toolbar strong { color:#30443d; font-size:14px; }.sheet-toolbar span { color:#718078; font-size:12px; }.sheet-table-wrap { width:100%; max-width:100%; overflow-x:auto; overflow-y:hidden; border:1px solid #cbd7d0; box-sizing:border-box; overscroll-behavior-inline:contain; }.package-result-sheet { width:100%; min-width:0; border-collapse:collapse; table-layout:fixed; font-size:12px; }.package-result-sheet th,.package-result-sheet td { padding:8px; border-right:1px solid #d4ded8; border-bottom:1px solid #d4ded8; line-height:1.45; vertical-align:top; word-break:break-word; }.package-result-sheet th { color:#40584f; background:#edf3ef; font-weight:750; text-align:left; white-space:normal; }.package-result-sheet tbody tr { cursor:pointer; }.package-result-sheet tbody tr:hover,.package-result-sheet tbody tr.is-active { background:#f5faf7; }.package-result-sheet tbody tr.is-active { box-shadow:inset 3px 0 #2d776c; }.package-result-sheet tbody tr.is-readonly { background:#fafbfa; }.package-result-sheet td small { display:block; margin-top:3px; color:#809089; font-size:11px; }.package-result-sheet :deep(.el-textarea__inner) { min-height:54px!important; padding:6px 8px; border-color:#cdd9d2; box-shadow:none; font-size:12px; line-height:1.45; }.package-result-sheet :deep(.el-input-number) { width:100%; }.package-result-sheet :deep(.el-input-number .el-input__wrapper) { box-shadow:none; border:1px solid #cdd9d2; }.sheet-seq { width:4.5%; }.sheet-task { width:17%; }.sheet-plan { width:16%; }.sheet-completion { width:18%; }.sheet-result { width:18%; }.sheet-remark { width:16%; }.sheet-performance { width:10%; }.sheet-status { width:10.5%; }.package-result-sheet.is-month-sheet .sheet-seq { width:4%; }.package-result-sheet.is-month-sheet .sheet-task { width:15%; }.package-result-sheet.is-month-sheet .sheet-plan { width:14%; }.package-result-sheet.is-month-sheet .sheet-completion,.package-result-sheet.is-month-sheet .sheet-result { width:17%; }.package-result-sheet.is-month-sheet .sheet-remark { width:14%; }.package-result-sheet.is-month-sheet .sheet-performance { width:10%; }.package-result-sheet.is-month-sheet .sheet-status { width:9%; }.sheet-center { text-align:center; }.sheet-issue { color:#b46b2d!important; line-height:1.35; }
.batch-summary { display:grid; grid-template-columns:repeat(3,1fr); margin-bottom:18px; border:1px solid #d8e1dc; }.batch-summary div { display:grid; justify-items:center; gap:3px; padding:14px; border-right:1px solid #d8e1dc; }.batch-summary div:last-child { border-right:0; }.batch-summary strong { font-size:22px; }.batch-summary span { color:#718078; font-size:12px; }.batch-issues { border-top:1px solid #d8e1dc; }.batch-issues > div { display:flex; align-items:center; justify-content:space-between; min-height:54px; border-bottom:1px solid #e1e7e3; }.batch-issues span { display:grid; gap:3px; }.batch-issues small { color:#b16a2e; }.batch-results p { color:#9d4d46; }
.summary-preview-wrap { max-height:68vh; overflow:auto; }.summary-preview-table { width:100%; min-width:1000px; border-collapse:collapse; table-layout:fixed; font-size:12px; }.summary-preview-table th,.summary-preview-table td { padding:9px; border:1px solid #ced8d2; line-height:1.55; vertical-align:top; word-break:break-word; }.summary-preview-table th { background:#edf2ef; }.summary-preview-table th:first-child { width:48px; }.summary-preview-table tbody tr { cursor:pointer; }.summary-preview-table tbody tr:hover { background:#f4f8f6; }
.performance-preview-sheet { min-width:1040px; color:#1d2b25; font-family:"SimSun","Microsoft YaHei",sans-serif; }.performance-preview-sheet h2 { margin:0 auto 12px; color:#1f3029; font-size:20px; text-align:center; }.performance-preview-meta { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin-bottom:8px; font-size:12px; }.performance-preview-meta span { min-width:0; }.performance-preview-meta span:nth-child(2) { text-align:center; }.performance-preview-meta span:last-child { text-align:right; }.performance-preview-meta strong { display:inline-block; min-width:80px; padding:0 6px 2px; border-bottom:1px solid #52615a; font-weight:400; text-align:center; }.performance-preview-table { width:100%; border-collapse:collapse; table-layout:fixed; font-size:11px; line-height:1.45; }.performance-preview-table th,.performance-preview-table td { padding:6px 5px; border:1px solid #7d8b84; vertical-align:middle; overflow-wrap:anywhere; }.performance-preview-table th { background:#f1f4f2; text-align:center; }.performance-preview-table td:first-child,.performance-preview-table td:nth-child(3),.performance-preview-table td:nth-child(5),.performance-preview-table td:nth-child(8),.performance-preview-table td:nth-child(9) { text-align:center; }.performance-preview-table th:nth-child(1) { width:4%; }.performance-preview-table th:nth-child(2) { width:17%; }.performance-preview-table th:nth-child(3) { width:6%; }.performance-preview-table th:nth-child(4) { width:21%; }.performance-preview-table th:nth-child(5) { width:11%; }.performance-preview-table th:nth-child(6) { width:18%; }.performance-preview-table th:nth-child(7) { width:8%; }.performance-preview-table th:nth-child(8) { width:8%; }.performance-preview-table th:nth-child(9) { width:7%; }.performance-preview-table tfoot td { font-weight:700; text-align:center; }.performance-preview-confirmation { margin-top:14px; font-size:12px; line-height:1.7; }.performance-preview-confirmation p { margin:0 0 10px; }
.result-print-sheet { display:none; }
@media print {
  @page { size:A4 landscape; margin:10mm 8mm; }
  :global(html),:global(body),:global(#app),:global(.app-shell),:global(.main) { display:block!important; width:100%!important; height:auto!important; min-height:0!important; margin:0!important; padding:0!important; overflow:visible!important; background:#fff!important; }
  :global(.sidebar),:global(.topbar) { display:none!important; }
  :global(body *) { visibility:hidden!important; }
  :global(.package-workspace) { min-height:0!important; margin:0!important; padding:0!important; border:0!important; background:#fff!important; }
  :global(.package-workspace > :not(.result-print-sheet)) { display:none!important; }
  .result-print-sheet,.result-print-sheet * { visibility:visible!important; }
  .result-print-sheet { position:static; display:block; width:100%; margin:0; padding:0; color:#000; background:#fff; font-family:"SimSun","Microsoft YaHei",sans-serif; break-after:avoid; page-break-after:avoid; }
  .result-print-sheet h1 { width:max-content; max-width:100%; margin:0 auto 4px; padding:0 18px 1px; border-bottom:1px solid #000; font-size:18px; font-weight:700; line-height:1.25; text-align:center; }
  .result-print-sheet .performance-print-meta { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; margin:0 0 4px; font-size:10px; line-height:1.35; }
  .result-print-sheet .performance-print-meta span { display:flex; align-items:flex-end; gap:4px; min-width:0; }
  .result-print-sheet .performance-print-meta span:nth-child(2) { justify-content:center; text-align:center; }
  .result-print-sheet .performance-print-meta span:last-child { justify-content:flex-end; text-align:right; }
  .result-print-sheet .performance-print-meta strong { display:inline-block; min-width:76px; padding:0 8px 1px; border-bottom:1px solid #000; font-weight:400; line-height:1.1; text-align:center; }
  .result-print-sheet .performance-print-table { width:100%; border-collapse:collapse; table-layout:fixed; color:#000; font-size:8.5px; line-height:1.32; break-after:avoid; page-break-after:avoid; }
  .result-print-sheet .performance-print-table th,.result-print-sheet .performance-print-table td { padding:3px; border:1px solid #000; overflow-wrap:anywhere; vertical-align:middle; }
  .result-print-sheet .performance-print-table th { background:#fff; font-weight:700; text-align:center; }
  .result-print-sheet .performance-print-table td:nth-child(5) { white-space:nowrap; }
  .result-print-sheet .performance-print-table tbody tr { break-inside:avoid; page-break-inside:avoid; }
  .result-print-sheet .performance-print-table thead { display:table-header-group; }
  .result-print-sheet .performance-print-table tfoot { display:table-row-group; }
  .result-print-sheet .performance-print-table tfoot td { padding:2px 3px; font-weight:700; text-align:center; }
  .result-print-sheet .print-seq-column { width:4%; white-space:nowrap; }
  .result-print-sheet .print-work-column { width:17%; }
  .result-print-sheet .print-weight-column { width:5%; white-space:nowrap; }
  .result-print-sheet .print-standard-column { width:22%; }
  .result-print-sheet .print-standard-cell { line-height:1.28; }
  .result-print-sheet .print-standard-line { display:block; }
  .result-print-sheet .print-standard-line + .print-standard-line { margin-top:1px; }
  .result-print-sheet .print-date-column { width:10%; white-space:normal; }
  .result-print-sheet .print-completion-column { width:21%; }
  .result-print-sheet .print-remark-column { width:6%; }
  .result-print-sheet .print-value-column { width:8%; }
  .result-print-sheet .print-ratio-column { width:7%; }
  .result-print-sheet .print-center { text-align:center; }
  .result-print-sheet .performance-print-confirmation { margin-top:8px; font-size:10px; line-height:1.7; }
  .result-print-sheet .performance-print-confirmation p { margin:0 0 10px; font-size:10px; line-height:1.65; text-align:left; }
  .result-print-sheet .signature-row { display:grid; grid-template-columns:max-content 34mm 1fr; align-items:end; column-gap:4px; margin-top:8px; }
  .result-print-sheet .signature-line { display:block; height:1.2em; border-bottom:1px solid #000; }
  .result-print-sheet .signature-date { justify-self:center; min-width:42mm; text-align:left; }
}
@media (max-width:900px) { .workspace-header { align-items:flex-start; flex-direction:column; }.workspace-header-actions { width:100%; justify-content:space-between; }.workspace-layout.is-sheet .editor-pane { max-height:none; }.sheet-editor-pane { padding-inline:10px; }.package-result-sheet { font-size:11px; }.package-result-sheet th,.package-result-sheet td { padding:7px 6px; } }

/* Keep result entry aligned with the month-plan editor workbench. */
.package-workspace { --result-workspace-clearance:88px; padding-bottom:112px; background:#f6f7f5; }
.workspace-header { min-height:52px; padding:4px 0 10px; border-bottom-color:#d8e1dc; }
.workspace-title-row { gap:14px; }
.workspace-title strong { font-size:16px; }
.workspace-header-actions :deep(.el-segmented) { padding:3px; border:1px solid #d8e2dd; border-radius:7px; background:#eaf0ec; }
.workspace-header-actions :deep(.el-segmented__item) { min-height:32px; }
.workspace-header-actions :deep(.el-segmented__item-selected) { box-shadow:0 1px 4px rgb(48 73 64 / 14%); }
.workspace-layout { grid-template-columns:238px minmax(0,1fr); gap:16px; min-height:calc(100vh - 188px); border:0; }
.workspace-layout.is-sheet { grid-template-columns:minmax(0,1fr); }
.task-navigator { border:1px solid var(--line); border-radius:8px; background:#fff; }
.navigator-summary { min-height:50px; border-bottom:1px solid var(--line); }
.task-filter { margin-top:10px; }
.task-list { border-top:0; }
.task-nav-item { min-height:60px; border-bottom:1px solid #e3e9e5; }
.editor-pane { max-height:calc(100vh - 188px); overflow:auto; }
.editor-pane :deep(.task-editor) { max-width:none; margin:0; padding:20px 22px calc(var(--result-workspace-clearance) + 18px); border:1px solid var(--line); border-radius:8px; background:#fff; }
.editor-pane :deep(.task-editor-header) { padding-bottom:16px; }
.editor-pane :deep(.task-editor-header h2) { margin:4px 0 0; font-size:18px; }
.editor-pane :deep(.task-editor-header p) { font-size:12px; }
.editor-pane :deep(.task-context) { color:#2d776c; font-size:11px; font-weight:800; }
.editor-pane :deep(.task-facts) { padding:13px 0; }
.editor-pane :deep(.result-form) { padding-top:18px; }
.editor-pane :deep(.result-form .el-form-item) { margin-bottom:18px; }
.sheet-editor-pane { padding:16px 16px calc(var(--result-workspace-clearance) + 16px); border:1px solid var(--line); border-radius:8px; background:#f5f7f4; }
.sheet-toolbar { padding-bottom:4px; }
.sheet-toolbar strong { font-size:18px; }
.sheet-toolbar span { font-size:12px; }
.package-result-sheet { border-color:#cfdad4; background:#fff; }
.package-result-sheet th { min-height:46px; color:#40564f; background:#edf2ee; font-size:12px; }
.package-result-sheet td { background:transparent; }
.package-result-sheet tbody tr:hover,.package-result-sheet tbody tr.is-active { background:rgb(255 255 255 / 72%); }
.workspace-layout.is-sheet .editor-pane { max-height:calc(100vh - 178px); }
.workspace-action-bar { position:fixed; right:34px; bottom:0; left:302px; width:auto; max-width:var(--content-max,1540px); min-height:68px; margin-inline:auto; padding:10px 20px max(10px,env(safe-area-inset-bottom)); border-top:1px solid #cbd7d1; background:rgb(255 253 248 / 96%); box-shadow:0 -8px 24px rgb(35 57 49 / 9%); backdrop-filter:blur(14px); }
.workspace-layout.is-sheet .workspace-action-bar { right:34px; left:34px; }
.workspace-action-bar .primary-actions { gap:8px; }
.workspace-action-bar .el-button { min-height:38px; }
@media (max-width:1180px) { .workspace-layout { grid-template-columns:1fr; }.task-navigator { display:none; }.editor-pane { max-height:none; }.workspace-action-bar,.workspace-layout.is-sheet .workspace-action-bar { right:16px; left:16px; }.package-workspace { padding-bottom:150px; } }
@media (max-width:720px) { .package-workspace { --result-workspace-clearance:128px; padding-bottom:132px; }.workspace-header-actions { flex-wrap:wrap; }.workspace-header-actions :deep(.el-segmented) { flex:1; min-width:220px; }.workspace-header-actions :deep(.el-segmented__group) { width:100%; }.workspace-header-actions :deep(.el-segmented__item) { flex:1; }.editor-pane :deep(.task-editor) { padding:16px 16px calc(var(--result-workspace-clearance) + 14px); }.sheet-editor-pane { padding:10px 10px calc(var(--result-workspace-clearance) + 12px); }.sheet-toolbar span { display:none; }.package-result-sheet { min-width:720px; }.workspace-action-bar,.workspace-layout.is-sheet .workspace-action-bar { right:16px; left:16px; grid-template-columns:auto 1fr; }.workspace-action-bar .save-state { grid-column:1 / -1; grid-row:1; justify-self:start; }.workspace-action-bar .task-step-actions { grid-column:1; grid-row:2; }.workspace-action-bar .primary-actions,.workspace-action-bar .readonly-action { grid-column:2; grid-row:2; justify-content:flex-end; }.workspace-action-bar .primary-actions .el-button:nth-child(-n+2) { display:none; } }

/* Table editing is intentionally borderless so the controls read as cells, not nested cards. */
.package-result-sheet :deep(.el-input__wrapper),
.package-result-sheet :deep(.el-textarea__inner),
.package-result-sheet :deep(.el-input-number .el-input__wrapper) {
  --el-input-bg-color: transparent;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.package-result-sheet :deep(.el-input__wrapper) { min-height:30px; padding:0; }
.package-result-sheet :deep(.el-textarea__inner) { min-height:30px!important; padding:5px 0; overflow:hidden; resize:none; line-height:1.45; }
.package-result-sheet :deep(.el-input__inner),
.package-result-sheet :deep(.el-textarea__inner) { color:#263d36; font-size:13px; }
.package-result-sheet :deep(.el-input__inner::placeholder),
.package-result-sheet :deep(.el-textarea__inner::placeholder) { color:#9aaba4; opacity:1; }
.package-result-sheet :deep(.el-input-number) { width:100%; }
.package-result-sheet :deep(.el-input-number__increase),
.package-result-sheet :deep(.el-input-number__decrease) { display:none; }
.package-result-sheet :deep(.el-input-number .el-input__inner) { padding:0; text-align:left; }
.package-result-sheet td.sheet-editable-cell { position:relative; padding:0; }
.package-result-sheet td.sheet-editable-cell > :deep(.el-textarea),
.package-result-sheet td.sheet-editable-cell > :deep(.el-input),
.package-result-sheet td.sheet-editable-cell > :deep(.el-input-number) { position:absolute; inset:0; width:100%; height:100%; min-height:100%; }
.package-result-sheet td.sheet-editable-cell > :deep(.el-textarea) { display:flex; }
.package-result-sheet td.sheet-editable-cell > :deep(.el-textarea) .el-textarea__inner { flex:1; height:100%; }
.package-result-sheet td.sheet-editable-cell > :deep(.el-textarea__inner) { min-height:100%!important; box-sizing:border-box; cursor:text; }
.package-result-sheet td.sheet-editable-cell { cursor:text; }
.package-result-sheet td.sheet-editable-cell > :deep(.el-input-number) { display:flex; align-items:flex-start; }
.package-result-sheet td.sheet-editable-cell > :deep(.el-input-number) .el-input__wrapper { height:100%; }
.package-result-sheet td:focus-within { background:rgb(67 136 120 / 5%); }

/* Let the page own vertical scrolling; do not trap it inside the workbench. */
.package-workspace .editor-pane,
.package-workspace .workspace-layout.is-sheet .editor-pane { max-height:none; overflow:visible; }
.package-workspace .sheet-table-wrap { overflow-x:auto; overflow-y:hidden; }

/* Weekly result sheets include an evidence upload cell alongside the editable fields. */
.package-result-sheet:not(.is-month-sheet) .sheet-task { width:15%; }
.package-result-sheet:not(.is-month-sheet) .sheet-plan { width:14%; }
.package-result-sheet:not(.is-month-sheet) .sheet-completion { width:16%; }
.package-result-sheet:not(.is-month-sheet) .sheet-result { width:16%; }
.package-result-sheet:not(.is-month-sheet) .sheet-remark { width:14%; }
.package-result-sheet:not(.is-month-sheet) .sheet-evidence { width:12%; }
.package-result-sheet:not(.is-month-sheet) .sheet-status { width:8.5%; }
.package-result-sheet.is-month-sheet .sheet-task { width:13%; }
.package-result-sheet.is-month-sheet .sheet-plan { width:12%; }
.package-result-sheet.is-month-sheet .sheet-completion { width:15%; }
.package-result-sheet.is-month-sheet .sheet-result { width:15%; }
.package-result-sheet.is-month-sheet .sheet-remark { width:11%; }
.package-result-sheet.is-month-sheet .sheet-evidence { width:10%; }
.package-result-sheet.is-month-sheet .sheet-performance { width:10%; }
.package-result-sheet.is-month-sheet .sheet-status { width:8%; }
.sheet-evidence-cell { min-width:0; padding:6px 8px!important; vertical-align:top; }
.sheet-evidence-picker { display:inline-flex; align-items:center; justify-content:center; flex-direction:column; gap:4px; width:102px; min-height:60px; box-sizing:border-box; padding:7px 8px; border:1px solid #b7d0c5; border-radius:5px; color:#2d776c; background:#f8fbf9; font-size:13px; line-height:1.25; text-align:center; cursor:pointer; user-select:none; }
.sheet-evidence-picker .el-icon { font-size:17px; }
.sheet-evidence-picker.is-compact { flex-direction:row; width:auto; min-height:26px; padding:2px 0; border:0; background:transparent; font-size:12px; }
.sheet-evidence-picker.is-compact .el-icon { font-size:14px; }
.sheet-evidence-picker.is-disabled { color:#9aa8a2; border-color:#d7e0db; cursor:not-allowed; }
.sheet-evidence-picker.is-dragging { border-color:#2d776c; background:#eaf4f0; box-shadow:0 0 0 2px rgb(45 119 108 / 12%); }
.package-result-sheet tbody tr.is-dragging { background:#eaf4f0; }
.sheet-evidence-selected { display:flex; align-items:center; gap:3px; min-width:0; margin-top:3px; }
.sheet-evidence-name { min-width:0; overflow:hidden; color:#52665e; font-size:11px; text-overflow:ellipsis; white-space:nowrap; }
.sheet-evidence-selected button { display:grid; flex:none; width:20px; height:20px; place-items:center; padding:0; border:0; color:#708078; background:transparent; cursor:pointer; }
.sheet-evidence-existing { display:block; margin-top:4px; color:#75847d; font-size:11px; }
.package-result-sheet input.visually-hidden { position:absolute!important; width:1px!important; height:1px!important; margin:-1px!important; padding:0!important; overflow:hidden!important; clip:rect(0,0,0,0)!important; white-space:nowrap!important; border:0!important; }
.package-result-sheet.is-month-sheet { width:100%; min-width:1120px; }
.package-result-sheet.is-month-sheet .sheet-seq { width:4%; }
.package-result-sheet.is-month-sheet .sheet-task { width:14%; }
.package-result-sheet.is-month-sheet .sheet-weight { width:5%; }
.package-result-sheet.is-month-sheet th.sheet-standard { width:15%; }
.package-result-sheet.is-month-sheet .sheet-date { width:8%; }
.package-result-sheet.is-month-sheet .sheet-completion { width:13%; }
.package-result-sheet.is-month-sheet .sheet-remark { width:8%; }
.package-result-sheet.is-month-sheet .sheet-value { width:7%; }
.package-result-sheet.is-month-sheet .sheet-performance { width:9%; }
.package-result-sheet.is-month-sheet .sheet-evidence { width:10%; }
.package-result-sheet.is-month-sheet .sheet-status { width:7%; }
.package-result-sheet.is-month-sheet td > .sheet-standard { width:auto; }
.sheet-standard { min-width:0; max-width:100%; color:#2f453d; font-size:13px; font-weight:400; line-height:1.6; writing-mode:horizontal-tb; text-orientation:mixed; word-break:normal!important; overflow-wrap:break-word; white-space:pre-wrap; }
.sheet-value { color:#263d36; font-variant-numeric:tabular-nums; }
.package-result-sheet tbody tr:not(.is-readonly) td.sheet-editable-cell { background:#f4faf6; }
.package-result-sheet tbody tr:not(.is-readonly) td.sheet-editable-cell:hover { background:#eaf5ef; }
.package-result-sheet tbody tr:not(.is-readonly) td.sheet-editable-cell:focus-within { background:#e4f1ea; }
.package-result-sheet tbody tr.is-readonly td { color:#7a8781; background:#f3f5f3; }
.package-result-sheet tbody tr.is-readonly td.sheet-editable-cell { background:#edf0ed; }
.package-result-sheet tbody tr.is-readonly td.sheet-editable-cell :deep(.el-input__inner),
.package-result-sheet tbody tr.is-readonly td.sheet-editable-cell :deep(.el-textarea__inner) { color:#8b9690; }
</style>
