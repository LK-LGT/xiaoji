<template>
  <main class="month-archive-detail">
    <header class="archive-header">
      <div class="command-row">
        <el-button class="back-command" text :icon="ArrowLeft" @click="backToArchive">返回成果档案</el-button>
        <div class="header-actions">
          <el-button v-if="archive" :icon="Printer" :disabled="detailsLoading" @click="printArchive">打印/下载</el-button>
          <el-button v-if="hasRejectedTask" type="primary" @click="handleRejection">前往处理退回</el-button>
        </div>
      </div>
      <div v-if="archive" class="title-block">
        <h1>{{ archive.title }}</h1>
        <div class="archive-identity">
          <el-tag :type="archiveStatus.type" effect="plain">{{ archiveStatus.label }}</el-tag>
          <span>月度成果档案</span>
          <span aria-hidden="true">·</span>
          <span>{{ monthText(archive.planMonth) }}</span>
        </div>
        <div class="archive-meta">
          <span><small>姓名</small>{{ archive.employeeName || '--' }}</span>
          <span><small>项目</small>{{ archive.projectName || '--' }}</span>
          <span><small>岗位</small>{{ archive.positionName || '--' }}</span>
          <span><small>最后更新</small>{{ dateTime(archive.updatedAt) }}</span>
        </div>
      </div>
    </header>

    <el-alert v-if="pageError" class="page-alert" type="warning" :closable="false" show-icon :title="pageError">
      <el-button link type="primary" @click="loadArchive">重新加载</el-button>
    </el-alert>
    <el-skeleton v-if="loading" class="page-skeleton" :rows="10" animated />

    <template v-else-if="archive">
      <section class="archive-summary" aria-label="档案摘要">
        <div><span>计划任务</span><strong>{{ archive.counts.total }}</strong></div>
        <div><span>已有成果</span><strong>{{ archive.counts.result }}</strong></div>
        <div><span>已确认</span><strong>{{ archive.counts.confirmed }}</strong></div>
        <div><span>个人绩效合计</span><strong>{{ moneyText(performanceTotal) }}</strong></div>
      </section>

      <el-alert v-if="failedTasks.length" class="detail-warning" type="warning" :closable="false" show-icon :title="`${failedTasks.length} 项成果详情加载失败，其他任务已正常展示`">
        <el-button link type="primary" :icon="Refresh" :loading="retrying" @click="retryFailedDetails">重试失败项</el-button>
      </el-alert>

      <nav class="detail-tabs" aria-label="档案详情视图">
        <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ 'is-active': activeTab === tab.value }" @click="activeTab = tab.value">{{ tab.label }}</button>
      </nav>

      <section v-if="activeTab === 'content'" class="task-ledger">
        <header class="ledger-heading">
          <div><h2>月度任务成果</h2><p>完整记录计划目标、执行成果与证明材料</p></div>
          <span>{{ archive.counts.result }} / {{ archive.counts.total }} 项已有成果</span>
        </header>

        <article v-for="task in archive.tasks" :key="task.key" class="archive-task">
          <header class="task-heading">
            <span class="task-index">{{ String(task.sortNo).padStart(2, '0') }}</span>
            <div>
              <h3>{{ task.taskName }}</h3>
              <MonthPlanTaskContentView class="task-heading-content" :content="task.taskContent" empty-text="未填写计划内容" />
            </div>
            <el-tag size="small" effect="plain" :type="taskStatusMeta(task).type">{{ taskStatusMeta(task).label }}</el-tag>
          </header>

          <div class="task-plan-grid">
            <div><span>权重</span><strong>{{ numberText(task.performanceWeight) }}%</strong></div>
            <div><span>计划周期</span><strong>{{ periodText(task) }}</strong></div>
            <div><span>预估价值</span><strong>{{ moneyText(task.estimatedValue) }}</strong></div>
            <div class="task-standard"><span>考核标准</span><strong>{{ standardText(task) }}</strong></div>
          </div>

          <div v-if="!task.latestVersion" class="task-empty-result">未提交成果</div>
          <div v-else-if="taskState(task).state === 'failed'" class="task-load-error">
            <el-icon><WarningFilled /></el-icon><span>成果详情加载失败</span>
          </div>
          <el-skeleton v-else-if="taskState(task).state === 'loading'" class="task-loading" :rows="3" animated />
          <div v-else class="task-result-grid">
            <div><span>完成情况</span><p>{{ taskState(task).detail?.completionStatus || '无' }}</p></div>
            <div><span>实际成果</span><p>{{ taskState(task).detail?.description || '无' }}</p></div>
            <div><span>备注</span><p>{{ taskState(task).detail?.remark || '无' }}</p></div>
            <div><span>个人绩效</span><p class="result-number">{{ optionalMoneyText(taskState(task).detail?.personalPerformance) }}</p></div>
          </div>

          <section v-if="taskState(task).detail" class="task-evidence">
            <h4>证明材料</h4>
            <div v-if="taskState(task).detail?.evidences.length" class="evidence-list">
              <div v-for="file in taskState(task).detail?.evidences" :key="file.id">
                <span>{{ file.fileName }}</span><small>{{ file.fileType }} · {{ fileSize(file.fileSize) }}</small>
                <el-button link type="primary" :icon="Download" @click="downloadEvidence(task, file.id, file.fileName)">下载</el-button>
              </div>
            </div>
            <p v-else class="evidence-empty">未上传证明材料</p>
          </section>
        </article>
      </section>

      <section v-else class="record-view">
        <div class="record-toolbar">
          <span>查看任务</span>
          <el-select v-model="selectedTaskId" aria-label="选择成果任务">
            <el-option v-for="task in resultTasks" :key="task.id" :label="task.taskName" :value="task.id" />
          </el-select>
        </div>
        <ResultApprovalTimeline v-if="activeTab === 'approval'" :events="approvalEvents" />
        <ResultVersionHistory v-else :versions="versions" @compare="compareVersion" />
      </section>

      <EmployeeMonthResultPrintSheet :archive="archive" :details="printDetails" :failed-task-ids="failedTaskIds" :printed-at="printedAt" />
    </template>
  </main>
