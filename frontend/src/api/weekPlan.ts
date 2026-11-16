import { http } from './http'
import type { PageResponse } from './performance'

export type WeekPlanStatus = 'DRAFT' | 'PENDING' | 'APPROVED' | 'REJECTED'

export interface WeekPlanParentOption {
  monthPlanItemId: number
  monthPlanId: number
  monthPlanTitle: string
  planMonth: string
  taskType: 'REGULAR'
  performanceWeight: number
  taskName: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  status: string
  existingWeekPlanCount: number
}

export interface WeekPlanItem {
  id: number
  monthPlanItemId: number | null
  content: string
  completionStatus?: string
  deliverable?: string
  quality?: string
  remark?: string
  plannedFinishDate?: string
  sortNo: number
  parent?: WeekPlanParentOption
}

export interface WeekPlanSummary {
  id: number
  title: string
  weekStart: string
  weekEnd: string
  status: WeekPlanStatus
  versionNo: number
  ownerUserId: number
  employeeName: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  departmentName: string
  itemCount: number
  submitAt?: string
  approveAt?: string
  approvalComment?: string
}

export interface WeekPlanDetail {
  summary: WeekPlanSummary
  items: WeekPlanItem[]
  siblingPlans: WeekPlanSummary[]
  dayPlanCount: number
}

export interface ProjectWeekPlanMemberProgress {
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  positionName: string
  planStatus: WeekPlanStatus | 'NOT_CREATED'
  statusLabel: string
  approvedAt?: string
}

export interface ProjectWeekPlanPackage {
  /** 项目组 ID（飞书 department_id，string）。 */
  groupId: string
  groupName: string
  weekStart: string
  weekEnd: string
  approvedCount: number
  totalMemberCount: number
  status: 'EMPTY' | 'COLLECTING' | 'OPEN'
  contentAvailable: boolean
  pendingMembers: ProjectWeekPlanMemberProgress[]
  completedAt?: string
}

export interface ProjectWeekPlanPrintRow {
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  workContent: string
  completionStatus?: string
  deliverable?: string
  quality?: string
}

export interface ProjectWeekPlanPackageDetail {
  packageInfo: ProjectWeekPlanPackage
  rows: ProjectWeekPlanPrintRow[]
}

export interface WeekPlanSavePayload {
  weekStart: string
  versionNo?: number
  items: Array<{
    monthPlanItemId: number | null
    content: string
    deliverable?: string
    quality?: string
    plannedFinishDate?: string
  }>
  recordVersion?: boolean
  versionRequestId?: string
  restoredFromVersionId?: number
}

export interface WeekPlanAction {
  id: number
  status: WeekPlanStatus
  versionNo: number
  message: string
}

export interface WeekPlanListQuery {
  status?: WeekPlanStatus | ''
  weekStart?: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  pageNo?: number
  pageSize?: number
}

export function listEmployeeWeekPlansApi(params?: WeekPlanListQuery) {
  return http.get<unknown, WeekPlanSummary[]>('/employee/week-plans', { params })
}

export function getEmployeeWeekPlanApi(id: number) {
  return http.get<unknown, WeekPlanDetail>(`/employee/week-plans/${id}`)
}

export function listWeekPlanParentOptionsApi() {
  return http.get<unknown, WeekPlanParentOption[]>('/employee/week-plans/parent-options')
}

export function createEmployeeWeekPlanApi(payload: WeekPlanSavePayload) {
  return http.post<WeekPlanSavePayload, WeekPlanDetail>('/employee/week-plans', payload)
}

export function updateEmployeeWeekPlanApi(id: number, payload: WeekPlanSavePayload) {
  return http.put<WeekPlanSavePayload, WeekPlanDetail>(`/employee/week-plans/${id}`, payload)
}

export function submitEmployeeWeekPlanApi(id: number, versionNo: number) {
  return http.post<undefined, WeekPlanAction>(`/employee/week-plans/${id}/submit`, undefined, { params: { versionNo } })
}

export function withdrawEmployeeWeekPlanApi(id: number, versionNo: number) {
  return http.post<undefined, WeekPlanAction>(`/employee/week-plans/${id}/withdraw`, undefined, { params: { versionNo } })
}

export function deleteEmployeeWeekPlanApi(id: number, versionNo: number) {
  return http.delete<unknown, WeekPlanAction>(`/employee/week-plans/${id}`, { params: { versionNo } })
}

export function listLeaderWeekPlansApi(params?: WeekPlanListQuery) {
  return http.get<unknown, WeekPlanSummary[]>('/leader/week-plan-approvals', { params })
}

export function getLeaderWeekPlanApi(id: number) {
  return http.get<unknown, WeekPlanDetail>(`/leader/week-plan-approvals/${id}`)
}

export function approveLeaderWeekPlanApi(id: number, versionNo: number, comment: string) {
  return http.post<unknown, WeekPlanAction>(`/leader/week-plan-approvals/${id}/approve`, { versionNo, comment })
}

export function rejectLeaderWeekPlanApi(id: number, versionNo: number, comment: string) {
  return http.post<unknown, WeekPlanAction>(`/leader/week-plan-approvals/${id}/reject`, { versionNo, comment })
}

export function listLeaderProjectWeekPlanPackagesApi(params?: { scopeOrgId?: string; weekStart?: string }) {
  return http.get<unknown, ProjectWeekPlanPackage[]>('/leader/project-week-plan-packages', { params })
}

export function getLeaderProjectWeekPlanPackageApi(groupId: string, params?: { weekStart?: string }) {
  return http.get<unknown, ProjectWeekPlanPackageDetail>(`/leader/project-week-plan-packages/${groupId}`, { params })
}

export function downloadLeaderProjectWeekPlanExcelApi(groupId: string, params?: { weekStart?: string }) {
  return http.get<unknown, Blob>(`/leader/project-week-plan-packages/${groupId}/excel`, {
    params,
    responseType: 'blob',
  })
}

export function listDepartmentWeekPlanApprovalsApi(params?: WeekPlanListQuery) {
  return http.get<unknown, WeekPlanSummary[]>('/department/week-plan-approvals', { params })
}

export function getDepartmentWeekPlanApprovalApi(id: number) {
  return http.get<unknown, WeekPlanDetail>(`/department/week-plan-approvals/${id}`)
}

export function approveDepartmentWeekPlanApi(id: number, versionNo: number, comment: string) {
  return http.post<unknown, WeekPlanAction>(`/department/week-plan-approvals/${id}/approve`, { versionNo, comment })
}

export function rejectDepartmentWeekPlanApi(id: number, versionNo: number, comment: string) {
  return http.post<unknown, WeekPlanAction>(`/department/week-plan-approvals/${id}/reject`, { versionNo, comment })
}

export function listDepartmentWeekPlansApi(params?: WeekPlanListQuery) {
  return http.get<unknown, PageResponse<WeekPlanSummary>>('/department/week-plans', { params })
}

export function getDepartmentWeekPlanApi(id: number) {
  return http.get<unknown, WeekPlanDetail>(`/department/week-plans/${id}`)
}

export const weekPlanStatusMeta: Record<WeekPlanStatus, { label: string; type: 'info' | 'warning' | 'success' | 'danger' }> = {
  DRAFT: { label: '草稿', type: 'info' },
  PENDING: { label: '待审批', type: 'warning' },
  APPROVED: { label: '已通过', type: 'success' },
  REJECTED: { label: '已驳回', type: 'danger' },
}
