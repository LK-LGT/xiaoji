/**
 * 🧩 **AI 助手的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``PlanAiAssistant.vue`` 有 **300 行**，其中「周条目归一化 / 字符串数组归一 /
 * 草稿构造 / 提示文案 / 结束日计算」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖 el-dialog / AI 流式请求 / 多个子组件，
 *   挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/** AI 助手模式。 */
export type PlanAiAssistantMode = 'week' | 'day'

/** 周计划 AI 条目。 */
export interface WeekPlanAiItem {
  id?: string
  monthPlanItemId: string | null
  content: string
  deliverable: string
  quality: string
  plannedFinishDate: string
}

/** AI 消息载荷（最小结构）。 */
export interface AiConversationPayload {
  suggestionId?: unknown
  items?: unknown
  content?: unknown
  remark?: unknown
  warnings?: unknown
  missingContext?: unknown
  notice?: unknown
  draftVersionId?: unknown
  draftVersionNo?: unknown
  relatedWeekPlanItemIds?: unknown
}

/** AI 会话消息（最小结构）。 */
export interface AiConversationMessageLike {
  suggestionId?: string
  payload?: AiConversationPayload | null
}

/** 周计划草稿。 */
export interface WeekPlanAiDraft {
  suggestionId: string
  items: WeekPlanAiItem[]
  warnings: string[]
  missingContext: string[]
  notice: string
  draftVersionId: number
  draftVersionNo: number
}

/** 日计划草稿。 */
export interface DayPlanAiDraft {
  suggestionId: string
  relatedWeekPlanItemIds: string[]
  content: string
  remark: string
  warnings: string[]
  missingContext: string[]
  notice: string
  draftVersionId: number
  draftVersionNo: number
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 结束日计算
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 周计划的结束日（开始日 + 6 天）。
 *
 * ⚠️ **用 ``setDate`` 而不是加 86400000 毫秒**
 *    ⇒ 跨月 / 跨年 / 夏令时都不会错。
 */
export function resolveWeekEndDate(weekStart: string): string {
  const dateValue = new Date(`${weekStart}T00:00:00`)
  dateValue.setDate(dateValue.getDate() + 6)
  return `${dateValue.getFullYear()}-${String(dateValue.getMonth() + 1).padStart(2, '0')}-${String(dateValue.getDate()).padStart(2, '0')}`
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 归一化
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 归一化字符串数组。
 *
 * ⚠️ **非数组 ⇒ 空数组**（不抛错）⇒ 载荷字段缺失时不炸。
 * ⚠️ **过滤空串**（``filter(Boolean)``）⇒ 不出现空的提示项。
 */
export function normalizeAiStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}

/**
 * 归一化周计划 AI 条目。
 *
 * ⚠️ **BUG-R6-14：条目主键是字符串 UUID，禁止 ``Number(...)`` 归一化**
 *    ⇒ ``Number('abc-123')`` 会塌成 ``NaN``，主键就丢了。
 * ⚠️ **``id`` 缺失 ⇒ ``undefined``**（不是空串）⇒ 调用方能区分「没有」和「空」。
 * ⚠️ **``monthPlanItemId`` 去空白后为空 ⇒ ``null``**。
 */
export function normalizeWeekPlanAiItem(
  raw: unknown,
  fallbackFinishDate: string,
): WeekPlanAiItem | null {
  if (!raw || typeof raw !== 'object') return null
  const item = raw as Record<string, unknown>
  const rawMonthPlanItemId = item.monthPlanItemId
  const monthPlanItemId = rawMonthPlanItemId === null || rawMonthPlanItemId === undefined
    ? ''
    : String(rawMonthPlanItemId).trim()
  return {
    id: item.id === null || item.id === undefined ? undefined : String(item.id),
    monthPlanItemId: monthPlanItemId || null,
    content: String(item.content || ''),
    deliverable: String(item.deliverable || ''),
    quality: String(item.quality || ''),
    plannedFinishDate: String(item.plannedFinishDate || fallbackFinishDate),
  }
}

/**
 * 归一化周计划 AI 条目列表（过滤掉非对象的项）。
 */
export function normalizeWeekPlanAiItems(
  raw: unknown,
  fallbackFinishDate: string,
): WeekPlanAiItem[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item) => normalizeWeekPlanAiItem(item, fallbackFinishDate))
    .filter((item): item is WeekPlanAiItem => item !== null)
}

