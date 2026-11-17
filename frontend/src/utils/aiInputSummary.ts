export interface AiInputOverview {
  employee: string
  department: string
  period: string
  intent: string
  currentFormItemCount: number
  emptyFormItemCount: number
  relatedWeekItemCount: number
}

import { periodMonthKey } from './period.ts'

export interface AiParentOptionView {
  id: string
  taskName: string
  taskContent: string
  deliverable: string
}

export interface AiParentGroupView {
  planMonth: string
  periodFlag: '' | '非当前周期' | '未来周期'
  items: AiParentOptionView[]
}

export interface AiRelatedWeekItemView {
  id: string
  monthPlanItemId: string
  content: string
  deliverable: string
  quality: string
  plannedFinishDate: string
}

export interface AiKnowledgeView {
  knowledgeId: string
  title: string
  version: string
  citation: string
  content: string
  knowledgeType: string
  binding: string
  bindingLabel: string
}

export interface AiConversationMemoryView {
  summary: string
  recentMessages: Record<string, unknown>[]
  instruction: string
  hasHistory: boolean
}

export interface AiInputSummaryView {
  parsed: boolean
  rawText: string
  prettyRawText: string
  overview: AiInputOverview
  parentGroups: AiParentGroupView[]
  relatedWeekItems: AiRelatedWeekItemView[]
  knowledgeItems: AiKnowledgeView[]
  memory: AiConversationMemoryView
  priorityLabels: string[]
  knowledgeUsageNotice: string
  warnings: string[]
  sections: {
    parentOptions: boolean
    relatedWeekItems: boolean
    knowledge: boolean
    memory: boolean
    rules: boolean
  }
}

const CONTEXT_PRIORITY_LABELS: Record<string, string> = {
  DETERMINISTIC_BUSINESS_RULES: '系统硬规则',
  APPROVED_PARENT_PLAN: '已审批父级计划',
  PUBLISHED_ENTERPRISE_KNOWLEDGE: '已发布企业知识',
  EMPLOYEE_INTENT: '员工意图',
}

const EMPTY_OVERVIEW: AiInputOverview = {
  employee: '未记录',
  department: '未记录',
  period: '未记录',
  intent: '未记录',
  currentFormItemCount: 0,
  emptyFormItemCount: 0,
  relatedWeekItemCount: 0,
}

const EMPTY_MEMORY: AiConversationMemoryView = {
  summary: '',
  recentMessages: [],
  instruction: '',
  hasHistory: false,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasOwn(value: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key)
}

function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

function stringValue(value: unknown, fallback = '未记录'): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback
}

function idValue(value: unknown): string {
  return value === undefined || value === null || value === '' ? '未记录' : String(value)
}