</template>

<script setup lang="ts">
import { ArrowLeft, Download, Printer, Refresh, WarningFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  downloadEmployeeResultEvidenceApi,
  getEmployeeResultDetailApi,
  getEmployeeResultSubmitOptionsApi,
  type EmployeeResultDetailResp,
  type EmployeeResultSubmitOptionsResp,
} from '@/api/employee'
import { saveBlob } from '@/utils/download'
import { formatMonthPlanPeriod, formatMonthPlanStandard } from '@/utils/monthPlanPeriod'
import { formatPeriodLabel } from '@/utils/period'
import {
  buildMonthResultArchives,
  monthArchiveStatusMeta,
  resolveMonthArchivePlanId,
  type MonthResultArchive,
  type MonthResultArchiveTask,
} from '../resultArchiveModel'
import EmployeeMonthResultPrintSheet from './EmployeeMonthResultPrintSheet.vue'
import ResultApprovalTimeline from './ResultApprovalTimeline.vue'
import ResultVersionHistory from './ResultVersionHistory.vue'
import MonthPlanTaskContentView from '@/components/MonthPlanTaskContentView.vue'

type DetailTab = 'content' | 'approval' | 'history'
type TaskLoadState = 'idle' | 'loading' | 'loaded' | 'failed'
interface TaskDetailState { state: TaskLoadState; detail: EmployeeResultDetailResp | null; error: string }

