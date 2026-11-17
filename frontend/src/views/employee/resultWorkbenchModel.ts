import type {
  EmployeeResultStatus,
  EmployeeResultSubmitOptionsResp,
  EmployeeResultVersionResp,
} from '@/api/employee'
import type { WeekResultRecord, WeekResultSubmitOptions } from '@/api/weekResult'
import type { AiReview } from '@/api/aiReview'
import { resultStatusMeta, resultStatusRank } from './resultStatus.ts'
import { periodMonthKey } from '@/utils/period'

export type ResultPackageType = 'MONTH' | 'WEEK'
export type ResultWorkbenchStatus = 'NOT_STARTED' | 'DRAFT' | 'REJECTED' | 'SUBMITTED' | 'CONFIRMED' | 'APPROVED' | 'LOCKED'
export type ResultPrimaryAction = 'START' | 'CONTINUE' | 'HANDLE_REJECTION' | 'VIEW_PROGRESS' | 'VIEW_RESULT' | 'NONE'

export interface ResultWorkbenchTask {
  key: string
  type: ResultPackageType
  /**
   * 计划主键。BUG-R6-14：主键是**十六进制 / UUID 字符串**，
   * 不是数字 —— 早期 TS 声明为 `number` 纯属历史遗留，
   * 任何 `Number(...)` 强转都会把它塌成 `0`。
   */
  planId: string
  taskId: string
  planTitle: string
  periodKey: string
  periodLabel: string
  taskName: string
  taskContent: string
  plannedDeliverable: string
  quality: string
  startDate?: string
  endDate?: string
  status: ResultWorkbenchStatus
  editable: boolean
  latestResultId?: string
  monthVersion?: EmployeeResultSubmitOptionsResp['resultVersions'][number]
  weekResult?: WeekResultRecord | null
}

export interface ResultPackageCounts {
  total: number
  pending: number
  draft: number
  rejected: number
  submitted: number
  completed: number
}

export interface ResultWorkbenchPackage {
  key: string
  type: ResultPackageType
  /** 计划主键（字符串，见 `ResultWorkbenchTask.planId`）。 */
  planId: string
  periodKey: string
  title: string
  startDate?: string
  endDate?: string
  status: ResultWorkbenchStatus
  primaryAction: ResultPrimaryAction
  counts: ResultPackageCounts
  tasks: ResultWorkbenchTask[]
  alertText: string
}

export interface ResultWorkbenchData {
  month: string
  packages: ResultWorkbenchPackage[]
  crossPeriodAlerts: ResultWorkbenchPackage[]
  totals: ResultPackageCounts
}

export interface BatchSubmitItemResult {
  task: ResultWorkbenchTask
  success: boolean
  message: string
}

export interface ResultTaskDraft {
  completionStatus: string
  description: string
  deliverable: string
  remark: string
  personalPerformance: number | null
  file: File | null
  aiReview: AiReview | null
  aiReviewStale: boolean
  dirty: boolean
}

export function statusMeta(status: ResultWorkbenchStatus) {
  const meta = resultStatusMeta(status)
  if (status === 'LOCKED') return { ...meta, label: '只读' }
  return meta
}

export function primaryActionLabel(action: ResultPrimaryAction) {
  return ({
    START: '开始填写',
    CONTINUE: '继续填写',
    HANDLE_REJECTION: '处理退回',
    VIEW_PROGRESS: '查看进度',
    VIEW_RESULT: '查看成果',
    NONE: '查看',
  })[action]
}

export function sortTasks<T extends Pick<ResultWorkbenchTask, 'status' | 'taskId'>>(tasks: T[]) {
  // BUG-R6-14：taskId 是字符串，不能做数值减法（NaN 比较不稳定）。
  // 改为按状态优先级 + 字符串字典序，保证排序稳定。
  return [...tasks].sort((left, right) => resultStatusRank(left.status) - resultStatusRank(right.status)
    || String(left.taskId).localeCompare(String(right.taskId)))
}

