import { http } from './http'

export type EmployeePlanStatus = 'draft' | 'submitted' | 'approved' | 'rejected' | 'confirmed' | 'paused' | 'canceled' | 'archived'
export type EmployeeResultStatus = 'not_submitted' | 'draft' | 'submitted' | 'confirmed' | 'rejected'
export type EmployeeAppealStatus = 'draft' | 'submitted' | 'processing' | 'resolved' | 'closed'
export type EmployeeEvidencePeriodType = 'day' | 'week' | 'month' | 'quarter' | 'year'
export type EmployeeEvidenceSourceType = 'day_plan' | 'month_plan' | 'result' | 'appeal'

export interface EmployeeMonthPlan {
  id: number
  planMonth: string
  title: string
  planStatus: EmployeePlanStatus
  resultStatus: EmployeeResultStatus
  completionRate: number
  updatedAt: string
}

export interface EmployeeDayPlanCalendarItem {
  date: string
  status: EmployeePlanStatus
}

export interface EmployeeWorkdayCalendarItem {
  date: string
  ruleType: 'WORKDAY' | 'WEEKEND' | 'HOLIDAY' | 'LEAVE' | 'BUSINESS_TRIP' | 'SPECIAL_SHIFT'
  forceReport: boolean
  description?: string
  ruleId?: number
  versionNo?: number
  explicit: boolean
  missingRequired: boolean
}

export interface EmployeeDashboardResp {
  currentMonth: string
  orgName: string
  monthPlans: EmployeeMonthPlan[]
  dayPlanCalendar: EmployeeDayPlanCalendarItem[]
  workdayCalendar: EmployeeWorkdayCalendarItem[]
  summary: {
    monthPlanCount: number
    submittedResultCount: number
    averageCompletionRate: number
    openAppealCount: number
    missingRequiredDayPlanCount: number
  }
}

export interface EmployeeMonthPlanItem {
  id: string
  taskType: 'REGULAR'
  performanceWeight: number
  estimatedValue: number
  taskName: string
  taskContent: string
  progress: string
  deliverable: string
  quality: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  status: EmployeePlanStatus
  sortNo: number
  submittedAt?: string
  approvedAt?: string
  approvalComment?: string
}

export interface EmployeeDeliverable {
  id: string
  name: string
  fileType: string
  relatedTaskName: string
  submittedAt: string
  fileUrl: string
}

export interface EmployeeResultSummary {
  submittedCount: number
  confirmedCount: number
  rejectedCount: number
  latestVersion: string
  overallCompletionRate: number
}

export interface ConfirmRecord {
  id: string
  bizType: string
  bizId: string
  operatorName: string
  action: string
  comment: string
  createdAt: string
}

export interface EmployeeMonthPlanDetailResp {
  id: string
  planMonth: string
  employeeName: string
  departmentName: string
  projectName?: string
  positionName?: string
  status: EmployeePlanStatus
  resultStatus: EmployeeResultStatus
  updatedAt: string
  approvalComment?: string
  approvedAt?: string
  items: EmployeeMonthPlanItem[]
  deliverables: EmployeeDeliverable[]
  resultSummary: EmployeeResultSummary
  confirmRecords: ConfirmRecord[]
}

export type EmployeeMonthPlanDraftClearResp = Omit<EmployeeMonthPlanDetailResp, 'id'> & { id: null }

export interface EmployeeMonthPlanTemplatePreviewItem {
  rowNo: number
  taskContent: string
  performanceWeight: number
  deliverable: string
  quality: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  estimatedValue: number
}

export interface EmployeeMonthPlanTemplatePreviewResp {
  planId: string
  planMonth: string
  employeeName?: string
  projectName?: string
  positionName?: string
  items: EmployeeMonthPlanTemplatePreviewItem[]
  estimatedValueTotal: number
}

