import { AI_OPERATION_TIMEOUT_MS, http } from './http'

export type AiPlanType = 'MONTH' | 'WEEK' | 'DAY'
export type AiConversationStatus = 'ACTIVE' | 'ENDED'
export type AiConversationMessageStatus = 'PENDING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export interface AiConversation {
  id: number
  planType: AiPlanType
  periodKey: string
  title: string
  status: AiConversationStatus
  createdAt: string
  updatedAt: string
  endedAt: string
}

export interface AiConversationMessage {
  id: number
  conversationId: number
  role: 'USER' | 'ASSISTANT' | 'EVENT'
  messageType: 'TEXT' | 'BUSINESS_RESULT' | 'ACTION'
  operationCode: string
  status: AiConversationMessageStatus
  textContent: string
  payload?: Record<string, unknown>
  suggestionId?: string
  requestId?: string
  errorMessage?: string
  createdAt: string
}

export interface AiConversationMessagePage {
  records: AiConversationMessage[]
  nextBeforeId?: number
  hasMore: boolean
}

export function resolveAiConversation(planType: AiPlanType, periodKey: string) {
  return http.get<unknown, AiConversation>('/employee/ai/conversations/active', {
    params: { planType, periodKey },
    timeout: AI_OPERATION_TIMEOUT_MS,
  })
}

export function listAiConversations(planType: AiPlanType, periodKey: string) {
  return http.get<unknown, AiConversation[]>('/employee/ai/conversations/history', {
    params: { planType, periodKey },
    timeout: AI_OPERATION_TIMEOUT_MS,
  })
}

export function listAiConversationMessages(conversationId: number, beforeId?: number) {
  return http.get<unknown, AiConversationMessagePage>(`/employee/ai/conversations/${conversationId}/messages`, {
    params: { beforeId, size: 30 },
    timeout: AI_OPERATION_TIMEOUT_MS,
  })
}

export function startNewAiConversation(planType: AiPlanType, periodKey: string) {
  return http.post<unknown, AiConversation>('/employee/ai/conversations/new', { planType, periodKey }, {
    timeout: AI_OPERATION_TIMEOUT_MS,
  })
}
