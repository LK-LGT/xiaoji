import { AI_OPERATION_TIMEOUT_MS, http } from './http'

export interface AiPlanContext {
  id?: number
  /** 组织 ID（飞书 department_id，string）。 */
  orgId: string
  orgName: string
  planMonth: string
  departmentGoal: string
  leaderRequirement: string
  versionNo: number
  updatedAt: string
}

export interface AiModelConfig {
  id: number
  configName: string
  providerCode: 'OPENAI_COMPATIBLE' | 'ALIYUN'
  baseUrl: string
  apiKeyConfigured: boolean
  modelName: string
  timeoutSeconds: number
  globalEnabled: boolean
  draftEnabled: boolean
  optimizeEnabled: boolean
  checkEnabled: boolean
  allowedUserIds: string
  allowedOrgIds: string
  draftDailyLimit: number
  optimizeDailyLimit: number
  checkDailyLimit: number
  versionNo: number
  status: 'ENABLED' | 'DISABLED'
}

export type AiModelConfigPayload = Omit<AiModelConfig, 'id' | 'apiKeyConfigured' | 'status'> & { apiKey?: string }

export interface AiPrompt {
  id: number
  sceneCode: string
  versionNo: string
  systemPrompt: string
  userTemplate: string
  outputSchemaVersion: string
  status: string
  createdAt: string
}

export interface AiMetrics {
  totalCalls: number
  successCalls: number
  successRate: number
  inputTokens: number
  outputTokens: number
  averageLatencyMs: number
  adoptedSuggestions: number
  adoptionRate: number
  byScene: Array<Record<string, string | number>>
}

export interface AiCallLogFilters {
  start?: string
  end?: string
  sceneCode?: string
  success?: boolean
  userId?: string
  orgId?: string
  requestId?: string
  suggestionId?: string
  pageNo?: number
  pageSize?: number
}

export interface AiCallLogRow {
  id: number
  createdAt: string
  sceneCode: string
  success: boolean
  /** 操作人 ID（飞书 user_id，string）。 */
  userId: string
  orgId?: string
  bizType?: string
  bizId?: number
  providerCode?: string
  modelName?: string
  promptVersion?: string
  inputTokens: number
  outputTokens: number
  latencyMs: number
  errorCode?: string
  errorMessage?: string
  requestId: string
  suggestionId: string
}

export type AiTraceStatus = 'RUNNING' | 'SUCCESS' | 'FAILED' | 'SKIPPED' | 'CANCELLED'

export interface AiTraceEvent {
  id: number
  eventCode: string
  status: AiTraceStatus
  startedAt: string
  finishedAt?: string
  durationMs: number
  summary?: string
  metadata?: unknown
  errorCode?: string
  errorMessage?: string
}

export interface AiCallLogDetail extends AiCallLogRow {
  inputHash: string
  inputSummary?: string
  outputJson?: unknown
  suggestionAction?: string
  traceEvents: AiTraceEvent[]
}

export interface AiCallLogPage {
  items: AiCallLogRow[]
  total: number
  pageNo: number
  pageSize: number
}

export function getLeaderAiContextApi(planMonth: string, orgId: string) {
  return http.get<unknown, AiPlanContext>('/leader/ai/month-plan-context', { params: { planMonth, orgId } })
}

export function saveLeaderAiContextApi(payload: Pick<AiPlanContext, 'orgId' | 'planMonth' | 'departmentGoal' | 'leaderRequirement' | 'versionNo'>) {
  return http.put<typeof payload, AiPlanContext>('/leader/ai/month-plan-context', payload)
}

export function listAiConfigsApi() {
  return http.get<unknown, AiModelConfig[]>('/system/ai/configs')
}

export function createAiConfigApi(payload: AiModelConfigPayload) {
  return http.post<typeof payload, AiModelConfig>('/system/ai/configs', payload)
}

export function updateAiConfigApi(id: number, payload: AiModelConfigPayload) {
  return http.put<typeof payload, AiModelConfig>(`/system/ai/configs/${id}`, payload)
}

export function testAiConnectionApi(id: number) {
  return http.post<undefined, { success: boolean; providerCode: string; modelName: string; latencyMs: number; message: string }>(`/system/ai/configs/${id}/test`, undefined, { timeout: AI_OPERATION_TIMEOUT_MS })
}

export function enableAiConfigApi(id: number) {
  return http.post<undefined, AiModelConfig>(`/system/ai/configs/${id}/enable`)
}

export function listAiPromptsApi() {
  return http.get<unknown, AiPrompt[]>('/system/ai/prompts')
}

export function publishAiPromptApi(payload: { sceneCode: string; versionNo: string; systemPrompt: string; userTemplate: string; outputSchemaVersion: string }) {
  return http.post<typeof payload, AiPrompt>('/system/ai/prompts/publish', payload)
}

export function getAiMetricsApi(days = 30) {
  return http.get<unknown, AiMetrics>('/system/ai/metrics', { params: { days } })
}

export function getAiCallLogsApi(filters: AiCallLogFilters) {
  return http.get<unknown, AiCallLogPage>('/system/ai/call-logs', { params: filters })
}

export function getAiCallLogDetailApi(id: number) {
  return http.get<unknown, AiCallLogDetail>(`/system/ai/call-logs/${id}`)
}