export interface SaveMonthPlanDraftReq {
  planMonth?: string
  items: Array<{
    id?: string
    taskName: string
    taskContent: string
    deliverable: string
    quality: string
    estimatedStartDate: string
    estimatedEndDate: string
    performanceWeight: number
    estimatedValue: number
  }>
  recordVersion?: boolean
  versionRequestId?: string
  restoredFromVersionId?: number
}

export interface EmployeeMonthPlanSubmitResp {
  id: string
  status: EmployeePlanStatus
  submittedAt?: string
}

export interface EmployeeMonthPlanItemOption {
  id: string
  taskName: string
}

export interface EmployeeWeekPlanItemOption {
  id: string
  content: string
  deliverable?: string
  plannedFinishDate?: string
  monthPlanItemId: string | null
  monthPlanItemName?: string
}

export interface EmployeeLegacyMonthPlanItem {
  id: string
  taskName: string
}

export interface EmployeeDayPlanDetailResp {
  /** BUG-R6-14：日计划主键为字符串（UUID/十六进制）。 */
  id: string
  planDate: string
  orgName: string
  /** BUG-R6-14：关联的周计划条目主键为字符串（UUID/十六进制）。 */
  relatedWeekPlanItemIds: string[]
  content: string
  remark: string
  status: EmployeePlanStatus
  reviewStatus?: string
  riskLevel?: string
  leaderComment?: string
  reviewedAt?: string
  departmentComment?: string
  departmentReviewedAt?: string
  approvalDueAt?: string
  aiCheckResult?: string
  calendarRule: EmployeeWorkdayCalendarItem
  weekPlanItemOptions: EmployeeWeekPlanItemOption[]
  legacyMonthPlanItem?: EmployeeLegacyMonthPlanItem | null
}

export interface SaveDayPlanDraftReq {
  /** BUG-R6-14：日计划主键为字符串（UUID/十六进制）。 */
  id?: string
  planDate: string
  /** BUG-R6-14：关联的周计划条目主键为字符串（UUID/十六进制）。 */
  relatedWeekPlanItemIds: string[]
  content: string
  remark: string
  recordVersion?: boolean
  versionRequestId?: string
  restoredFromVersionId?: number
}

export interface SubmitDayPlanReq extends SaveDayPlanDraftReq {}

export interface EmployeeDayPlanSubmitResp {
  /** BUG-R6-14：日计划主键为字符串（UUID/十六进制）。 */
  id: string
  status: EmployeePlanStatus
  submittedAt: string
}

export interface EmployeeResultSubmitOptionsResp {
  /**
   * BUG-R6-14：主键是**十六进制 / UUID 字符串**，
   * 不是数字。早期 TS 声明为 `number` 属历史遗留，
   * 任何 `Number(...)` 强转都会把主键塌成 `0`。
   */
  monthPlanOptions: Array<{
    id: string
    title: string
    planMonth: string
    employeeName: string
    projectName: string
    positionName: string
  }>
  monthPlanItemOptions: Array<{
    id: string
    monthPlanId: string
    taskName: string
    taskContent: string
    performanceWeight: number
    deliverable: string
    quality: string
    estimatedStartDate?: string
    estimatedEndDate?: string
    estimatedValue: number
    sortNo: number
  }>
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
  resultVersions: Array<{
    id: string
    monthPlanId: string
    monthPlanItemId?: string
    versionNo: string
    status: EmployeeResultStatus
    submittedAt: string
    leaderSuggestion: string
    confirmComment: string
    description: string
    completionStatus: string
    remark: string
    personalPerformance?: number
    completionRate: number
    evidenceStatus: string
    hasEvidence: boolean
  }>
}

export interface SubmitEmployeeResultReq {
  monthPlanId: string
  monthPlanItemId?: string
  completionRate: number
  description?: string
  completionStatus: string
  remark?: string
  personalPerformance: number
  file?: File | Blob
}

export interface EmployeeResultVersionResp {
  id: number
  versionNo: string
  status: EmployeeResultStatus
  submittedAt: string
}

export interface EmployeePerformanceEvidenceItem {
  id: number
  evidenceDate: string
  periodType: EmployeeEvidencePeriodType
  sourceType: EmployeeEvidenceSourceType
  title: string
  description: string
  score: number
  createdAt: string
}

