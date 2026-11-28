import { http } from './http'

export type PlanStatus = 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED' | 'PAUSED' | 'CANCELED'

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

export function listMonthPlansApi(params?: Record<string, unknown>) {
  return http.get<unknown, MonthPlan[]>('/planning/month-plans', { params })
}

export function listDayPlansApi(params?: Record<string, unknown>) {
  return http.get<unknown, DayPlan[]>('/planning/day-plans', { params })
}
