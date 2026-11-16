import { http } from './http'
import type { DailyReviewItem } from './leader'
import type {
  LeaderEmployeeMonthPlanDetail,
  LeaderEmployeeSummary,
  LeaderEmployeeWorkPage,
  LeaderMonthPlanApprovalPage,
} from './leader'
import type {
  ActionResult,
  BatchActionPayload,
  EvidenceFile,
  ExportDownload,
  ExportTask,
  ExportTaskCreatePayload,
  LedgerItem,
  MetricItem,
  OrgNode,
  PerformanceActionPayload,
} from './performance'
import type { PageResponse } from './performance'

export interface DepartmentSummary {
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  monthPlanCount: number
  approvedPlanCount: number
  pendingPlanCount: number
  confirmedResultCount: number
  closureRate: number
  missingFieldCount: number
  overdueCount: number
  riskSummary: string
}

export interface TodoItem {
  id: string
  sceneCode: string
  title: string
  triggerText: string
  receiverId: number
  receiverName: string
  objectType: string
  objectId: string
  dueAt: string
  requirement: string
  impact: string
  status: string
  remindCount: number
  routeHint: string
}

export interface DepartmentAppeal {
  id: number
  appealNo: string
  title: string
  reason: string
  status: string
  ownerUserId: number
  employeeName: string
  orgName: string
  relatedResultId?: number
  resultTitle: string
  resultStatus: string
  completionRate: number
  handlerId?: number
  handleComment: string
  createdAt: string
  handledAt?: string
}

export interface DepartmentDayPlanReview {
  id: string
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  planDate: string
  submittedAt?: string
  workContent: string
  deliverable: string
  approvalDueAt?: string
  overdueApproval: boolean
  missingFields: string[]
  aiCheckResult: string
  reviewStatus: string
  riskLevel: string
  leaderComment: string
  leaderName: string
  reviewedAt?: string
  status: string
  departmentComment: string
  departmentReviewerName: string
  departmentReviewedAt?: string
}

export interface DepartmentDashboard {
  metrics: MetricItem[]
  summaries: DepartmentSummary[]
  urgentTodos: TodoItem[]
}

export type DepartmentEmployeeSummary = LeaderEmployeeSummary
export type DepartmentEmployeeWorkPage = LeaderEmployeeWorkPage

export interface MonthPlanApprovalItem {
  id: string
  planNo: string
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  planYear: number
  planMonth: number
  deliverable: string
  estimatedStartDate: string
  estimatedEndDate: string
  status: string
  leaderComment: string
  approverId?: number
  approverName?: string
  approvedAt?: string
  aiCheckResult: string
  missingFields: string[]
  submittedAt: string
  version: number
  items: Array<{
    id: number
    workType: string
    taskName: string
    taskContent: string
    deliverable: string
    quality: string
    performanceWeight: number
    estimatedValue: number
    estimatedStartDate?: string
    estimatedEndDate?: string
    status: string
  }>
}

export interface MonthPlanSnapshotItem {
  id: number
  workType: string
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  performanceWeight: number
  estimatedValue: number
  estimatedStartDate?: string
  estimatedEndDate?: string
}

export interface MonthPlanRejectionBaseline {
  schemaVersion: number
  versionNo: number
  rejectedAt: string
  rejectionComment: string
  snapshot: {
    planMonth: string
    items: MonthPlanSnapshotItem[]
  }
}

export interface LeaderMonthPlanApprovalDetail {
  plan: MonthPlanApprovalItem
  comparisonStatus: 'NONE' | 'AVAILABLE' | 'UNAVAILABLE'
  rejectionBaseline?: MonthPlanRejectionBaseline
}

export interface MonthPlanApprovalPage {
  items: MonthPlanApprovalItem[]
  total: number
  pageNo: number
  pageSize: number
}

export interface DepartmentLedgerPage extends PageResponse<LedgerItem> {
  monthPlanCount: number
  dayPlanCount: number
  missingCount: number
  appealCount: number
}

export interface ProjectMonthPlanMemberProgress {
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  positionName: string
  planStatus: string
  statusLabel: string
  planVersion?: number
  approvedAt?: string
}

