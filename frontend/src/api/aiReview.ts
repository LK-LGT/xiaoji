import { AI_OPERATION_TIMEOUT_MS, http } from './http'
import type { AiRequestOptions } from './employeeAi'

export type AiReviewBizType = 'MONTH_PLAN' | 'WEEK_PLAN' | 'DAY_PLAN' | 'RESULT'
export type AiRiskLevel = 'LOW' | 'MEDIUM' | 'HIGH'
export type AiIssueSeverity = 'LOW' | 'MEDIUM' | 'HIGH' | 'BLOCKING'

export interface AiReviewIssue {
  code: string
  source: 'RULE' | 'AI'
  severity: AiIssueSeverity
  field: string
  title: string
  ruleId: string
  quote: string
  basis: string
  suggestion: string
  confidence: number
  references: string[]
}

export interface AiAcceptanceCoverage {
  criterionId: string
  criterion: string
  status: 'PROVEN' | 'PARTIAL' | 'UNPROVEN' | 'UNKNOWN'
  basis: string
  evidenceQuote: string
  confidence: number
  evidenceReferences: string[]
}

export interface AiAnalysisDimension {
  ruleId: string
  title: string
  status: 'PASS' | 'RISK' | 'UNKNOWN' | 'NOT_RUN'
  conclusion: string
  quote: string
  basis: string
  confidence: number
  references: string[]
}

export interface AiReviewResult {
  overallRisk: AiRiskLevel
  summary: string
  issues: AiReviewIssue[]
  analysisDimensions?: AiAnalysisDimension[]
  acceptanceCoverage: AiAcceptanceCoverage[]
  suggestedCompletionMin?: number
  suggestedCompletionMax?: number
  evidenceStatus?: 'SUFFICIENT' | 'PARTIAL' | 'INSUFFICIENT' | 'UNKNOWN'
  declaredCompletionRate?: number
  completionCalculationBasis?: string
}

export interface AiReview {
  id: number
  bizType: AiReviewBizType
  bizId: number
  bizVersion?: string
  contentHash: string
  status: 'SUCCESS' | 'RULE_ONLY' | 'MODEL_FAILED'
  overallRisk: AiRiskLevel
  provider: string
  modelName: string
  promptVersion: string
  checkedAt: string
  stale?: boolean
  modelEnabled: boolean
  errorMessage?: string
  result: AiReviewResult
}

export interface AiCapability {
  modelEnabled: boolean
  mode: 'CONFIGURED_AI' | 'RULE_ONLY'
  provider: string
  modelName: string
  promptVersion: string
  message: string
}

let capabilityRequest: Promise<AiCapability> | null = null

export function getAiCapabilityApi(force = false) {
  if (force || !capabilityRequest) {
    const request = http.get<unknown, AiCapability>('/ai/reviews/capability')
    capabilityRequest = request
    const clear = () => {
      if (capabilityRequest === request) capabilityRequest = null
    }
    void request.then(clear, clear)
  }
  return capabilityRequest
}

export function checkPlanAiReviewApi(bizType: Exclude<AiReviewBizType, 'RESULT'>, bizId: number, options?: AiRequestOptions) {
  return http.post<undefined, AiReview>(`/ai/reviews/plans/${bizType}/${bizId}/check`, undefined, {
    ...reviewRequestConfig(options),
  })
}

export function ensurePlanAiReviewApi(bizType: Exclude<AiReviewBizType, 'RESULT'>, bizId: number) {
  return http.post<undefined, AiReview>(`/ai/reviews/plans/${bizType}/${bizId}/ensure`, undefined, {
    timeout: AI_OPERATION_TIMEOUT_MS,
  })
}

export function previewResultAiReviewApi(payload: FormData, options?: AiRequestOptions) {
  return http.post<FormData, AiReview>('/ai/reviews/results/preview', payload, {
    ...reviewRequestConfig(options),
    headers: { 'Content-Type': 'multipart/form-data', ...(options ? { 'X-AI-Request-Id': options.requestId } : {}) },
  })
}

export function getLatestAiReviewApi(bizType: AiReviewBizType, bizId: number) {
  return http.get<unknown, AiReview | null>('/ai/reviews/latest', { params: { bizType, bizId } })
}

export function getLatestAiReviewsApi(bizType: AiReviewBizType, bizIds: number[]) {
  return http.get<unknown, Record<number, AiReview>>('/ai/reviews/latest/batch', {
    params: { bizType, bizIds: [...new Set(bizIds.filter((id) => Number.isInteger(id) && id > 0))].join(',') },
  })
}

function reviewRequestConfig(options?: AiRequestOptions) {
  return {
    timeout: AI_OPERATION_TIMEOUT_MS,
    signal: options?.signal,
    headers: options ? { 'X-AI-Request-Id': options.requestId } : undefined,
  }
}