export function sortPackages<T extends Pick<ResultWorkbenchPackage, 'status' | 'startDate'>>(packages: T[]) {
  return [...packages].sort((left, right) => resultStatusRank(left.status) - resultStatusRank(right.status)
    || String(right.startDate || '').localeCompare(String(left.startDate || '')))
}

function monthStatus(status?: EmployeeResultStatus): ResultWorkbenchStatus {
  return ({ draft: 'DRAFT', submitted: 'SUBMITTED', confirmed: 'CONFIRMED', rejected: 'REJECTED', not_submitted: 'NOT_STARTED' } as const)[status || 'not_submitted']
}

function weekStatus(status?: string): ResultWorkbenchStatus {
  return ({ NOT_SUBMITTED: 'NOT_STARTED', SUBMITTED: 'SUBMITTED', APPROVED: 'APPROVED', REJECTED: 'REJECTED' } as const)[status || 'NOT_SUBMITTED'] || 'LOCKED'
}

function latestMonthVersion(
  versions: EmployeeResultSubmitOptionsResp['resultVersions'],
  planId: string,
  taskId: string,
) {
  // BUG-R6-14：主键是字符串，版本号按字符串字典序倒排（UUID 无大小序语义，
  // 但版本号在 `versionNo` 上单独可比，这里保持稳定排序即可）。
  return versions
    .filter((version) => String(version.monthPlanId) === planId && String(version.monthPlanItemId) === taskId)
    .sort((left, right) => String(right.id).localeCompare(String(left.id)))[0]
}

function countTasks(tasks: ResultWorkbenchTask[]): ResultPackageCounts {
  return {
    total: tasks.length,
    pending: tasks.filter((task) => task.status === 'NOT_STARTED' || task.status === 'DRAFT' || task.status === 'REJECTED').length,
    draft: tasks.filter((task) => task.status === 'DRAFT').length,
    rejected: tasks.filter((task) => task.status === 'REJECTED').length,
    submitted: tasks.filter((task) => task.status === 'SUBMITTED').length,
    completed: tasks.filter((task) => task.status === 'CONFIRMED' || task.status === 'APPROVED').length,
  }
}

function packageState(tasks: ResultWorkbenchTask[]) {
  if (tasks.some((task) => task.status === 'REJECTED')) return { status: 'REJECTED' as const, action: 'HANDLE_REJECTION' as const }
  if (tasks.some((task) => task.status === 'DRAFT')) return { status: 'DRAFT' as const, action: 'CONTINUE' as const }
  if (tasks.some((task) => task.status === 'NOT_STARTED')) return { status: 'NOT_STARTED' as const, action: 'START' as const }
  if (tasks.some((task) => task.status === 'SUBMITTED')) return { status: 'SUBMITTED' as const, action: 'VIEW_PROGRESS' as const }
  if (tasks.every((task) => task.status === 'CONFIRMED')) return { status: 'CONFIRMED' as const, action: 'VIEW_RESULT' as const }
  if (tasks.every((task) => task.status === 'APPROVED')) return { status: 'APPROVED' as const, action: 'VIEW_RESULT' as const }
  return { status: 'LOCKED' as const, action: 'NONE' as const }
}

function packageAlert(counts: ResultPackageCounts) {
  if (counts.rejected) return `${counts.rejected}项退回待修改`
  if (counts.pending) return `${counts.pending}项待完善`
  if (counts.submitted) return `${counts.submitted}项等待审批`
  return counts.completed ? '本周期成果已完成' : ''
}

function monthLabel(month: string) {
  const [year, value] = month.split('-')
  return `${year}年${Number(value)}月`
}

