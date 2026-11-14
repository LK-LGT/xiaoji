export type TracePresentationMetadata = {
  input?: Record<string, unknown>
  action?: string
  evidence?: unknown[]
  output?: Record<string, unknown>
  impact?: Record<string, unknown>
  technical?: Record<string, unknown>
  legacy?: boolean
}

export type TracePresentationEvent = {
  id: number
  eventCode: string
  status: 'RUNNING' | 'SUCCESS' | 'FAILED' | 'SKIPPED' | 'CANCELLED'
  summary?: string
  metadata?: unknown
  durationMs?: number
  startedAt?: string
  finishedAt?: string
  errorCode?: string
  errorMessage?: string
}

const semanticLabels: Record<string, string> = {
  REQUEST_RECEIVED: '目标识别',
  MODE_DECISION: '模式决策',
  CONTEXT_BUILD: '上下文准备',
  KNOWLEDGE_RETRIEVAL: '证据检索',
  PROMPT_PREPARE: '请求编排',
  MODEL_ATTEMPT: '模型生成',
  OUTPUT_VALIDATE: '结果校验',
  REPAIR_ATTEMPT: '自动修复',
  PERSIST_RESULT: '业务结果保存',
  CONVERSATION_UPDATE: '会话更新',
  COMPLETED: '执行完成',
  FAILED: '执行终止',
  CANCELLED: '执行取消',
  IDEMPOTENCY_HIT: '复用已有结果',
  CALL_LOG_PERSIST: '技术日志保存',
}

export function parseTraceMetadata(value: unknown): TracePresentationMetadata {
  let parsed = value
  if (typeof parsed === 'string') {
    try { parsed = JSON.parse(parsed) } catch { return { legacy: true } }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { legacy: true }
  const record = parsed as Record<string, unknown>
  const semantic = ['input', 'action', 'evidence', 'output', 'impact', 'technical']
  if (!semantic.some((key) => key in record)) return { technical: record, legacy: true }
  return {
    input: isRecord(record.input) ? record.input : undefined,
    action: typeof record.action === 'string' ? record.action : undefined,
    evidence: Array.isArray(record.evidence) ? record.evidence : [],
    output: isRecord(record.output) ? record.output : undefined,
    impact: isRecord(record.impact) ? record.impact : undefined,
    technical: isRecord(record.technical) ? record.technical : undefined,
  }
}

export function normalizeTraceEvent(event: TracePresentationEvent) {
  const metadata = parseTraceMetadata(event.metadata)
  const technical = metadata.technical || {}
  const attempt = typeof technical.attempt === 'number' ? technical.attempt : undefined
  return {
    ...event,
    label: semanticLabels[event.eventCode] || event.eventCode || '未命名阶段',
    metadata,
    attempt,
    technical: event.eventCode === 'CALL_LOG_PERSIST' || metadata.legacy,
    branchKey: attempt ? `attempt-${attempt}` : 'main',
  }
}

export function isTechnicalEvent(eventCode: string) {
  return eventCode === 'CALL_LOG_PERSIST'
}

export function semanticEvidenceText(value: unknown) {
  if (!value || typeof value !== 'object') return String(value || '')
  const record = value as Record<string, unknown>
  return [record.title, record.knowledgeId, record.version, record.citation, record.summary]
    .filter((item) => item !== undefined && item !== null && String(item).trim())
    .map(String).join(' · ')
}

export function suggestionActionLabel(action?: string) {
  if (action === 'APPLY_ALL') return '全部采纳'
  if (action === 'APPLY_ITEM') return '采纳单条'
  if (action === 'APPLY_FIELDS') return '部分采纳'
  if (action === 'ADOPT_WITH_EDIT') return '人工修改后采纳'
  if (action === 'PREVIEW') return '已预览'
  if (action === 'IGNORE') return '已忽略'
  return '尚未反馈'
}

export function traceCountSummary(value: Record<string, unknown>) {
  const parts: string[] = []
  const generatedItemCount = numberValue(value.generatedItemCount)
  const changedFieldCount = numberValue(value.changedFieldCount)
  const draftVersionNo = numberValue(value.draftVersionNo)
  if (generatedItemCount !== undefined) parts.push(`生成 ${generatedItemCount} 条`)
  if (changedFieldCount !== undefined) parts.push(`变更 ${changedFieldCount} 个字段`)
  if (draftVersionNo !== undefined) parts.push(`草稿 V${draftVersionNo}`)
  return parts.join(' · ')
}

export function traceFieldList(value: Record<string, unknown>) {
  return stringList(value.failedFields)
}

export function traceValidationMessages(value: Record<string, unknown>) {
  return stringList(value.validationMessages)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item) => typeof item === 'string') : []
}
