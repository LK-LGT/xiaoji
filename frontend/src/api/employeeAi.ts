import { AI_OPERATION_TIMEOUT_MS, http } from './http'

export type AiWorkType = 'TASK' | 'METRIC' | 'DOCUMENT' | 'PROJECT' | 'COMMUNICATION' | 'ROUTINE' | 'UNKNOWN'
export type MonthPlanAiOperationMode = 'AUTO' | 'GENERATE' | 'SUPPLEMENT' | 'OPTIMIZE' | 'REVISE_REJECTION' | 'CLARIFY'
export type PlanGenerationProfile = 'EMPLOYEE_EXECUTION' | 'LEADER_DELIVERY_MANAGEMENT'
export interface AiRequestOptions { requestId: string; signal: AbortSignal }
export interface AiGenerationExample { fileName: string; content: string; truncated?: boolean }
export type AiCancelState = 'CANCELLED' | 'ALREADY_CANCELLED' | 'ALREADY_FINISHED'

export type MonthPlanContextType =
  | 'ORGANIZATION'
  | 'JOB_DESCRIPTION'
  | 'HISTORY_PLANS'
  | 'DEPARTMENT_GOAL'
  | 'LEADER_REQUIREMENT'
  | 'DELIVERABLE_TEMPLATES'
  | 'ENTERPRISE_KNOWLEDGE'

export const MONTH_PLAN_CONTEXT_TYPES: MonthPlanContextType[] = [
  'ORGANIZATION',
  'JOB_DESCRIPTION',
  'HISTORY_PLANS',
  'DEPARTMENT_GOAL',
  'LEADER_REQUIREMENT',
  'DELIVERABLE_TEMPLATES',
  'ENTERPRISE_KNOWLEDGE',
]

export const MONTH_PLAN_CONTEXT_LABELS: Record<MonthPlanContextType, string> = {
  ORGANIZATION: '组织信息',
  JOB_DESCRIPTION: '岗位说明',
  HISTORY_PLANS: '最近三个月已审批月计划',
  DEPARTMENT_GOAL: '部门目标',
  LEADER_REQUIREMENT: '领导要求',
  DELIVERABLE_TEMPLATES: '交付物与验收模板',
  ENTERPRISE_KNOWLEDGE: '企业知识依据',
}

export interface AiContextOption {
  type: MonthPlanContextType
  label: string
  available: boolean
  summary?: string
}

export interface AiKnowledgeReference {
  knowledgeId: string
  title: string
  knowledgeType: string
  version: string
  binding: 'NORMATIVE' | 'REFERENCE' | 'EXAMPLE' | string
  citation: string
}

export interface AiHistoryMonthOption {
  month: string
  label: string
  available: boolean
  planCount: number
}

export interface AiKnowledgeTypeOption {
  type: string
  label: string
  available: boolean
  documentCount: number
}

export interface AiPlanItem {
  workType?: AiWorkType
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  estimatedStartDate: string
  estimatedEndDate: string
  performanceWeight: number
  estimatedValue: number
}

export interface AiPlanForm {
  items: AiPlanItem[]
}

export interface AiContextResponse {
  enabled: boolean
  providerCode: string
  modelName: string
  generationProfile?: PlanGenerationProfile
  generationProfileLabel?: string
  availableContext: string[]
  missingContext: string[]
  historyPlanCount?: number
  contextOptions?: AiContextOption[]
  knowledgeReferences?: AiKnowledgeReference[]
  historyMonthOptions?: AiHistoryMonthOption[]
  knowledgeTypeOptions?: AiKnowledgeTypeOption[]
  parentOptions?: PlanAiParentOption[]
  relatedWeekItems?: WeekPlanAiItem[]
  remainingCalls: Record<string, number>
  notice: string
}

export interface PlanAiParentOption { /** BUG-R6-14：条目主键为字符串（UUID/十六进制）。 */ id: string; planMonth: string; taskName: string; taskContent: string; deliverable: string }
export interface WeekPlanAiItem { /** BUG-R6-14：主键为字符串（UUID/十六进制）。 */ id?: string; monthPlanItemId: string | null; content: string; deliverable?: string; quality?: string; plannedFinishDate: string }
export interface WeekPlanAiDraft { suggestionId: string; items: WeekPlanAiItem[]; warnings: string[]; missingContext: string[]; notice: string; draftVersionId: number; draftVersionNo: number }
export interface DayPlanAiForm { /** BUG-R6-14：周计划条目主键为字符串（UUID/十六进制）。 */ relatedWeekPlanItemIds: string[]; content: string; remark: string }
export interface DayPlanAiDraft extends DayPlanAiForm { suggestionId: string; warnings: string[]; missingContext: string[]; notice: string; draftVersionId: number; draftVersionNo: number }