/**
 * 归一化关联的周计划条目 ID 列表。
 *
 * ⚠️ **BUG-R6-14：ID 是字符串 UUID，禁止 ``Number(...)`` 归一化**
 *    ⇒ 一律转字符串 + 去空白 + 去空。
 */
export function normalizeRelatedWeekPlanItemIds(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter(Boolean)
    : []
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 草稿构造
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 构造周计划草稿。
 *
 * ⚠️ **没有可应用的条目 ⇒ 返回 ``null``**
 *    ⇒ 调用方据此提示「这条 AI 消息没有可应用的周计划建议」。
 */
export function buildWeekPlanAiDraft(
  message: AiConversationMessageLike,
  fallbackFinishDate: string,
): WeekPlanAiDraft | null {
  const payload = message.payload || {}
  const items = normalizeWeekPlanAiItems(payload.items, fallbackFinishDate)
  if (!items.length) return null
  return {
    suggestionId: message.suggestionId || String(payload.suggestionId || ''),
    items,
    warnings: normalizeAiStringArray(payload.warnings),
    missingContext: normalizeAiStringArray(payload.missingContext),
    notice: String(payload.notice || ''),
    draftVersionId: Number(payload.draftVersionId || 0),
    draftVersionNo: Number(payload.draftVersionNo || 0),
  }
}

/**
 * 构造日计划草稿。
 *
 * ⚠️ **``content`` 不是字符串 ⇒ 返回 ``null``**
 *    ⇒ 调用方据此提示「这条 AI 消息没有可应用的日计划建议」。
 */
export function buildDayPlanAiDraft(
  message: AiConversationMessageLike,
): DayPlanAiDraft | null {
  const payload = message.payload || {}
  if (typeof payload.content !== 'string') return null
  return {
    suggestionId: message.suggestionId || String(payload.suggestionId || ''),
    relatedWeekPlanItemIds: normalizeRelatedWeekPlanItemIds(payload.relatedWeekPlanItemIds),
    content: payload.content,
    remark: typeof payload.remark === 'string' ? payload.remark : '',
    warnings: normalizeAiStringArray(payload.warnings),
    missingContext: normalizeAiStringArray(payload.missingContext),
    notice: String(payload.notice || ''),
    draftVersionId: Number(payload.draftVersionId || 0),
    draftVersionNo: Number(payload.draftVersionNo || 0),
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 提示文案
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 没有可应用内容时的提示。
 */
export function resolveAiDraftMissingMessage(mode: PlanAiAssistantMode): string {
  return mode === 'week'
    ? '这条 AI 消息没有可应用的周计划建议'
    : '这条 AI 消息没有可应用的日计划建议'
}

/**
 * 空态描述。
 */
export function resolveAiAssistantEmptyDescription(mode: PlanAiAssistantMode): string {
  return mode === 'week'
    ? '描述本周工作重点和交付要求，生成周计划建议'
    : '描述今日工作重点和交付要求，生成日计划建议'
}

/**
 * 输入框占位文案。
 *
 * ⚠️ **已有草稿 ⇒ 「继续补充要求或调整当前建议」**。
 */
export function resolveAiComposerPlaceholder(
  mode: PlanAiAssistantMode,
  hasDraft: boolean,
): string {
  if (hasDraft) return '继续补充要求或调整当前建议'
  return mode === 'week'
    ? '描述本周工作重点和交付要求'
    : '描述今日工作重点和交付要求'
}

/**
 * 发送按钮文案。
 *
 * ⚠️ **已有草稿 ⇒ 「继续调整」（走调整接口）；否则 ⇒ 「生成xxx」**。
 */
export function resolveAiComposerSendLabel(
  mode: PlanAiAssistantMode,
  hasDraft: boolean,
): string {
  if (hasDraft) return '继续调整'
  return mode === 'week' ? '生成周计划' : '生成日计划'
}

/**
 * 发送时走「生成」还是「调整」。
 *
 * ⚠️ **有草稿 ⇒ 调整；无草稿 ⇒ 生成**。
 */
export function shouldAdjustAiDraft(hasDraft: boolean): boolean {
  return hasDraft
}