function prettyJson(value: unknown): string {
  if (value === undefined || value === null || value === '') return '未记录'
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function parseObject(value: unknown): Record<string, unknown> | null {
  if (typeof value === 'string') {
    if (!value.trim()) return null
    try {
      const parsed: unknown = JSON.parse(value)
      return isRecord(parsed) ? parsed : null
    } catch {
      return null
    }
  }
  return isRecord(value) ? value : null
}

function periodText(context: Record<string, unknown>): string {
  const weekStart = stringValue(context.weekStart, '')
  const weekEnd = stringValue(context.weekEnd, '')
  if (weekStart && weekEnd) return `${weekStart} 至 ${weekEnd}`
  const planMonth = stringValue(context.planMonth, '')
  if (planMonth) return planMonth
  return stringValue(context.planDate)
}

function currentItems(context: Record<string, unknown>): Record<string, unknown>[] {
  const currentForm = parseObject(context.currentForm)
  if (currentForm) return recordList(currentForm.items)
  const draft = parseObject(context.draft)
  return draft ? recordList(draft.items) : []
}

function isEmptyPlanItem(item: Record<string, unknown>): boolean {
  return !stringValue(item.content, '')
    && !stringValue(item.deliverable, '')
    && !stringValue(item.quality, '')
    && !stringValue(item.acceptanceStandard, '')
}

function parentGroups(context: Record<string, unknown>, targetMonth: string): AiParentGroupView[] {
  const groups = new Map<string, AiParentOptionView[]>()
  for (const item of recordList(context.parentOptions)) {
    const planMonth = stringValue(item.planMonth, '未标注月份')
    const items = groups.get(planMonth) ?? []
    items.push({
      id: idValue(item.id),
      taskName: stringValue(item.taskName),
      taskContent: stringValue(item.taskContent),
      deliverable: stringValue(item.deliverable),
    })
    groups.set(planMonth, items)
  }
  return Array.from(groups, ([planMonth, items]) => {
    let periodFlag: AiParentGroupView['periodFlag'] = ''
    if (targetMonth && planMonth !== '未标注月份' && planMonth !== targetMonth) {
      periodFlag = planMonth > targetMonth ? '未来周期' : '非当前周期'
    }
    return { planMonth, periodFlag, items }
  })
}

function relatedWeekItems(context: Record<string, unknown>): AiRelatedWeekItemView[] {
  return recordList(context.relatedWeekItems).map((item) => ({
    id: idValue(item.id),
    monthPlanItemId: idValue(item.monthPlanItemId),
    content: stringValue(item.content),
    deliverable: stringValue(item.deliverable),
    quality: stringValue(item.quality ?? item.acceptanceStandard),
    plannedFinishDate: stringValue(item.plannedFinishDate),
  }))
}

function knowledgeItems(context: Record<string, unknown>): AiKnowledgeView[] {
  return recordList(context.enterpriseKnowledge).map((item) => {
    const binding = stringValue(item.binding, '')
    const bindingLabel = binding === 'NORMATIVE'
      ? '规范依据'
      : binding === 'REFERENCE'
        ? '参考资料'
        : binding === 'EXAMPLE' ? '示例资料' : '未标注'
    return {
      knowledgeId: stringValue(item.knowledgeId),
      title: stringValue(item.title),
      version: stringValue(item.version),
      citation: stringValue(item.citation),
      content: stringValue(item.content),
      knowledgeType: stringValue(item.knowledgeType),
      binding,
      bindingLabel,
    }
  })
}

function conversationMemory(context: Record<string, unknown>): AiConversationMemoryView {
  const value = parseObject(context.conversationMemory)
  if (!value) return { ...EMPTY_MEMORY }
  const summary = stringValue(value.summary, '')
  const recentMessages = recordList(value.recentMessages)
  return {
    summary,
    recentMessages,
    instruction: stringValue(value.instruction, ''),
    hasHistory: Boolean(summary || recentMessages.length),
  }
}

function fallback(rawText: string): AiInputSummaryView {
  const hasRawText = Boolean(rawText.trim())
  return {
    parsed: false,
    rawText,
    prettyRawText: hasRawText ? rawText : '未记录',
    overview: { ...EMPTY_OVERVIEW },
    parentGroups: [],
    relatedWeekItems: [],
    knowledgeItems: [],
    memory: { ...EMPTY_MEMORY },
    priorityLabels: [],
    knowledgeUsageNotice: '',
    warnings: [hasRawText ? '输入 JSON 无法解析或疑似被截断' : '输入摘要未记录'],
    sections: { parentOptions: false, relatedWeekItems: false, knowledge: false, memory: false, rules: false },
  }
}

export function parseAiInputSummary(value: unknown): AiInputSummaryView {
  const rawText = typeof value === 'string' ? value : prettyJson(value)
  const context = parseObject(value)
  if (!context) return fallback(rawText)

  const items = currentItems(context)
  const related = relatedWeekItems(context)
  // ⚠️ **BUG-R6-26**：统一走 `periodMonthKey`（不再手写 `slice(0, 7)`）。
  //    三种来源格式可能各不相同（紧凑 / 带横杠 / 日期），periodMonthKey 都能吃。
  const targetMonth = periodMonthKey(stringValue(context.weekStart, ''))
    || periodMonthKey(stringValue(context.planMonth, ''))
    || periodMonthKey(stringValue(context.planDate, ''))
  const parents = parentGroups(context, targetMonth)
  const knowledge = knowledgeItems(context)
  const memory = conversationMemory(context)
  const intent = stringValue(context.intentText ?? context.instruction)
  const overview: AiInputOverview = {
    employee: stringValue(context.employee),
    department: stringValue(context.department),
    period: periodText(context),
    intent,
    currentFormItemCount: items.length,
    emptyFormItemCount: items.filter(isEmptyPlanItem).length,
    relatedWeekItemCount: related.length,
  }

  const warnings: string[] = []
  if (intent === '未记录' || intent.length < 6) warnings.push('员工意图为空或过短，模型可能缺少明确任务目标')
  if (parents.some((group) => group.periodFlag)) warnings.push('父级计划候选包含跨月份任务，请核对模型是否选对周期')
  if ((hasOwn(context, 'currentForm') || hasOwn(context, 'draft'))
      && items.length > 0 && overview.emptyFormItemCount === items.length) {
    warnings.push('当前表单为空，本次结果主要依赖意图和上下文生成')
  }
  if (hasOwn(context, 'relatedWeekItems') && !related.length) warnings.push('本周没有已有任务')
  if (hasOwn(context, 'enterpriseKnowledge') && !knowledge.length) warnings.push('企业知识为空')
  if (hasOwn(context, 'conversationMemory') && !memory.hasHistory) warnings.push('会话没有历史记忆')

  return {
    parsed: true,
    rawText,
    prettyRawText: prettyJson(context),
    overview,
    parentGroups: parents,
    relatedWeekItems: related,
    knowledgeItems: knowledge,
    memory,
    priorityLabels: stringList(context.contextPriority).map((item) => CONTEXT_PRIORITY_LABELS[item] ?? item),
    knowledgeUsageNotice: stringValue(context.knowledgeUsageNotice, ''),
    warnings,
    sections: {
      parentOptions: hasOwn(context, 'parentOptions'),
      relatedWeekItems: hasOwn(context, 'relatedWeekItems'),
      knowledge: hasOwn(context, 'enterpriseKnowledge'),
      memory: hasOwn(context, 'conversationMemory'),
      rules: hasOwn(context, 'contextPriority') || hasOwn(context, 'knowledgeUsageNotice'),
    },
  }
}
