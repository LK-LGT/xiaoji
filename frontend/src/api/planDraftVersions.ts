import { http } from './http'

export type PlanDraftVersionPlanType = 'MONTH' | 'WEEK' | 'DAY'
export type PlanDraftVersionSource = 'AI_GENERATE' | 'AI_OPTIMIZE' | 'MANUAL_SAVE' | 'RESTORED_SAVE'

export interface PlanDraftVersionSummary {
  id: number
  planType: PlanDraftVersionPlanType
  periodKey: string
  planId?: number
  versionNo: number
  sourceType: PlanDraftVersionSource
  snapshotSchemaVersion: number
  baseVersionId?: number
  conversationId?: string
  messageId?: string
  suggestionId?: string
  requestId?: string
  createdAt: string
}

export interface PlanDraftVersionDetail extends PlanDraftVersionSummary {
  snapshot: Record<string, unknown>
}

export function listPlanDraftVersionsApi(planType: PlanDraftVersionPlanType, periodKey: string) {
  return http.get<unknown, PlanDraftVersionSummary[]>('/employee/plan-draft-versions', {
    params: { planType, periodKey },
  })
}

export function getPlanDraftVersionApi(id: number) {
  return http.get<unknown, PlanDraftVersionDetail>(`/employee/plan-draft-versions/${id}`)
}

export function restorePlanDraftVersionApi(id: number, planType: PlanDraftVersionPlanType, periodKey: string) {
  return http.get<unknown, PlanDraftVersionDetail>(`/employee/plan-draft-versions/${id}/restore`, {
    params: { planType, periodKey },
  })
}