export interface ProjectMonthPlanPackage {
  /** 项目组 ID（飞书 department_id，string）。 */
  groupId: string
  groupName: string
  departmentName: string
  planMonth: string
  approvedCount: number
  totalMemberCount: number
  status: 'EMPTY' | 'COLLECTING' | 'OPEN'
  contentAvailable: boolean
  pendingMembers: ProjectMonthPlanMemberProgress[]
  completedAt?: string
}

export interface ProjectMonthPlanPackageDetail {
  packageInfo: ProjectMonthPlanPackage
  plans: MonthPlanApprovalItem[]
}

export interface ProjectResultMemberProgress {
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  positionName: string
  resultStatus: string
  statusLabel: string
  confirmedResultCount: number
  expectedResultCount: number
}

export interface ProjectResultPackage {
  /** 项目组 ID（飞书 department_id，string）。 */
  groupId: string
  groupName: string
  departmentName: string
  planMonth: string
  completedMemberCount: number
  totalMemberCount: number
  status: 'EMPTY' | 'COLLECTING' | 'OPEN'
  contentAvailable: boolean
  pendingMembers: ProjectResultMemberProgress[]
  completedAt?: string
}

export interface ProjectResultPackageDetail {
  packageInfo: ProjectResultPackage
  results: ResultConfirmItem[]
}

export interface ResultConfirmItem {
  id: string
  resultNo: string
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerId: string
  employeeNo: string
  employeeName: string
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  planType: string
  planId: number
  planNo: string
  resultTitle: string
  resultDate: string
  completionRatio: number
  autoLevel: string
  evidenceStatus: string
  leaderSuggestion: string
  issueCodes: string[]
  issueText: string
  confirmStatus: string
  evidences: EvidenceFile[]
}

export interface DeliverableTemplate {
  id: number
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  templateName: string
  evidenceType: string
  required: boolean
  appliesTo: string
  description: string
  versionNo: string
  status: string
  referenceCount: number
}

export interface AcceptanceStandard {
  id: number
  templateId: number
  templateName: string
  standardText: string
  requireReviewPassed: boolean
  evidenceRequirement: string
  versionNo: string
  status: string
}

export interface ScoreRule {
  id: number
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  ruleName: string
  status: string
  effectiveStart?: string
  effectiveEnd?: string
  ruleJson: Record<string, unknown>
}

export interface ScoreSimulation {
  employeeName: string
  score: number
  hitFactors: string[]
  explanation: string
}

export interface DeliverableTemplatePayload {
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  templateName: string
  evidenceType: string
  required: boolean
  appliesTo?: string
  description?: string
}

export interface AcceptanceStandardPayload {
  templateId: number
  standardText: string
  requireReviewPassed?: boolean
  evidenceRequirement?: string
}

export interface ScoreRulePayload {
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  ruleName: string
  effectiveStart?: string
  effectiveEnd?: string
  ruleJson?: Record<string, unknown>
}

export interface ScoreSimulationPayload {
  employeeId?: number
  employeeName?: string
  completionRatio?: number
  overdueCount?: number
  rejectCount?: number
  evidenceComplete?: boolean
  reviewPassed?: boolean
}

export function getDepartmentOrgTreeApi() {
  return http.get<unknown, OrgNode[]>('/department/org-tree')
}

export function getDepartmentDashboardApi(params?: Record<string, unknown>) {
  return http.get<unknown, DepartmentDashboard>('/department/dashboard', { params })
}

export function listProjectMonthPlanPackagesApi(params?: Record<string, unknown>) {
  return http.get<unknown, ProjectMonthPlanPackage[]>('/department/project-month-plan-packages', { params })
}

export function getProjectMonthPlanPackageApi(groupId: string, params?: Record<string, unknown>) {
  return http.get<unknown, ProjectMonthPlanPackageDetail>(`/department/project-month-plan-packages/${groupId}`, { params })
}

export function listProjectResultPackagesApi(params?: Record<string, unknown>) {
  return http.get<unknown, ProjectResultPackage[]>('/department/project-result-packages', { params })
}

export function getProjectResultPackageApi(groupId: string, params?: Record<string, unknown>) {
  return http.get<unknown, ProjectResultPackageDetail>(`/department/project-result-packages/${groupId}`, { params })
}

export function listDepartmentEmployeesApi(params?: Record<string, unknown>) {
  return http.get<unknown, DepartmentEmployeeSummary[]>('/department/employees', { params })
}

export function getDepartmentEmployeeWorkApi(ownerId: string, params?: Record<string, unknown>) {
  return http.get<unknown, DepartmentEmployeeWorkPage>(`/department/employees/${ownerId}/work-items`, { params })
}