export interface EmployeePerformanceEvidenceResp {
  periodType: EmployeeEvidencePeriodType
  periodStart: string
  periodEnd: string
  items: EmployeePerformanceEvidenceItem[]
}

export interface EmployeeResultEvidence {
  /** BUG-R6-14：证据 ID 是字符串主键。 */
  id: string
  fileName: string
  fileType: string
  fileSize: number
  status: string
  reviewPassed: boolean
  checksum: string
  createdAt: string
  downloadUrl: string
}

export interface EmployeeResultDetailResp {
  id: string
  resultNo: string
  title: string
  resultDate: string
  description: string
  completionRate: number
  completionStatus: string
  remark: string
  personalPerformance?: number
  versionNo: string
  status: EmployeeResultStatus
  planType: string
  planId?: string
  planTitle: string
  monthPlanItemId?: string
  planItemName: string
  submittedAt: string
  suggestionStatus: string
  leaderSuggestion: string
  suggestedAt?: string
  confirmComment: string
  confirmedAt?: string
  evidenceStatus: string
  issueCodes: string[]
  issueText: string
  evidences: EmployeeResultEvidence[]
}

export interface EmployeeAppealItem {
  id: string
  appealNo: string
  title: string
  reason: string
  status: EmployeeAppealStatus
  handleComment: string
  handledAt?: string
  createdAt: string
}

export interface EmployeeAppealListResp {
  items: EmployeeAppealItem[]
}

export interface EmployeeAppealOption {
  resultId: string
  label: string
  status: EmployeeResultStatus
  deadline: string
}

export interface CreateEmployeeAppealReq {
  relatedResultId?: string
  title: string
  reason: string
}

export interface CreateEmployeePlanAdjustmentReq {
  planType: 'MONTH'
  planId: string
  adjustmentType: 'PAUSE' | 'CANCEL'
  reason: string
  impactText?: string
}

export interface EmployeeExportResp {
  taskId: string
  fileName: string
  checksum: string
  downloadUrl: string
}

export async function getEmployeeDashboardApi(month: string) {
  return http.get<unknown, EmployeeDashboardResp>('/employee/dashboard', { params: { month } })
}

export async function getEmployeeMonthPlanDetailApi(id: string) {
  return http.get<unknown, EmployeeMonthPlanDetailResp>(`/employee/month-plans/${id}`)
}

export async function getEmployeeMonthPlanTemplatePreviewApi(id: string) {
  return http.get<unknown, EmployeeMonthPlanTemplatePreviewResp>(`/employee/month-plans/${id}/template-preview`)
}

export async function downloadEmployeeMonthPlanTemplateApi(id: string) {
  return http.get<unknown, Blob>(`/employee/month-plans/${id}/template-download`, { responseType: 'blob' })
}

export async function createEmployeeMonthPlanDraftApi(payload: SaveMonthPlanDraftReq) {
  return http.post<SaveMonthPlanDraftReq, EmployeeMonthPlanDetailResp>('/employee/month-plans/draft', payload)
}

export async function saveEmployeeMonthPlanDraftApi(id: string, payload: SaveMonthPlanDraftReq) {
  return http.post<SaveMonthPlanDraftReq, EmployeeMonthPlanDetailResp>(`/employee/month-plans/${id}/draft`, payload)
}

export async function clearEmployeeMonthPlanDraftApi(id: string) {
  return http.post<undefined, EmployeeMonthPlanDraftClearResp>(`/employee/month-plans/${id}/draft/clear`)
}

export async function submitEmployeeMonthPlanApi(id: string) {
  return http.post<undefined, EmployeeMonthPlanSubmitResp>(`/employee/month-plans/${id}/submit`)
}

export async function withdrawEmployeeMonthPlanApi(id: string) {
  return http.post<undefined, EmployeeMonthPlanSubmitResp>(`/employee/month-plans/${id}/withdraw`)
}