const route = useRoute()
const router = useRouter()
const resultBasePath = computed(() => route.path.startsWith('/leader/') ? '/leader/results' : '/employee/results')
const loading = ref(false)
const retrying = ref(false)
const pageError = ref('')
const activeTab = ref<DetailTab>('content')
const archive = ref<MonthResultArchive | null>(null)
const options = ref<EmployeeResultSubmitOptionsResp | null>(null)
// BUG-R6-14：任务 / 成果 ID 是字符串主键，不是数字。
const selectedTaskId = ref<string | null>(null)
const taskDetails = reactive(new Map<string, TaskDetailState>())
const printedAt = ref('')
const tabs = [{ label: '成果内容', value: 'content' as const }, { label: '审批记录', value: 'approval' as const }, { label: '历史版本', value: 'history' as const }]

const archiveStatus = computed(() => monthArchiveStatusMeta(archive.value?.status || 'PARTIAL'))
const resultTasks = computed(() => archive.value?.tasks.filter((task) => task.latestVersion) || [])
const selectedTask = computed(() => resultTasks.value.find((task) => task.id === selectedTaskId.value) || resultTasks.value[0] || null)
const selectedDetail = computed(() => selectedTask.value ? taskDetails.get(selectedTask.value.id)?.detail || null : null)
const failedTasks = computed(() => archive.value?.tasks.filter((task) => taskDetails.get(task.id)?.state === 'failed') || [])
const failedTaskIds = computed(() => failedTasks.value.map((task) => task.id))
const detailsLoading = computed(() => [...taskDetails.values()].some((item) => item.state === 'loading'))
const hasRejectedTask = computed(() => archive.value?.tasks.some((task) => task.status === 'REJECTED') || false)
const performanceTotal = computed(() => [...taskDetails.values()].reduce((sum, item) => sum + Number(item.detail?.personalPerformance || 0), 0))
const printDetails = computed<Record<string, EmployeeResultDetailResp>>(() => Object.fromEntries(
  [...taskDetails.entries()].flatMap(([taskId, value]) => value.detail ? [[taskId, value.detail]] : []),
))
const approvalEvents = computed(() => {
  const detail = selectedDetail.value
  if (!detail) return []
  return [
    { label: '员工提交', operatorName: '本人', occurredAt: detail.submittedAt, state: 'SUBMITTED', current: detail.status === 'submitted' },
    ...(detail.suggestedAt ? [{ label: '领导处理', operatorName: '直属领导', occurredAt: detail.suggestedAt, comment: detail.leaderSuggestion, state: detail.status === 'rejected' ? 'REJECTED' : 'APPROVED', current: false }] : []),
    ...(detail.confirmedAt ? [{ label: '部门确认', operatorName: '部门负责人', occurredAt: detail.confirmedAt, comment: detail.confirmComment, state: 'CONFIRMED', current: false }] : []),
  ]
})
const versions = computed(() => {
  const task = selectedTask.value
  if (!task || !options.value) return []
  return options.value.resultVersions
    .filter((version) => version.monthPlanId === archive.value?.planId && version.monthPlanItemId === task.id && !['draft', 'not_submitted'].includes(version.status))
    // BUG-R6-14：成果 ID 是字符串，不能做数值减法。
    .sort((left, right) => String(right.submittedAt).localeCompare(String(left.submittedAt)) || String(right.id).localeCompare(String(left.id)))
    .map((version) => ({ resultId: version.id, versionNo: version.versionNo, submittedAt: dateTime(version.submittedAt), changeSummary: version.status === 'rejected' ? '成果退回' : version.status === 'confirmed' ? '成果确认' : '提交成果', rejectionComment: version.status === 'rejected' ? version.leaderSuggestion : '', current: version.id === task.latestVersion?.id }))
})

