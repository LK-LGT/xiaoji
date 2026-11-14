import { http } from './http'
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
import type { LeaderMonthPlanApprovalDetail, MonthPlanApprovalItem } from './department'

export interface LeaderDateStatus {
  date: string
  orgId: number
  orgName: string
  pendingReviewCount: number
  pendingSuggestCount: number
  overdueCount: number
  status: string
}

export interface DailyReviewItem {
  id: string
  ownerId: number
  employeeNo: string
  employeeName: string
  orgId: number
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
  leaderComment?: string
  reviewedAt?: string
}

export interface LeaderWorkbench {
  orgTree: OrgNode[]
  metrics: MetricItem[]
  dateStatuses: LeaderDateStatus[]
  subordinateSummaries: DailyReviewItem[]
}

export interface LeaderEmployeeSummary {
  ownerId: number
  employeeNo: string
  employeeName: string
  orgId: number
  orgName: string
  monthPlanCount: number
  weekPlanCount: number
  dayPlanCount: number
  resultCount: number
  totalWorkCount: number
  latestActivityAt?: string
}

export type LeaderWorkType = 'MONTH_PLAN' | 'WEEK_PLAN' | 'DAY_PLAN' | 'RESULT'

export interface LeaderEmployeeWorkItem {
  rowId: string
  workType: LeaderWorkType
  taskType?: string
  businessId: number
  itemId?: number
  title: string
  content: string
  deliverable: string
  remark: string
  quality: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  performanceWeight?: number
  estimatedValue?: number
  periodLabel: string
  startDate?: string
  endDate?: string
  status: string
  completionRate?: number
  evidenceStatus?: string
  relatedPlanType?: string
  relatedPlanId?: number
  relatedPlanTitle: string
  leaderComment: string
  updatedAt?: string
}

export interface LeaderEmployeeWorkPage {
  employee: LeaderEmployeeSummary
  items: LeaderEmployeeWorkItem[]
  total: number
  pageNo: number
  pageSize: number
}

export interface LeaderEmployeeMonthPlanTask {
  id: number
  sortNo?: number
  taskType?: string
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  remark: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  performanceWeight?: number
  estimatedValue?: number
  status: string
  completionRate?: number
}

export interface LeaderEmployeeMonthPlanDetail {
  id: number
  title: string
  planMonth: string
  status: string
  leaderComment: string
  updatedAt?: string
  tasks: LeaderEmployeeMonthPlanTask[]
}

export interface LeaderMonthPlanApprovalPage {
  items: MonthPlanApprovalItem[]
  total: number
  pageNo: number
  pageSize: number
  submittedCount: number
  approvedCount: number
  totalEmployeeCount: number
}

export interface ResultSuggestionItem {
  id: string
  ownerId: number
  employeeNo: string
  employeeName: string
  orgId: number
  orgName: string
  resultNo: string
  resultTitle: string
  resultDate: string
  planType: string
  planId: number
  planNo: string
  completionRatio: number
  autoLevel: string
  evidenceStatus: string
  issueCodes: string[]
  issueText: string
  suggestionStatus: string
  leaderSuggestion?: string
  resultStatus: string
  evidences: EvidenceFile[]
}

export interface ProjectDayPlanMemberProgress {
  ownerId: number
  employeeNo: string
  employeeName: string
  positionName: string
  planStatus: string
  statusLabel: string
  submittedAt?: string
}

export interface ProjectDayPlanPackage {
  groupId: number
  groupName: string
  planDate: string
  submittedCount: number
  totalMemberCount: number
  status: string
  contentAvailable: boolean
  pendingMembers: ProjectDayPlanMemberProgress[]
  completedAt?: string
}

export interface ProjectDayPlanPrintRow {
  ownerId: number
  employeeNo: string
  employeeName: string
  workContent: string
}

export interface ProjectDayPlanPackageDetail {
  packageInfo: ProjectDayPlanPackage
  rows: ProjectDayPlanPrintRow[]
}

export interface ResultEvidenceArchiveEntry {
  path: string
  name: string
  directory: boolean
  size: number
  previewType: 'FOLDER' | 'IMAGE' | 'PDF' | 'DOCUMENT' | 'ARCHIVE' | 'TEXT' | 'DOWNLOAD'
}

export interface ResultEvidencePreview {
  fileName: string
  previewType: 'IMAGE' | 'PDF' | 'DOCUMENT' | 'ARCHIVE' | 'TEXT' | 'DOWNLOAD'
  mimeType: string
  textContent?: string
  entries: ResultEvidenceArchiveEntry[]
}

export interface PlanAdjustmentItem {
  id: string
  originalPlanType: string
  originalPlanId: number
  originalPlanNo: string
  originalWorkContent: string
  newPlanType: string
  newPlanId: number
  newPlanNo: string
  ownerId: number
  employeeName: string
  adjustmentType: string
  reason: string
  impactText: string
  operationComment?: string
  status: string
  keepEvidenceChain: boolean
  operatorName?: string
  operatedAt?: string
}

export function getLeaderOrgTreeApi() {
  return http.get<unknown, OrgNode[]>('/leader/org-tree')
}

export function getLeaderWorkbenchApi(params?: Record<string, unknown>) {
  return http.get<unknown, LeaderWorkbench>('/leader/workbench', { params })
}

export function listLeaderEmployeesApi(params?: Record<string, unknown>) {
  return http.get<unknown, LeaderEmployeeSummary[]>('/leader/employees', { params })
}

