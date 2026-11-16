import { http } from './http'
import type { PageResponse } from './performance'

/**
 * BUG-R6-14：主键是**十六进制 / UUID 字符串**，不是数字。
 * 早期 TS 声明为 `number` 属历史遗留，任何 `Number(...)` 强转都会把它塌成 `0`。
 */
export interface WeekResultTask {
  id: string
  weekPlanId: string
  monthPlanItemId: string | null
  content: string
  deliverable?: string
  quality?: string
  plannedFinishDate?: string
  sortNo: number
}

export interface WeekResultEvidence {
  id: string
  fileName: string
  fileType: string
  fileSize: number
  checksum: string
  downloadUrl: string
  createdAt: string
}

export interface WeekResultRecord {
  id: string
  weekPlanId: string
  weekPlanItemId: string
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
  approverId?: string
  approveAt?: string
  approvalComment?: string
  evidences: WeekResultEvidence[]
}

export interface WeekResultSubmitContext {
  weekPlanId: string
  weekPlanTitle: string
  weekStart: string
  weekEnd: string
  weekPlanStatus: string
  task: WeekResultTask
  latestResult?: WeekResultRecord | null
  /**
   * 允许上传的**文件扩展名**（不含点，如 `pdf` / `docx` / `png`）。
   * ⚠️ BUG-R6-19：后端此前返回的是 MIME（`application/pdf`），
   *    前端却按扩展名比对 → 合法文件一律被判「类型不支持」，
   *    拼出来的 `accept` 属性（`.application/pdf`）也完全无效。
   */
  acceptedFileTypes: string[]
  /** 允许上传的 MIME 类型（后端服务端校验用，前端一般不用）。 */
  acceptedFileMimes?: string[]
  maxFileSizeMb: number
}

export interface WeekResultSubmitAction {
  id: string
  status: string
  submittedAt: string
  message: string
}

export interface WeekResultSubmitOption {
  weekPlanId: string
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
  id: string
  weekPlanId: string
  ownerUserId: string
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
  approverId?: string
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

export function getEmployeeWeekResultDetailApi(resultId: string) {
  return http.get<unknown, WeekResultRecord>(`/employee/week-results/${resultId}`)
}

export function downloadWeekResultEvidenceApi(resultId: string, evidenceId: string) {
  return http.get<unknown, Blob>(`/employee/week-results/${resultId}/evidence/${evidenceId}`, { responseType: 'blob' })
}

export function getWeekResultSubmitContextApi(weekPlanId: string, itemId: string) {
  return http.get<unknown, WeekResultSubmitContext>(`/employee/week-plans/${weekPlanId}/items/${itemId}/results/submit-context`)
}

export function submitWeekResultApi(weekPlanId: string, itemId: string, payload: FormData) {
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

export function approveLeaderWeekResultApi(id: string, comment?: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/leader/week-result-approvals/${id}/approve`, { comment })
}

export function rejectLeaderWeekResultApi(id: string, comment: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/leader/week-result-approvals/${id}/reject`, { comment })
}

export function approveDepartmentWeekResultApi(id: string, comment?: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/department/week-result-approvals/${id}/approve`, { comment })
}

export function rejectDepartmentWeekResultApi(id: string, comment: string) {
  return http.post<unknown, WeekResultSubmitAction>(`/department/week-result-approvals/${id}/reject`, { comment })
}

export function downloadWeekResultApprovalEvidenceApi(
  workspace: 'leader' | 'department', resultId: string, evidenceId: string,
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