function taskState(task: MonthResultArchiveTask): TaskDetailState { return taskDetails.get(task.id) || { state: 'idle', detail: null, error: '' } }
function taskStatusMeta(task: MonthResultArchiveTask) { return ({ REJECTED: { label: '已退回', type: 'danger' as const }, SUBMITTED: { label: '审批中', type: 'primary' as const }, CONFIRMED: { label: '已确认', type: 'success' as const }, NOT_SUBMITTED: { label: '未提交', type: 'info' as const } })[task.status] }
// ⚠️ **BUG-R6-26**：统一走 `formatPeriodLabel`（唯一权威实现）。
function monthText(value: string) { return formatPeriodLabel(value, value) }
function dateTime(value?: string) { return value ? value.replace('T', ' ').slice(0, 16) : '--' }
function numberText(value?: number) { return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }
function moneyText(value?: number) { return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }
function optionalMoneyText(value?: number) { return value == null ? '未填写' : moneyText(value) }
function periodText(task: MonthResultArchiveTask) { return formatMonthPlanPeriod(task.estimatedStartDate, task.estimatedEndDate, '--') }
function standardText(task: MonthResultArchiveTask) { return formatMonthPlanStandard(task.deliverable, task.quality, '--') }
function fileSize(value?: number) { if (!value) return '未知大小'; if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`; return `${(value / 1024 / 1024).toFixed(2)} MB` }

async function loadTaskDetails(tasks: MonthResultArchiveTask[]) {
  const pending = tasks.filter((task) => task.latestVersion)
  pending.forEach((task) => taskDetails.set(task.id, { state: 'loading', detail: taskDetails.get(task.id)?.detail || null, error: '' }))
  const results = await Promise.allSettled(pending.map((task) => getEmployeeResultDetailApi(task.latestVersion!.id)))
  results.forEach((result, index) => {
    const task = pending[index]
    if (result.status === 'fulfilled') taskDetails.set(task.id, { state: 'loaded', detail: result.value, error: '' })
    else taskDetails.set(task.id, { state: 'failed', detail: null, error: result.reason instanceof Error ? result.reason.message : '成果详情加载失败' })
  })
}

async function loadArchive() {
  loading.value = true
  pageError.value = ''
  taskDetails.clear()
  try {
    options.value = await getEmployeeResultSubmitOptionsApi()
    const planId = resolveMonthArchivePlanId(options.value, String(route.params.resultId), route.query.archiveScope)
    archive.value = buildMonthResultArchives(options.value).find((item) => item.planId === planId) || null
    if (!archive.value) throw new Error('未找到对应的月度成果档案')
    selectedTaskId.value = archive.value.tasks.find((task) => task.latestVersion)?.id || null
    await loadTaskDetails(archive.value.tasks)
  } catch (error) {
    archive.value = null
    pageError.value = error instanceof Error ? error.message : '月度成果档案加载失败'
  } finally {
    loading.value = false
  }
}

async function retryFailedDetails() {
  if (!failedTasks.value.length) return
  retrying.value = true
  await loadTaskDetails(failedTasks.value)
  retrying.value = false
}

function backToArchive() {
  const keys = ['view', 'archiveType', 'archiveMonth', 'archiveStatus', 'archiveKeyword', 'archivePage'] as const
  const query = Object.fromEntries(keys.flatMap((key) => route.query[key] == null ? [] : [[key, route.query[key]]]))
  void router.push({ path: resultBasePath.value, query: { ...query, view: 'archive' } })
}
function handleRejection() {
  const task = selectedTask.value?.status === 'REJECTED' ? selectedTask.value : archive.value?.tasks.find((item) => item.status === 'REJECTED')
  if (!archive.value || !task) return
  void router.push({ path: `${resultBasePath.value}/workspace/MONTH/${archive.value.planId}`, query: { taskId: String(task.id), month: archive.value.planMonth } })
}
async function downloadEvidence(task: MonthResultArchiveTask, evidenceId: string, fileName: string) {
  const resultId = taskDetails.get(task.id)?.detail?.id
  if (!resultId) return
  try { saveBlob(await downloadEmployeeResultEvidenceApi(resultId, evidenceId), fileName) }
  catch (error) { ElMessage.error(error instanceof Error ? error.message : '附件下载失败') }
}
async function printArchive() {
  if (!archive.value || detailsLoading.value) return
  printedAt.value = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'long', day: 'numeric' }).format(new Date())
  await nextTick()
  window.print()
}
function compareVersion() { ElMessage.info('当前版本暂无可比较的内容差异') }

onMounted(loadArchive)
</script>

<style scoped>
.month-archive-detail { width:100%; max-width:1400px; min-height:calc(100vh - 132px); margin:0 auto; padding:16px 24px 64px; box-sizing:border-box; color:#263a34; background:#fff; }
.archive-header { padding-bottom:20px; border-bottom:1px solid #d8e1dc; }
.command-row { display:flex; align-items:center; justify-content:space-between; min-height:40px; gap:16px; }
.back-command { margin-left:-12px; }
.header-actions { display:flex; align-items:center; gap:8px; }
.title-block { padding-top:14px; }
.title-block h1 { margin:0; color:#20342d; font-size:24px; line-height:1.4; letter-spacing:0; overflow-wrap:anywhere; }
.archive-identity { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-top:10px; color:#61736b; font-size:13px; }
.archive-meta { display:flex; align-items:center; flex-wrap:wrap; gap:10px 28px; margin-top:14px; color:#314941; font-size:13px; }
.archive-meta span { display:flex; align-items:baseline; gap:8px; }
.archive-meta small { color:#7b8983; font-size:12px; }
.page-alert,.page-skeleton,.detail-warning { margin-top:18px; }
.archive-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); min-height:82px; margin-top:18px; border-top:1px solid #d8e1dc; border-bottom:1px solid #d8e1dc; background:#f6f8f6; }
.archive-summary > div { display:flex; align-items:center; justify-content:center; flex-direction:column; gap:5px; min-width:0; padding:12px; }
.archive-summary > div + div { border-left:1px solid #d8e1dc; }
.archive-summary span { color:#718078; font-size:12px; }
.archive-summary strong { color:#20342d; font-size:20px; line-height:1.2; font-variant-numeric:tabular-nums; }
.detail-tabs { display:flex; gap:28px; min-height:54px; margin-top:8px; border-bottom:1px solid #d8e1dc; }
.detail-tabs button { position:relative; padding:0 6px; border:0; color:#66776f; background:transparent; font-size:15px; font-weight:650; letter-spacing:0; cursor:pointer; }
.detail-tabs button::after { position:absolute; right:0; bottom:-1px; left:0; height:3px; background:#2d776c; content:''; opacity:0; }
.detail-tabs button.is-active { color:#1e5f56; }
.detail-tabs button.is-active::after { opacity:1; }
.task-ledger { padding-top:22px; }
.ledger-heading { display:flex; align-items:flex-end; justify-content:space-between; gap:20px; padding-bottom:12px; }
.ledger-heading h2 { margin:0; color:#294139; font-size:18px; line-height:1.4; }
.ledger-heading p { margin:4px 0 0; color:#75847d; font-size:12px; }
.ledger-heading > span { color:#52665e; font-size:13px; font-variant-numeric:tabular-nums; }
.archive-task { border-top:1px solid #cfdad4; }
.archive-task:last-child { border-bottom:1px solid #cfdad4; }
.task-heading { display:grid; grid-template-columns:42px minmax(0,1fr) auto; align-items:start; gap:12px; padding:16px 12px; background:#f3f6f3; }
.task-index { color:#2d776c; font-size:15px; font-weight:750; font-variant-numeric:tabular-nums; }
.task-heading h3 { margin:0; color:#20342d; font-size:15px; line-height:1.45; overflow-wrap:anywhere; }
.task-heading-content { margin-top:4px; color:#65766e; font-size:13px; line-height:1.6; overflow-wrap:anywhere; }
.task-plan-grid { display:grid; grid-template-columns:110px 210px 130px minmax(260px,1fr); border-bottom:1px solid #e0e6e2; }
.task-plan-grid > div { min-width:0; padding:12px; }
.task-plan-grid > div + div { border-left:1px solid #e0e6e2; }
.task-plan-grid span,.task-result-grid span { display:block; color:#75847d; font-size:11px; }
.task-plan-grid strong { display:block; margin-top:5px; color:#314941; font-size:13px; line-height:1.55; font-weight:600; overflow-wrap:anywhere; }
.task-result-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)) 150px; border-bottom:1px solid #e0e6e2; }
.task-result-grid > div { min-width:0; padding:14px 12px 16px; }
.task-result-grid > div + div { border-left:1px solid #e0e6e2; }
.task-result-grid p { margin:7px 0 0; color:#20342d; font-size:13px; line-height:1.7; overflow-wrap:anywhere; white-space:pre-wrap; }
.task-result-grid .result-number { font-weight:700; font-variant-numeric:tabular-nums; }
.task-empty-result,.task-load-error { display:flex; align-items:center; justify-content:center; gap:7px; min-height:76px; border-bottom:1px solid #e0e6e2; color:#75847d; font-size:13px; }
.task-load-error { color:#b5473c; background:#fff8f7; }
.task-loading { padding:14px 12px; border-bottom:1px solid #e0e6e2; }
.task-evidence { padding:12px; }
.task-evidence h4 { margin:0 0 8px; color:#52665e; font-size:12px; }
.evidence-list { border-top:1px solid #e0e6e2; }
.evidence-list > div { display:grid; grid-template-columns:minmax(0,1fr) 180px 72px; align-items:center; min-height:44px; border-bottom:1px solid #e0e6e2; }
.evidence-list > div > span { min-width:0; color:#294139; overflow-wrap:anywhere; }
.evidence-list small,.evidence-empty { color:#75847d; }
.evidence-list :deep(.el-button) { justify-self:end; }
.evidence-empty { margin:0; padding:8px 0; }
.record-view { padding-top:18px; }
.record-toolbar { display:flex; align-items:center; gap:12px; padding:12px 16px; border-bottom:1px solid #d8e1dc; background:#f6f8f6; }
.record-toolbar > span { color:#52665e; font-size:13px; }
.record-toolbar :deep(.el-select) { width:min(420px,100%); }
@media (max-width:1040px) {
  .month-archive-detail { padding-right:18px; padding-left:18px; }
  .task-plan-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
  .task-standard { grid-column:1 / -1; border-top:1px solid #e0e6e2; border-left:0 !important; }
  .task-result-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .task-result-grid > div:nth-child(3) { border-top:1px solid #e0e6e2; border-left:0; }
  .task-result-grid > div:nth-child(4) { border-top:1px solid #e0e6e2; }
}
@media (max-width:680px) {
  .month-archive-detail { padding:10px 12px 40px; }
  .command-row { align-items:flex-start; }
  .header-actions { flex-wrap:wrap; justify-content:flex-end; }
  .title-block h1 { font-size:21px; }
  .archive-summary { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .archive-summary > div:nth-child(3) { border-top:1px solid #d8e1dc; border-left:0; }
  .archive-summary > div:nth-child(4) { border-top:1px solid #d8e1dc; }
  .detail-tabs { gap:10px; justify-content:space-between; }
  .detail-tabs button { min-width:0; font-size:14px; }
  .ledger-heading { align-items:flex-start; flex-direction:column; gap:6px; }
  .task-heading { grid-template-columns:34px minmax(0,1fr); }
  .task-heading :deep(.el-tag) { grid-column:2; justify-self:start; }
  .task-plan-grid,.task-result-grid { grid-template-columns:1fr; }
  .task-plan-grid > div + div,.task-result-grid > div + div { border-top:1px solid #e0e6e2; border-left:0; }
  .task-standard { grid-column:auto; }
  .evidence-list > div { grid-template-columns:minmax(0,1fr) auto; gap:4px 10px; padding:8px 0; }
  .evidence-list small { grid-column:1; }
  .evidence-list :deep(.el-button) { grid-column:2; grid-row:1 / 3; }
  .record-toolbar { align-items:flex-start; flex-direction:column; }
  .record-toolbar :deep(.el-select) { width:100%; }
}
</style>
