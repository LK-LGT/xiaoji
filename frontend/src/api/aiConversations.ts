import { AI_OPERATION_TIMEOUT_MS, http } from './http'

export type AiPlanType = 'MONTH' | 'WEEK' | 'DAY'
export type AiConversationStatus = 'ACTIVE' | 'ENDED'
export type AiConversationMessageStatus = 'PENDING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export interface AiConversation {
  // ⚠️ 会话主键是 UUID 字符串（后端 `AiConversation.id: String(64)`），
  // 历史类型声明为 number 会导致把 UUID 发回后端时 422。
  id: string
  planType: AiPlanType
  periodKey: string
  title: string
  status: AiConversationStatus
  createdAt: string
  updatedAt: string
  endedAt: string
}

export interface AiConversationMessage {
  id: string
  conversationId: string
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
  nextBeforeId?: string
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

export function listAiConversationMessages(conversationId: string, beforeId?: string) {
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
