import type { EmployeeResultSubmitOptionsResp } from '@/api/employee'

export type MonthArchiveStatus = 'REJECTED' | 'SUBMITTED' | 'CONFIRMED' | 'PARTIAL'
export type MonthArchiveTaskStatus = 'REJECTED' | 'SUBMITTED' | 'CONFIRMED' | 'NOT_SUBMITTED'
export type MonthResultVersion = EmployeeResultSubmitOptionsResp['resultVersions'][number]
export type MonthPlanItemOption = EmployeeResultSubmitOptionsResp['monthPlanItemOptions'][number]

export interface MonthResultArchiveTask extends MonthPlanItemOption {
  key: string
  status: MonthArchiveTaskStatus
  latestVersion?: MonthResultVersion
}

export interface MonthResultArchive {
  key: string
  type: 'MONTH'
  planId: number
  planMonth: string
  title: string
  employeeName: string
  projectName: string
  positionName: string
  status: MonthArchiveStatus
  updatedAt: string
  counts: {
    total: number
    result: number
    confirmed: number
    rejected: number
  }
  tasks: MonthResultArchiveTask[]
  searchText: string
}

function newestVersion(left: MonthResultVersion, right: MonthResultVersion) {
  return String(right.submittedAt || '').localeCompare(String(left.submittedAt || '')) || right.id - left.id
}

function latestVersions(options: EmployeeResultSubmitOptionsResp) {
  const byTask = new Map<string, MonthResultVersion>()
  for (const version of [...options.resultVersions].sort(newestVersion)) {
    if (!version.monthPlanItemId || ['draft', 'not_submitted'].includes(version.status)) continue
    const key = `${version.monthPlanId}:${version.monthPlanItemId}`
    if (!byTask.has(key)) byTask.set(key, version)
  }
  return byTask
}

function taskStatus(version?: MonthResultVersion): MonthArchiveTaskStatus {
  if (version?.status === 'rejected') return 'REJECTED'
  if (version?.status === 'submitted') return 'SUBMITTED'
  if (version?.status === 'confirmed') return 'CONFIRMED'
  return 'NOT_SUBMITTED'
}

function archiveStatus(tasks: MonthResultArchiveTask[]): MonthArchiveStatus {
  if (tasks.some((task) => task.status === 'REJECTED')) return 'REJECTED'
  if (tasks.some((task) => task.status === 'SUBMITTED')) return 'SUBMITTED'
  if (tasks.length && tasks.every((task) => task.status === 'CONFIRMED')) return 'CONFIRMED'
  return 'PARTIAL'
}

export function monthArchiveStatusMeta(status: MonthArchiveStatus) {
  return ({
    REJECTED: { label: '有退回', type: 'danger' as const },
    SUBMITTED: { label: '审批中', type: 'primary' as const },
    CONFIRMED: { label: '已确认', type: 'success' as const },
    PARTIAL: { label: '部分完成', type: 'info' as const },
  })[status]
}

export function buildMonthResultArchives(options: EmployeeResultSubmitOptionsResp): MonthResultArchive[] {
  const versions = latestVersions(options)
  return options.monthPlanOptions.flatMap((plan) => {
    const tasks = options.monthPlanItemOptions
      .filter((item) => item.monthPlanId === plan.id)
      .sort((left, right) => left.sortNo - right.sortNo || left.id - right.id)
      .map<MonthResultArchiveTask>((item) => {
        const latestVersion = versions.get(`${plan.id}:${item.id}`)
        return {
          ...item,
          key: `MONTH:${plan.id}:${item.id}`,
          latestVersion,
          status: taskStatus(latestVersion),
        }
      })

    if (!tasks.some((task) => task.latestVersion)) return []

    const resultTasks = tasks.filter((task) => task.latestVersion)
    const submittedTimes = resultTasks.map((task) => task.latestVersion?.submittedAt || '').sort()
    const updatedAt = submittedTimes[submittedTimes.length - 1] || ''
    const searchText = [
      plan.title,
      plan.employeeName,
      plan.projectName,
      ...tasks.flatMap((task) => [
        task.taskName,
        task.taskContent,
        task.latestVersion?.description || '',
        task.latestVersion?.completionStatus || '',
      ]),
    ].join(' ').toLowerCase()

    return [{
      key: `MONTH:${plan.id}`,
      type: 'MONTH' as const,
      planId: plan.id,
      planMonth: plan.planMonth,
      title: plan.title || `${plan.planMonth}月度成果`,
      employeeName: plan.employeeName,
      projectName: plan.projectName,
      positionName: plan.positionName,
      status: archiveStatus(tasks),
      updatedAt,
      counts: {
        total: tasks.length,
        result: resultTasks.length,
        confirmed: tasks.filter((task) => task.status === 'CONFIRMED').length,
        rejected: tasks.filter((task) => task.status === 'REJECTED').length,
      },
      tasks,
      searchText,
    }]
  }).sort((left, right) => right.updatedAt.localeCompare(left.updatedAt) || right.planId - left.planId)
}

export function resolveMonthArchivePlanId(
  options: EmployeeResultSubmitOptionsResp,
  routeId: number,
  scope?: unknown,
) {
  if (scope === 'plan' && options.monthPlanOptions.some((plan) => plan.id === routeId)) return routeId
  return options.resultVersions.find((version) => version.id === routeId)?.monthPlanId ?? null
}
