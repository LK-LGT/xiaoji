/**
 * 🧩 **AI 对话面板的纯逻辑**（CI-5 阶段 2 第四轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``PlanAiConversationPanel.vue`` 有 **494 行**，其中「角色标签 / 操作码映射 /
 * 消息正文渲染 / 结果摘要拼装 / 可预览判定」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖路由 / 多个 API / 滚动容器 / SSE 轮询，挂载失败难定位。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

export type AiConversationRole = 'USER' | 'ASSISTANT' | 'EVENT'

export interface AiConversationPayload {
  items?: unknown[]
  issues?: unknown[]
  item?: unknown
  content?: string
  [key: string]: unknown
}

/**
 * ⚠️ **消息的最小结构**。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型
 * （例如 ``AiConversationMessage`` 还有 ``id`` / ``status`` / ``errorMessage``）。
 */
export interface AiConversationMessageLike {
  role: AiConversationRole
  textContent?: string
  operationCode?: string
  payload?: AiConversationPayload | null
  submittedAt?: string
  createdAt?: string
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 角色与操作码标签
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 消息角色 ⇒ 展示名。
 *
 * ⚠️ ``EVENT`` 是「操作记录」⇒ 不是用户也不是 AI。
 */
export function resolveAiMessageRoleLabel(role: AiConversationRole): string {
  if (role === 'USER') return '我'
  if (role === 'ASSISTANT') return 'AI 助手'
  return '操作记录'
}

/** 操作码 ⇒ 中文文案。 */
export const AI_OPERATION_LABELS: Record<string, string> = {
  MONTH_PLAN_DRAFT: '生成月计划',
  MONTH_PLAN_ITEM_OPTIMIZE: '优化计划事项',
  MONTH_PLAN_CHECK: '检查月计划',
  WEEK_PLAN_DRAFT: '生成周计划',
  WEEK_PLAN_ADJUST: '调整周计划',
  DAY_PLAN_DRAFT: '生成日计划',
  DAY_PLAN_ADJUST: '调整日计划',
  PREVIEW: '已查看建议',
  APPLY_ALL: '已应用到草稿',
  APPLY_ITEM: '已应用到草稿',
  APPLY_FIELDS: '已应用到草稿',
  ADOPT_WITH_EDIT: '修改后采纳',
  IGNORE: '已忽略',
}

/**
 * 操作码 ⇒ 中文文案（未知码原样返回）。
 *
 * ⚠️ **空码 ⇒ 空串**（不是 undefined）⇒ 模板里不用再判空。
 */
export function resolveAiOperationLabel(code?: string): string {
  if (!code) return ''
  return AI_OPERATION_LABELS[code] || code
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 消息正文渲染
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 消息正文。
 *
 * ⚠️ **三段语义完全不同**：
 *   ① ``EVENT`` ⇒ 只显示操作码（映射后），没有就显示「操作已记录」；
 *   ② **正文为空** ⇒ 显示映射后的操作码，没有就显示「内容已记录」；
 *   ③ **正文非空** ⇒ 显示正文 —— **除非正文本身就是那个未映射的操作码**
 *      （这时正文是机器码，要换成中文）。
 */
export function resolveAiMessageBody<M extends AiConversationMessageLike>(message: M): string {
  const raw = (message.textContent || '').trim()
  const mapped = resolveAiOperationLabel(message.operationCode || raw)
  if (message.role === 'EVENT') return mapped || '操作已记录'
  if (!raw) return mapped || '内容已记录'
  return mapped && raw === (message.operationCode || raw) ? mapped : raw
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 时间格式化
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 紧凑时间（只显示时分）。
 *
 * ⚠️ **长度不足 16 位就原样返回** ⇒ 已经是「HH:mm」或空字符串时不截断出乱码。
 */
export function formatAiCompactTime(value?: string): string {
  if (!value) return ''
  const normalized = value.replace('T', ' ')
  return normalized.length >= 16 ? normalized.slice(11, 16) : normalized
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 结果摘要
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 结构化结果 ⇒ 可读摘要。
 *
 * ⚠️ **按 payload 的形状分派**（顺序很重要）：
 *   ① ``items`` ⇒ 逐条列内容 / 交付物 / 质量；
 *   ② ``issues`` ⇒ 只报条数；
 *   ③ ``item`` ⇒ 单条优化建议；
 *   ④ ``content`` ⇒ 直接用内容；
 *   ⑤ **兜底** ⇒ 用正文或「结构化建议已保存」。
 * ⚠️ **``items`` 是空数组 ⇒ 「AI 未返回计划建议」**（不是空串）。
 */
export function buildAiResultSummary<M extends AiConversationMessageLike>(message: M): string {
  const payload = message.payload || {}
  if (Array.isArray(payload.items)) {
    return payload.items.map((raw, index) => {
      const item = raw as Record<string, unknown>
      return [
        `${index + 1}. ${String(item.content || 'AI 未返回工作内容')}`,
        item.deliverable ? `交付物：${String(item.deliverable)}` : '',
        item.quality ? `质量：${String(item.quality)}` : '',
      ].filter(Boolean).join('\n')
    }).join('\n\n') || 'AI 未返回计划建议'
  }
  if (Array.isArray(payload.issues)) return `共 ${payload.issues.length} 条检查提示`
  if (payload.item) return '1 条计划事项优化建议'
  if (typeof payload.content === 'string') return payload.content
  return message.textContent || '结构化建议已保存'
}

/**
 * 该消息能否「预览结果」。
 *
 * ⚠️ **只有 AI 助手发的、且带结构化 payload（``items`` 或 ``content``）才行**
 *    ⇒ 用户自己发的消息不显示预览按钮。
 */
export function canPreviewAiResult<M extends AiConversationMessageLike>(message: M): boolean {
  if (message.role !== 'ASSISTANT' || !message.payload) return false
  const payload = message.payload
  return Array.isArray(payload.items) || typeof payload.content === 'string'
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 消息可见性与周期标签
// ═══════════════════════════════════════════════════════════════════════════

/** 面板里最多显示的最近消息条数。 */
export const AI_VISIBLE_MESSAGE_LIMIT = 5

/**
 * 面板里可见的消息（最近 N 条）。
 *
 * ⚠️ **只保留尾部** ⇒ 用户看到的是最新进展。
 */
export function selectVisibleAiMessages<M extends AiConversationMessageLike>(
  messages: M[],
  limit = AI_VISIBLE_MESSAGE_LIMIT,
): M[] {
  return messages.slice(-limit)
}

/** 被折叠掉的消息条数。 */
export function countHiddenAiMessages<M extends AiConversationMessageLike>(
  messages: M[],
  limit = AI_VISIBLE_MESSAGE_LIMIT,
): number {
  return Math.max(0, messages.length - selectVisibleAiMessages(messages, limit).length)
}

/**
 * 周期标签（``2026-11 月计划``）。
 *
 * ⚠️ **未知计划类型 ⇒ 「日计划」**（当前只有月/周/日三种）。
 */
export function buildAiPeriodLabel(planType: string, periodKey: string): string {
  const type = planType === 'MONTH' ? '月计划' : planType === 'WEEK' ? '周计划' : '日计划'
  return `${periodKey} ${type}`
}

/**
 * 会话是否只读（已结束）。
 *
 * ⚠️ **只有 ``viewingConversation``（看历史）且状态为 ``ENDED`` 才只读**
 *    ⇒ 当前活跃会话即使状态是 ENDED 也还能继续发。
 */
export function isAiConversationReadonly(
  viewingConversation: { status?: string } | undefined,
): boolean {
  return Boolean(viewingConversation && viewingConversation.status === 'ENDED')
}