export function getDepartmentEmployeeMonthPlanApi(ownerId: string, planId: number) {
  return http.get<unknown, LeaderEmployeeMonthPlanDetail>(`/department/employees/${ownerId}/month-plans/${planId}`)
}

export function listMonthPlanApprovalsApi(params?: Record<string, unknown>) {
  return http.get<unknown, MonthPlanApprovalItem[]>('/department/month-plan-approvals', { params })
}

export function pageMonthPlanApprovalsApi(params?: Record<string, unknown>) {
  return http.get<unknown, MonthPlanApprovalPage>('/department/month-plan-approvals/page', { params })
}

export function getMonthPlanApprovalApi(id: string) {
  return http.get<unknown, MonthPlanApprovalItem>(`/department/month-plan-approvals/${id}`)
}

export function pageDepartmentLeaderMonthPlanApprovalsApi(params?: Record<string, unknown>) {
  return http.get<unknown, LeaderMonthPlanApprovalPage>('/department/leader-month-plan-approvals/page', { params })
}

export function getDepartmentLeaderMonthPlanApprovalApi(id: string) {
  return http.get<unknown, LeaderMonthPlanApprovalDetail>(`/department/leader-month-plan-approvals/${id}`)
}

export function downloadDepartmentLeaderMonthPlanTemplateApi(id: string) {
  return http.get<unknown, Blob>(`/department/leader-month-plan-approvals/${id}/template-download`, { responseType: 'blob' })
}

export function approveDepartmentLeaderMonthPlanApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/leader-month-plan-approvals/${id}/approve`, data)
}

export function rejectDepartmentLeaderMonthPlanApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/leader-month-plan-approvals/${id}/reject`, data)
}

export function batchApproveDepartmentLeaderMonthPlansApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/department/leader-month-plan-approvals/batch-approve', data)
}

export function batchRejectDepartmentLeaderMonthPlansApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/department/leader-month-plan-approvals/batch-reject', data)
}

export function listResultConfirmsApi(params?: Record<string, unknown>) {
  return http.get<unknown, PageResponse<ResultConfirmItem>>('/department/result-confirms', { params })
}

export function getResultConfirmApi(id: string) {
  return http.get<unknown, ResultConfirmItem>(`/department/result-confirms/${id}`)
}

export function downloadDepartmentResultEvidenceApi(resultId: string, evidenceId: number) {
  return http.get<unknown, Blob>(`/department/result-confirms/${resultId}/evidence/${evidenceId}`, { responseType: 'blob' })
}

export function confirmResultApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/result-confirms/${id}/confirm`, data)
}

export function rejectResultApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/result-confirms/${id}/reject`, data)
}

export function listTodosApi(params?: Record<string, unknown>) {
  return http.get<unknown, PageResponse<TodoItem>>('/department/todos', { params })
}

export function getDepartmentDayPlanReviewApi(id: string) {
  return http.get<unknown, DepartmentDayPlanReview>(`/department/day-plan-reviews/${id}`)
}

export function listDepartmentDailyReviewsApi(params?: Record<string, unknown>) {
  return http.get<unknown, DailyReviewItem[]>('/department/daily-reviews', { params })
}

export function getDepartmentDailyReviewApi(id: string) {
  return http.get<unknown, DailyReviewItem>(`/department/daily-reviews/${id}`)
}

export function commentDepartmentDailyReviewApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/daily-reviews/${id}/comment`, data)
}

export function batchCommentDepartmentDailyReviewsApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/department/daily-reviews/batch-comment', data)
}

export function approveDepartmentDayPlanReviewApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/day-plan-reviews/${id}/approve`, data)
}

export function rejectDepartmentDayPlanReviewApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/day-plan-reviews/${id}/reject`, data)
}

export function remindTodoApi(id: string) {
  return http.post<unknown, ActionResult>(`/department/todos/${id}/remind`)
}

export function readTodoApi(id: string) {
  return http.post<unknown, ActionResult>(`/department/todos/${id}/read`)
}

export function escalateTodoApi(id: string) {
  return http.post<unknown, ActionResult>(`/department/todos/${id}/escalate`)
}

export function doneTodoApi(id: string) {
  return http.post<unknown, ActionResult>(`/department/todos/${id}/done`)
}

export function batchRemindTodosApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/department/todos/batch-remind', data)
}

export function batchEscalateTodosApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/department/todos/batch-escalate', data)
}

export function getDepartmentAppealApi(id: string) {
  return http.get<unknown, DepartmentAppeal>(`/department/appeals/${id}`)
}

export function acceptDepartmentAppealApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/appeals/${id}/accept`, data)
}

