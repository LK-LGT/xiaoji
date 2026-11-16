import type {
  EmployeeMonthPlanItem,
  EmployeePlanStatus,
  EmployeeResultStatus,
  EmployeeResultSubmitOptionsResp,
} from '@/api/employee'

export type MonthPlanResultVersion = Pick<
  EmployeeResultSubmitOptionsResp['resultVersions'][number],
  'monthPlanItemId' | 'status'
>

export function resultVersionForTask(
  versions: readonly MonthPlanResultVersion[],
  taskId: string,
) {
  // BUG-R6-14：任务 ID 是字符串主键。
  return versions.find((version) => version.monthPlanItemId === taskId)
}

export function canSubmitTaskResult(
  planStatus: EmployeePlanStatus,
  task: EmployeeMonthPlanItem,
  resultStatus: EmployeeResultStatus | undefined,
  resultStateLoaded: boolean,
) {
  return resultStateLoaded
    && planStatus === 'approved'
    && task.status === 'approved'
    && (!resultStatus || resultStatus === 'rejected')
}

export function firstPendingResultTask(
  planStatus: EmployeePlanStatus,
  tasks: readonly EmployeeMonthPlanItem[],
  versions: readonly MonthPlanResultVersion[],
  resultStateLoaded: boolean,
) {
  return tasks.find((task) => {
    const version = resultVersionForTask(versions, task.id)
    return canSubmitTaskResult(planStatus, task, version?.status, resultStateLoaded)
  })
}