export async function deleteEmployeeMonthPlanItemApi(planId: string, itemId: string) {
  return http.delete<unknown, void>(`/employee/month-plans/${planId}/items/${itemId}`)
}

export async function getEmployeeDayPlanDetailApi(date: string) {
  return http.get<unknown, EmployeeDayPlanDetailResp>('/employee/day-plans/detail', { params: { date } })
}

export async function saveEmployeeDayPlanDraftApi(payload: SaveDayPlanDraftReq) {
  return http.post<SaveDayPlanDraftReq, EmployeeDayPlanDetailResp>('/employee/day-plans/draft', payload)
}

export async function submitEmployeeDayPlanApi(payload: SubmitDayPlanReq) {
  return http.post<SubmitDayPlanReq, EmployeeDayPlanSubmitResp>('/employee/day-plans/submit', payload)
}

export async function deleteEmployeeDayPlanDraftApi(id: string) {
  return http.delete<unknown, void>(`/employee/day-plans/${id}`)
}

export async function withdrawEmployeeDayPlanApi(id: string) {
  return http.post<undefined, EmployeeDayPlanSubmitResp>(`/employee/day-plans/${id}/withdraw`)
}

export async function getEmployeeResultSubmitOptionsApi() {
  return http.get<unknown, EmployeeResultSubmitOptionsResp>('/employee/results/submit/options')
}

export async function getEmployeeResultDetailApi(id: string) {
  return http.get<unknown, EmployeeResultDetailResp>(`/employee/results/${id}`)
}

export async function downloadEmployeeResultEvidenceApi(resultId: string, evidenceId: string) {
  return http.get<unknown, Blob>(`/employee/results/${resultId}/evidence/${evidenceId}`, { responseType: 'blob' })
}

export async function submitEmployeeResultApi(payload: FormData | SubmitEmployeeResultReq) {
  return http.post<FormData | SubmitEmployeeResultReq, EmployeeResultVersionResp>('/employee/results/submit', payload, payload instanceof FormData ? {
    headers: { 'Content-Type': 'multipart/form-data' },
  } : undefined)
}

export async function downloadEmployeeResultExcelApi(payload: {
  monthPlanId: string
  rows: Array<{ monthPlanItemId: string; completionStatus: string; remark: string; personalPerformance: number | null }>
}) {
  return http.post<typeof payload, Blob>('/employee/results/submit/excel', payload, { responseType: 'blob' })
}

export async function getEmployeePerformanceEvidenceApi(periodType: EmployeeEvidencePeriodType) {
  return http.get<unknown, EmployeePerformanceEvidenceResp>('/employee/performance-evidence', { params: { periodType } })
}

export async function getEmployeeAppealsApi() {
  return http.get<unknown, EmployeeAppealListResp>('/employee/appeals')
}

export async function getEmployeeAppealOptionsApi() {
  return http.get<unknown, EmployeeAppealOption[]>('/employee/appeals/options')
}

export async function downloadEmployeeAppealPackageApi(id: string) {
  return http.get<unknown, Blob>(`/employee/appeals/${id}/package`, { responseType: 'blob' })
}

export async function createEmployeeAppealApi(payload: CreateEmployeeAppealReq) {
  return http.post<CreateEmployeeAppealReq, EmployeeAppealItem>('/employee/appeals', payload)
}

export async function createEmployeePlanAdjustmentApi(payload: CreateEmployeePlanAdjustmentReq) {
  return http.post<CreateEmployeePlanAdjustmentReq, { id: string; adjustmentNo: string; status: string }>('/employee/plan-adjustments', payload)
}

export async function createEmployeeEvidenceExportApi(payload: { periodType: EmployeeEvidencePeriodType; formats: string[]; includeEvidence: boolean }) {
  return http.post<typeof payload, EmployeeExportResp>('/employee/performance-evidence/export', payload)
}

export async function downloadEmployeeExportApi(taskId: string) {
  return http.get<unknown, Blob>(`/employee/export-tasks/${taskId}/download`, { responseType: 'blob' })
}