export const getWeekPlanAiContextApi = (weekStart: string) => http.get<unknown, AiContextResponse>('/employee/ai/week-plans/context', { params: { weekStart }, timeout: AI_OPERATION_TIMEOUT_MS })
export const generateWeekPlanAiApi = (payload: { requestId: string; weekStart: string; intentText: string; currentForm: { items: WeekPlanAiItem[] }; conversationId?: string; conciseOutput?: boolean; exampleFileName?: string; exampleContent?: string }, options?: AiRequestOptions) => http.post<typeof payload, WeekPlanAiDraft>('/employee/ai/week-plans/generate', payload, aiOperationConfig(payload.requestId, options))
export const adjustWeekPlanAiApi = (payload: { requestId: string; weekStart: string; draft: { items: WeekPlanAiItem[] }; instruction: string; targetItemIndex?: number; conversationId?: string; conciseOutput?: boolean }, options?: AiRequestOptions) => http.post<typeof payload, WeekPlanAiDraft>('/employee/ai/week-plans/adjust', payload, aiOperationConfig(payload.requestId, options))
export const getDayPlanAiContextApi = (planDate: string) => http.get<unknown, AiContextResponse>('/employee/ai/day-plans/context', { params: { planDate }, timeout: AI_OPERATION_TIMEOUT_MS })
export const generateDayPlanAiApi = (payload: { requestId: string; planDate: string; intentText: string; currentForm: DayPlanAiForm; conversationId?: string; exampleFileName?: string; exampleContent?: string }, options?: AiRequestOptions) => http.post<typeof payload, DayPlanAiDraft>('/employee/ai/day-plans/generate', payload, aiOperationConfig(payload.requestId, options))
export const adjustDayPlanAiApi = (payload: { requestId: string; planDate: string; draft: DayPlanAiForm; instruction: string; conversationId?: string }, options?: AiRequestOptions) => http.post<typeof payload, DayPlanAiDraft>('/employee/ai/day-plans/adjust', payload, aiOperationConfig(payload.requestId, options))

export interface AiGenerateResponse {
  suggestionId: string
  items: AiPlanItem[]
  warnings: string[]
  missingContext: string[]
  notice: string
  draftVersionId: number
  draftVersionNo: number
  effectiveOperationMode: Exclude<MonthPlanAiOperationMode, 'AUTO'>
  clarificationMessage?: string
  generationProfile?: PlanGenerationProfile
  generationProfileLabel?: string
  knowledgeReferences?: AiKnowledgeReference[]
}

export interface AiOptimizeResponse {
  suggestionId: string
  item: AiPlanItem
  warnings: string[]
  notice: string
  draftVersionId: number
  draftVersionNo: number
}

export interface AiCheckIssue {
  code: string
  level: 'INFO' | 'WARNING' | 'HIGH'
  fieldPath: string
  message: string
  suggestion: string
}

export interface AiCheckResponse {
  suggestionId: string
  issues: AiCheckIssue[]
  notice: string
}

export function getMonthPlanAiContextApi(planMonth: string, intentText?: string, selectedKnowledgeTypes?: string[]) {
  return http.get<unknown, AiContextResponse>('/employee/ai/month-plans/context', {
    params: {
      planMonth,
      intentText: intentText?.trim() || undefined,
      selectedKnowledgeTypes: selectedKnowledgeTypes?.join(','),
    },
    timeout: AI_OPERATION_TIMEOUT_MS,
  })
}

export function generateMonthPlanApi(payload: {
  requestId: string
  planMonth: string
  intentText: string
  currentForm: AiPlanForm
  jobDescription?: string
  selectedContextTypes?: MonthPlanContextType[]
  selectedHistoryMonths?: string[]
  selectedKnowledgeTypes?: string[]
  conversationId?: string
  operationMode?: MonthPlanAiOperationMode
  conciseOutput?: boolean
  exampleFileName?: string
  exampleContent?: string
}, options?: AiRequestOptions) {
  return http.post<typeof payload, AiGenerateResponse>('/employee/ai/month-plans/generate', payload, aiOperationConfig(payload.requestId, options))
}

export function extractPlanExampleApi(file: File) {
  const form = new FormData()
  form.append('file', file)
  return http.post<FormData, AiGenerationExample>('/employee/ai/plan-examples/extract', form, {
    timeout: 60_000,
  })
}

export function optimizeMonthPlanItemApi(payload: {
  requestId: string
  planMonth: string
  item: AiPlanItem
  currentForm: AiPlanForm
  targetItemIndex: number
  instruction?: string
  jobDescription?: string
  selectedContextTypes?: MonthPlanContextType[]
  selectedHistoryMonths?: string[]
  selectedKnowledgeTypes?: string[]
  conversationId?: string
}, options?: AiRequestOptions) {
  return http.post<typeof payload, AiOptimizeResponse>('/employee/ai/month-plans/items/optimize', payload, aiOperationConfig(payload.requestId, options))
}

export function checkMonthPlanApi(payload: {
  requestId: string
  planMonth: string
  currentForm: AiPlanForm
  jobDescription?: string
  selectedContextTypes?: MonthPlanContextType[]
  selectedHistoryMonths?: string[]
  selectedKnowledgeTypes?: string[]
  conversationId?: string
}, options?: AiRequestOptions) {
  return http.post<typeof payload, AiCheckResponse>('/employee/ai/month-plans/check', payload, aiOperationConfig(payload.requestId, options))
}

export function cancelAiRequestApi(requestId: string) {
  return http.post<undefined, { requestId: string; state: AiCancelState }>(
    `/ai/requests/${encodeURIComponent(requestId)}/cancel`,
  )
}

function aiOperationConfig(requestId: string, options?: AiRequestOptions) {
  return {
    timeout: AI_OPERATION_TIMEOUT_MS,
    signal: options?.signal,
    headers: { 'X-AI-Request-Id': options?.requestId || requestId },
  }
}

export function recordAiSuggestionActionApi(suggestionId: string, payload: {
  actionCode: 'PREVIEW' | 'APPLY_ALL' | 'APPLY_ITEM' | 'APPLY_FIELDS' | 'ADOPT_WITH_EDIT' | 'IGNORE'
  appliedFields?: string[]
  beforeHash?: string
  afterHash?: string
}) {
  return http.post<typeof payload, void>(`/employee/ai/suggestions/${suggestionId}/actions`, payload)
}

export function aiRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `ai-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
