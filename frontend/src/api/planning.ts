import { http } from './http'

export type PlanStatus = 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED' | 'PAUSED' | 'CANCELED'
export type ResultStatus = 'DRAFT' | 'PENDING' | 'CONFIRMED' | 'REJECTED'

export interface MonthPlan {
  /** BUG-R6-14：主键为字符串（UUID/十六进制），不可转数字。 */
  id: string
  title: string
  planMonth: string
  completionRate?: number
  versionNo?: string
  /** BUG-R6-14：员工 ID 为字符串（飞书 open_id / user_id）。 */
  ownerUserId: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  status: PlanStatus
  submitAt?: string
  approverId?: string
  approveAt?: string
  approvalComment?: string
  reviewStatus?: string
  riskLevel?: string
  reviewedAt?: string
}

export interface DayPlan {
  /** BUG-R6-14：主键为字符串（UUID/十六进制），不可转数字。 */
  id: string
  title: string
  planDate: string
  content: string
  monthPlanId?: string
  ownerUserId: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  status: PlanStatus
  submitAt?: string
  approverId?: string
  approveAt?: string
  approvalComment?: string
  departmentReviewComment?: string
  reviewStatus?: string
  riskLevel?: string
  reviewedAt?: string
}

export interface ResultItem {
  /** BUG-R6-14：主键为字符串（UUID/十六进制），不可转数字。 */
  id: string
  title: string
  resultDate: string
  content: string
  planType: 'DAY' | 'MONTH' | 'TEMP'
  planId?: string
  temporary: boolean
  temporaryReason?: string
  ownerUserId: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  status: ResultStatus
  submitAt?: string
  confirmerId?: string
  confirmAt?: string
  confirmComment?: string
  leaderSuggestion?: string
  completionRate?: number
  versionNo?: string
  evidenceStatus?: string
}

export interface PlanningStats {
  pendingDayPlans: number
  overduePendingDayPlans: number
  currentMonthResults: number
  closureRate: string
  /** 统计周期 YYYY-MM */
  period?: string
  /** 周期内已审批通过的成果数 */
  confirmedResults?: number
  /** 周期内被驳回的成果数 */
  rejectedResults?: number
  /** 计划执行率（0~100，按条目绩效权重加权） */
  planExecutionRate?: number | null
  /** 统计范围内的员工数 */
  scopedEmployeeCount?: number
  detail?: {
    submittedResults?: number
    expectedItems?: number
    scopedEmployeeIds?: string[]
  }
}

export function getPlanningStatsApi() {
  return http.get<unknown, PlanningStats>('/planning/stats')
}

export function listMonthPlansApi(params?: Record<string, unknown>) {
  return http.get<unknown, MonthPlan[]>('/planning/month-plans', { params })
}

export function listDayPlansApi(params?: Record<string, unknown>) {
  return http.get<unknown, DayPlan[]>('/planning/day-plans', { params })
}

export function listResultsApi(params?: Record<string, unknown>) {
  return http.get<unknown, ResultItem[]>('/planning/results', { params })
}