export function resolveDepartmentAppealApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/department/appeals/${id}/resolve`, data)
}

export function downloadDepartmentAppealPackageApi(id: string) {
  return http.get<unknown, Blob>(`/department/appeals/${id}/package`, { responseType: 'blob' })
}

export function listDeliverableTemplatesApi(params?: Record<string, unknown>) {
  return http.get<unknown, DeliverableTemplate[]>('/department/templates', { params })
}

export function createDeliverableTemplateApi(data: DeliverableTemplatePayload) {
  return http.post<unknown, DeliverableTemplate>('/department/templates', data)
}

export function updateDeliverableTemplateApi(id: number, data: DeliverableTemplatePayload) {
  return http.put<unknown, DeliverableTemplate>(`/department/templates/${id}`, data)
}

export function toggleDeliverableTemplateApi(id: number, enabled: boolean) {
  return http.post<unknown, ActionResult>(`/department/templates/${id}/${enabled ? 'enable' : 'disable'}`)
}

export function listAcceptanceStandardsApi(params?: Record<string, unknown>) {
  return http.get<unknown, AcceptanceStandard[]>('/department/acceptance-standards', { params })
}

export function createAcceptanceStandardApi(data: AcceptanceStandardPayload) {
  return http.post<unknown, AcceptanceStandard>('/department/acceptance-standards', data)
}

export function updateAcceptanceStandardApi(id: number, data: AcceptanceStandardPayload) {
  return http.put<unknown, AcceptanceStandard>(`/department/acceptance-standards/${id}`, data)
}

export function toggleAcceptanceStandardApi(id: number, enabled: boolean) {
  return http.post<unknown, ActionResult>(`/department/acceptance-standards/${id}/${enabled ? 'enable' : 'disable'}`)
}

export function listScoreRulesApi(params?: Record<string, unknown>) {
  return http.get<unknown, ScoreRule[]>('/department/score-rules', { params })
}

export function createScoreRuleApi(data: ScoreRulePayload) {
  return http.post<unknown, ScoreRule>('/department/score-rules', data)
}

export function updateScoreRuleApi(id: number, data: ScoreRulePayload) {
  return http.put<unknown, ScoreRule>(`/department/score-rules/${id}`, data)
}

export function enableScoreRuleApi(id: number) {
  return http.post<unknown, ActionResult>(`/department/score-rules/${id}/enable`)
}

export function simulateScoreRuleApi(id: number, data: ScoreSimulationPayload) {
  return http.post<unknown, ScoreSimulation>(`/department/score-rules/${id}/simulate`, data)
}

export function listDepartmentLedgersApi(params?: Record<string, unknown>) {
  return http.get<unknown, DepartmentLedgerPage>('/department/department-ledgers', { params })
}

export function exportDepartmentLedgersApi(data: ExportTaskCreatePayload) {
  return http.post<unknown, ExportTask>('/department/department-ledgers/export', data)
}

export function listExportTasksApi(params?: Record<string, unknown>) {
  return http.get<unknown, PageResponse<ExportTask>>('/department/export-tasks', { params })
}

export function getExportTaskApi(id: string) {
  return http.get<unknown, ExportTask>(`/department/export-tasks/${id}`)
}

export function createExportTaskApi(data: ExportTaskCreatePayload) {
  return http.post<unknown, ExportTask>('/department/export-tasks', data)
}

export function checkExportTaskApi(id: string) {
  return http.post<unknown, ActionResult>(`/department/export-tasks/${id}/check`)
}

export function retryExportTaskApi(id: string) {
  return http.post<unknown, ActionResult>(`/department/export-tasks/${id}/retry`)
}

export function getExportDownloadInfoApi(id: string) {
  return http.get<unknown, ExportDownload>(`/department/export-tasks/${id}/download-info`)
}

export function downloadExportTaskApi(id: string) {
  return http.get<unknown, Blob>(`/department/export-tasks/${id}/download`, { responseType: 'blob' })
}