export function listLeaderProjectDayPlanPackagesApi(params?: Record<string, unknown>) {
  return http.get<unknown, ProjectDayPlanPackage[]>('/leader/project-day-plan-packages', { params })
}

export function getLeaderProjectDayPlanPackageApi(groupId: number, params?: Record<string, unknown>) {
  return http.get<unknown, ProjectDayPlanPackageDetail>(`/leader/project-day-plan-packages/${groupId}`, { params })
}

export function getLeaderEmployeeWorkApi(ownerId: number, params?: Record<string, unknown>) {
  return http.get<unknown, LeaderEmployeeWorkPage>(`/leader/employees/${ownerId}/work-items`, { params })
}

export function getLeaderEmployeeMonthPlanApi(ownerId: number, planId: number) {
  return http.get<unknown, LeaderEmployeeMonthPlanDetail>(`/leader/employees/${ownerId}/month-plans/${planId}`)
}

export function listDailyReviewsApi(params?: Record<string, unknown>) {
  return http.get<unknown, DailyReviewItem[]>('/leader/daily-reviews', { params })
}

export function getDailyReviewApi(id: string) {
  return http.get<unknown, DailyReviewItem>(`/leader/daily-reviews/${id}`)
}

export function commentDailyReviewApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/leader/daily-reviews/${id}/comment`, data)
}

export function markDailyReviewRiskApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/leader/daily-reviews/${id}/risk`, data)
}

export function batchCommentDailyReviewsApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/leader/daily-reviews/batch-comment', data)
}

export function batchMarkDailyReviewRisksApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/leader/daily-reviews/batch-risk', data)
}

export function listResultSuggestionsApi(params?: Record<string, unknown>) {
  return http.get<unknown, ResultSuggestionItem[]>('/leader/result-suggestions', { params })
}

export function getResultSuggestionApi(id: string) {
  return http.get<unknown, ResultSuggestionItem>(`/leader/result-suggestions/${id}`)
}

export function downloadLeaderResultEvidenceApi(resultId: string, evidenceId: number) {
  return http.get<unknown, Blob>(`/leader/result-suggestions/${resultId}/evidence/${evidenceId}`, { responseType: 'blob' })
}

export function getLeaderResultEvidencePreviewApi(resultId: string, evidenceId: number) {
  return http.get<unknown, ResultEvidencePreview>(`/leader/result-suggestions/${resultId}/evidence/${evidenceId}/preview`)
}

export function openLeaderResultEvidenceArchiveEntryApi(resultId: string, evidenceId: number, path: string) {
  return http.get<unknown, Blob>(`/leader/result-suggestions/${resultId}/evidence/${evidenceId}/archive-entry`, {
    params: { path },
    responseType: 'blob',
  })
}

export function submitResultSuggestionApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/leader/result-suggestions/${id}/suggest`, data)
}

export function batchSubmitResultSuggestionsApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/leader/result-suggestions/batch-suggest', data)
}

export function listPlanAdjustmentsApi(params?: Record<string, unknown>) {
  return http.get<unknown, PlanAdjustmentItem[]>('/leader/plan-adjustments', { params })
}

export function getPlanAdjustmentApi(id: string) {
  return http.get<unknown, PlanAdjustmentItem>(`/leader/plan-adjustments/${id}`)
}

export function processPlanAdjustmentApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/leader/plan-adjustments/${id}/process`, data)
}

export function listLeaderMonthPlanApprovalsApi(params?: Record<string, unknown>) {
  return http.get<unknown, MonthPlanApprovalItem[]>('/leader/month-plan-approvals', { params })
}

export function pageLeaderMonthPlanApprovalsApi(params?: Record<string, unknown>) {
  return http.get<unknown, LeaderMonthPlanApprovalPage>('/leader/month-plan-approvals/page', { params })
}

export function getLeaderMonthPlanApprovalApi(id: string) {
  return http.get<unknown, LeaderMonthPlanApprovalDetail>(`/leader/month-plan-approvals/${id}`)
}

export function downloadLeaderMonthPlanTemplateApi(id: string) {
  return http.get<unknown, Blob>(`/leader/month-plan-approvals/${id}/template-download`, { responseType: 'blob' })
}

export function approveLeaderMonthPlanApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/leader/month-plan-approvals/${id}/approve`, data)
}

export function rejectLeaderMonthPlanApi(id: string, data: PerformanceActionPayload) {
  return http.post<unknown, ActionResult>(`/leader/month-plan-approvals/${id}/reject`, data)
}

export function batchApproveLeaderMonthPlansApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/leader/month-plan-approvals/batch-approve', data)
}

export function batchRejectLeaderMonthPlansApi(data: BatchActionPayload) {
  return http.post<unknown, ActionResult[]>('/leader/month-plan-approvals/batch-reject', data)
}

export function listTeamLedgersApi(params?: Record<string, unknown>) {
  return http.get<unknown, LedgerItem[]>('/leader/team-ledgers', { params })
}

export function exportTeamLedgersApi(data: ExportTaskCreatePayload) {
  return http.post<unknown, ExportTask>('/leader/team-ledgers/export', data)
}

export function createLeaderExportTaskApi(data: ExportTaskCreatePayload) {
  return http.post<unknown, ExportTask>('/leader/export-tasks', data)
}

export function getLeaderExportDownloadInfoApi(id: string) {
  return http.get<unknown, ExportDownload>(`/leader/export-tasks/${id}/download-info`)
}

export function downloadLeaderExportTaskApi(id: string) {
  return http.get<unknown, Blob>(`/leader/export-tasks/${id}/download`, { responseType: 'blob' })
}
