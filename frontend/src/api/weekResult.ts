import { http } from './http'
import type { PageResponse } from './performance'

export interface WeekResultTask {
  id: number
  weekPlanId: number
  monthPlanItemId: number | null
  content: string
  deliverable?: string
  quality?: string
  plannedFinishDate?: string
  sortNo: number
}

export interface WeekResultEvidence {
  id: number
  fileName: string
  fileType: string
  fileSize: number
  checksum: string
  downloadUrl: string
  createdAt: string
}

export interface WeekResultRecord {
  id: number
  weekPlanId: number
  weekPlanItemId: number
  resultDate: string
  title: string
  content: string
  completionStatus: string
  deliverable: string
  remark: string
  completionRate: number
  evidenceStatus: string
  status: string
  submitAt: string
  approverId?: number
  approveAt?: string
  approvalComment?: string
  evidences: WeekResultEvidence[]
}

export interface WeekResultSubmitContext {
  weekPlanId: number
  weekPlanTitle: string
  weekStart: string
  weekEnd: string
  weekPlanStatus: string
  task: WeekResultTask
  latestResult?: WeekResultRecord | null
  acceptedFileTypes: string[]
  maxFileSizeMb: number
}

export interface WeekResultSubmitAction {
  id: number
  status: string
  submittedAt: string
  message: string
}

export interface WeekResultSubmitOption {
  weekPlanId: number
  weekPlanTitle: string
  weekStart: string
  weekEnd: string
  weekPlanStatus: string
  task: WeekResultTask
  latestResult?: WeekResultRecord | null
}

export interface WeekResultSubmitOptions {
  plans: WeekResultSubmitOption[]
  acceptedFileTypes: string[]
  maxFileSizeMb: number
}

export interface WeekResultApprovalItem {
  id: number
  weekPlanId: number
  ownerUserId: number
  employeeNo: string
  employeeName: string
  groupName: string
  weekStart: string
  weekEnd: string
  workContent: string
  planDeliverable: string
  quality: string
  completionStatus: string
  deliverable: string
  remark: string
  evidenceStatus: string
  status: 'NOT_SUBMITTED' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'
  submitAt: string
  approverId?: number
  approveAt?: string
  approvalComment?: string
  supervisorResult: boolean
  evidences: WeekResultEvidence[]
}

export interface ProjectWeekResultMemberProgress {
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  positionName: string
  resultStatus: string
  statusLabel: string
  approvedAt?: string
}

export interface ProjectWeekResultPackage {
  /** 项目组 ID（飞书 department_id，string）。 */
  groupId: string
  groupName: string
  weekStart: string
  weekEnd: string
  approvedCount: number
  totalMemberCount: number
  status: 'EMPTY' | 'COLLECTING' | 'OPEN'
  contentAvailable: boolean
  pendingMembers: ProjectWeekResultMemberProgress[]
  completedAt?: string
}

export interface ProjectWeekResultPrintRow {
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  workContent: string
  completionStatus: string
  deliverable: string
  remark: string
}

export interface ProjectWeekResultPackageDetail {
  packageInfo: ProjectWeekResultPackage
  rows: ProjectWeekResultPrintRow[]
}

export interface WeekResultApprovalQuery {
  weekStart?: string
  status?: '' | 'NOT_SUBMITTED' | 'SUBMITTED' | 'APPROVED' | 'REJECTED'
  keyword?: string
  pageNo?: number
  pageSize?: number
}

export function getWeekResultSubmitOptionsApi() {
  return http.get<unknown, WeekResultSubmitOptions>('/employee/week-results/submit/options')
}

export function getEmployeeWeekResultDetailApi(resultId: number) {
  return http.get<unknown, WeekResultRecord>(`/employee/week-results/${resultId}`)
}

export function downloadWeekResultEvidenceApi(resultId: number, evidenceId: number) {
  return http.get<unknown, Blob>(`/employee/week-results/${resultId}/evidence/${evidenceId}`, { responseType: 'blob' })
}

export function getWeekResultSubmitContextApi(weekPlanId: number, itemId: number) {
  return http.get<unknown, WeekResultSubmitContext>(`/employee/week-plans/${weekPlanId}/items/${itemId}/results/submit-context`)
}

export function submitWeekResultApi(weekPlanId: number, itemId: number, payload: FormData) {
  return http.post<FormData, WeekResultSubmitAction>(`/employee/week-plans/${weekPlanId}/items/${itemId}/results/submit`, payload, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function listLeaderWeekResultApprovalsApi(params?: WeekResultApprovalQuery) {
  return http.get<unknown, PageResponse<WeekResultApprovalItem>>('/leader/week-result-approvals', { params })
}

export function listDepartmentWeekResultApprovalsApi(params?: WeekResultApprovalQuery) {
  return http.get<unknown, PageResponse<WeekResultApprovalItem>>('/department/week-result-approvals', { params })
}

export function approveLeaderWeekResultApi(id: number, comment?: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/leader/week-result-approvals/${id}/approve`, { comment })
}

export function rejectLeaderWeekResultApi(id: number, comment: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/leader/week-result-approvals/${id}/reject`, { comment })
}

export function approveDepartmentWeekResultApi(id: number, comment?: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/department/week-result-approvals/${id}/approve`, { comment })
}

export function rejectDepartmentWeekResultApi(id: number, comment: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/department/week-result-approvals/${id}/reject`, { comment })
}

export function downloadWeekResultApprovalEvidenceApi(
  workspace: 'leader' | 'department', resultId: number, evidenceId: number,
) {
  return http.get<unknown, Blob>(`/${workspace}/week-result-approvals/${resultId}/evidence/${evidenceId}`, {
    responseType: 'blob',
  })
}

export function listLeaderProjectWeekResultPackagesApi(params?: { scopeOrgId?: string; weekStart?: string }) {
  return http.get<unknown, ProjectWeekResultPackage[]>('/leader/project-week-result-packages', { params })
}

export function getLeaderProjectWeekResultPackageApi(groupId: string, params?: { weekStart?: string }) {
  return http.get<unknown, ProjectWeekResultPackageDetail>(`/leader/project-week-result-packages/${groupId}`, { params })
}

export function downloadLeaderProjectWeekResultExcelApi(groupId: string, params?: { weekStart?: string }) {
  return http.get<unknown, Blob>(`/leader/project-week-result-packages/${groupId}/excel`, {
    params,
    responseType: 'blob',
  })
}