export function buildResultWorkbench(
  monthOptions: EmployeeResultSubmitOptionsResp,
  weekOptions: WeekResultSubmitOptions,
  selectedMonth: string,
): ResultWorkbenchData {
  const allPackages: ResultWorkbenchPackage[] = []

  for (const plan of monthOptions.monthPlanOptions) {
    const tasks = monthOptions.monthPlanItemOptions
      .filter((item) => item.monthPlanId === plan.id)
      .map<ResultWorkbenchTask>((item) => {
        const version = latestMonthVersion(monthOptions.resultVersions, plan.id, item.id)
        const status = monthStatus(version?.status)
        return {
          key: `MONTH:${plan.id}:${item.id}`,
          type: 'MONTH',
          planId: plan.id,
          taskId: item.id,
          planTitle: plan.title,
          periodKey: plan.planMonth,
          periodLabel: monthLabel(plan.planMonth),
          taskName: item.taskName,
          taskContent: item.taskContent,
          plannedDeliverable: item.deliverable,
          quality: item.quality,
          startDate: item.estimatedStartDate,
          endDate: item.estimatedEndDate,
          status,
          editable: statusMeta(status).editable,
          latestResultId: version?.id,
          monthVersion: version,
        }
      })
    if (!tasks.length) continue
    const counts = countTasks(tasks)
    const state = packageState(tasks)
    allPackages.push({
      key: `MONTH:${plan.id}`,
      type: 'MONTH',
      planId: plan.id,
      periodKey: plan.planMonth,
      title: `${monthLabel(plan.planMonth)}绩效成果`,
      startDate: `${plan.planMonth}-01`,
      endDate: (() => { const dates = tasks.map((task) => task.endDate || '').sort(); return dates[dates.length - 1] || undefined })(),
      status: state.status,
      primaryAction: state.action,
      counts,
      tasks: sortTasks(tasks),
      alertText: packageAlert(counts),
    })
  }

  // BUG-R6-14：周计划主键也是字符串，Map 键用 `string`。
  const weekGroups = new Map<string, typeof weekOptions.plans>()
  for (const option of weekOptions.plans) {
    const weekPlanId = String(option.weekPlanId)
    const group = weekGroups.get(weekPlanId)
    if (group) group.push(option)
    else weekGroups.set(weekPlanId, [option])
  }
  for (const [planId, options] of weekGroups) {
    const first = options[0]
    const tasks = options.map<ResultWorkbenchTask>((option) => {
      const status = weekStatus(option.latestResult?.status)
      return {
        key: `WEEK:${planId}:${option.task.id}`,
        type: 'WEEK',
        planId,
        taskId: option.task.id,
        planTitle: option.weekPlanTitle,
        // ⚠️ **BUG-R6-26**：统一走 `periodMonthKey`（不再手写 `slice(0, 7)`）。
        periodKey: periodMonthKey(option.weekStart),
        periodLabel: `${option.weekStart} 至 ${option.weekEnd}`,
        taskName: option.task.content,
        taskContent: option.task.content,
        plannedDeliverable: option.task.deliverable || '',
        quality: option.task.quality || '',
        startDate: option.weekStart,
        endDate: option.weekEnd,
        status,
        editable: statusMeta(status).editable,
        latestResultId: option.latestResult?.id,
        weekResult: option.latestResult,
      }
    })
    const counts = countTasks(tasks)
    const state = packageState(tasks)
    allPackages.push({
      key: `WEEK:${planId}`,
      type: 'WEEK',
      planId,
      periodKey: periodMonthKey(first.weekStart),
      title: first.weekPlanTitle || `${first.weekStart}周执行成果`,
      startDate: first.weekStart,
      endDate: first.weekEnd,
      status: state.status,
      primaryAction: state.action,
      counts,
      tasks: sortTasks(tasks),
      alertText: packageAlert(counts),
    })
  }

  const packages = sortPackages(allPackages.filter((item) => item.periodKey === selectedMonth))
  const crossPeriodAlerts = sortPackages(allPackages.filter((item) => item.periodKey !== selectedMonth && item.status === 'REJECTED'))
  return {
    month: selectedMonth,
    packages,
    crossPeriodAlerts,
    totals: countTasks(packages.flatMap((item) => item.tasks)),
  }
}

export function summarizeSubmitResults(items: BatchSubmitItemResult[]) {
  return {
    requested: items.length,
    succeeded: items.filter((item) => item.success).length,
    failed: items.filter((item) => !item.success).length,
    failedTaskKeys: items.filter((item) => !item.success).map((item) => item.task.key),
  }
}

export function resultVersionLabel(version?: EmployeeResultVersionResp | null) {
  return version?.versionNo || '未提交'
}
