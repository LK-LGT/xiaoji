/**
 * 🧩 **月计划编辑器的纯逻辑**（CI-5 阶段 2 第三轮 —— 从重页面抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``EmployeeMonthPlanEdit.vue`` 有 **1512 行**，其中大量是**纯计算**：
 * 校验规则、准备度打分、AI 优化字段对比……这些逻辑**不需要挂载组件**就能测。
 *
 * ⚠️ **为什么不整页挂载测**
 *   1512 行的组件挂载成本极高（十几个 API / 路由 / store 依赖），
 *   而且一旦挂载失败，**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *   ① 测试可以直接对纯函数断言（快、稳、定位准）；
 *   ② 组件变薄 ⇒ 将来若要整页挂载测，成本也降下来了。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 *   需要 ``reactive``、``ref``、``watch`` 的留在组件里。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/** 月计划条目（与组件内的 ``EditableMonthPlanItem`` 对齐，但去掉 UI 字段）。 */
export interface MonthPlanEditableItem {
  id?: string
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  estimatedStartDate: string
  estimatedEndDate: string
  performanceWeight: number | null
  estimatedValue: number | null
}

export interface MonthPlanValidationIssue {
  id: string
  itemIndex?: number
  field?: string
  message: string
}

/** 可被 AI 优化的字段。 */
export type OptimizableField =
  | 'taskName'
  | 'taskContent'
  | 'deliverable'
  | 'quality'
  | 'estimatedStartDate'
  | 'estimatedEndDate'
  | 'performanceWeight'
  | 'estimatedValue'

export interface OptimizePreviewField {
  key: OptimizableField
  label: string
  originalValue: unknown
  suggestedValue: unknown
  changed: boolean
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 校验规则
// ═══════════════════════════════════════════════════════════════════════════

/** 常规任务的权重合计必须是 100%。 */
export const REQUIRED_WEIGHT_TOTAL = 100

/** 预估价值的上限（与后端一致）。 */
export const MAX_ESTIMATED_VALUE = 9999999999.99

/** 必填的文本字段（5 个）。 */
const REQUIRED_TEXT_FIELDS: Array<[keyof MonthPlanEditableItem, string]> = [
  ['taskContent', '任务内容'],
  ['deliverable', '交付物'],
  ['quality', '质量'],
  ['estimatedStartDate', '预估开始日期'],
  ['estimatedEndDate', '预估结束日期'],
]

/**
 * 权重合计（保留两位小数，避免浮点误差）。
 *
 * ⚠️ 用 ``toFixed(2)`` 而不是直接求和 ⇒
 *   ``0.1 + 0.2 = 0.30000000000000004`` 会让 ``=== 100`` 判定失败。
 */
export function sumRegularWeights(items: Array<Pick<MonthPlanEditableItem, 'performanceWeight'>>): number {
  return Number(
    items
      .reduce((sum, item) => sum + (Number(item.performanceWeight) || 0), 0)
      .toFixed(2),
  )
}

/**
 * 收集月计划的全部校验问题。
 *
 * ⚠️ **顺序有意义** —— 组件用 ``validationIssues[0]`` 决定「第一处问题」提示用户。
 *   ⇒ 顺序固定为：计划月份 → 空任务 → 逐项字段 → 权重合计。
 */
export function collectMonthPlanIssues(
  planMonth: string,
  items: MonthPlanEditableItem[],
): MonthPlanValidationIssue[] {
  const issues: MonthPlanValidationIssue[] = []

  if (!planMonth) {
    issues.push({ id: 'plan-month', field: 'planMonth', message: '请选择计划月份' })
  }
  if (!items.length) {
    issues.push({ id: 'items-empty', message: '请至少新增一项计划任务' })
  }

  items.forEach((item, itemIndex) => {
    REQUIRED_TEXT_FIELDS.forEach(([field, label]) => {
      if (!String(item[field] || '').trim()) {
        issues.push({
          id: `${itemIndex}-${String(field)}`,
          itemIndex,
          field: String(field),
          message: `任务 ${itemIndex + 1}：请填写${label}`,
        })
      }
    })

    const weight = Number(item.performanceWeight)
    if (!weight || weight <= 0 || weight > 100) {
      issues.push({
        id: `${itemIndex}-performanceWeight`,
        itemIndex,
        field: 'performanceWeight',
        message: `任务 ${itemIndex + 1}：请填写有效绩效权重`,
      })
    }

    const value = Number(item.estimatedValue)
    if (value < 0 || value > MAX_ESTIMATED_VALUE) {
      issues.push({
        id: `${itemIndex}-estimatedValue`,
        itemIndex,
        field: 'estimatedValue',
        message: `任务 ${itemIndex + 1}：预估价值需在 0 至 ${MAX_ESTIMATED_VALUE} 元之间`,
      })
    }

    if (
      item.estimatedStartDate
      && item.estimatedEndDate
      && item.estimatedStartDate > item.estimatedEndDate
    ) {
      issues.push({
        id: `${itemIndex}-estimatedEndDate-order`,
        itemIndex,
        field: 'estimatedEndDate',
        message: `任务 ${itemIndex + 1}：预估结束日期不能早于开始日期`,
      })
    }
  })

  if (items.length && sumRegularWeights(items) !== REQUIRED_WEIGHT_TOTAL) {
    issues.push({
      id: 'weight-total',
      field: 'performanceWeight',
      message: `常规任务权重合计需为 ${REQUIRED_WEIGHT_TOTAL}%，当前为 ${sumRegularWeights(items)}%`,
    })
  }

  return issues
}

/** 把校验问题按「计划信息 / 任务内容」分组（只保留非空组）。 */
export function groupMonthPlanIssues(issues: MonthPlanValidationIssue[]) {
  return [
    { key: 'plan', label: '计划信息', issues: issues.filter((issue) => issue.itemIndex === undefined) },
    { key: 'tasks', label: '任务内容', issues: issues.filter((issue) => issue.itemIndex !== undefined) },
  ].filter((group) => group.issues.length)
}

/** 每组最多显示几条（超出部分折叠）。 */
export const ISSUE_GROUP_PREVIEW_LIMITS: Record<string, number> = { plan: 3, tasks: 4 }

/** 按分组裁剪，并带上每组总数与被折叠掉的数量。 */
export function previewMonthPlanIssueGroups(
  issues: MonthPlanValidationIssue[],
  expanded = false,
) {
  return groupMonthPlanIssues(issues).map((group) => {
    const limit = ISSUE_GROUP_PREVIEW_LIMITS[group.key] ?? 4
    return {
      ...group,
      total: group.issues.length,
      issues: expanded ? group.issues : group.issues.slice(0, limit),
    }
  })
}

/** 折叠状态下总共藏了多少条问题。 */
export function countHiddenMonthPlanIssues(
  issues: MonthPlanValidationIssue[],
): number {
  return groupMonthPlanIssues(issues).reduce(
    (count, group) => count + Math.max(0, group.issues.length - (ISSUE_GROUP_PREVIEW_LIMITS[group.key] ?? 4)),
    0,
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 准备度打分
// ═══════════════════════════════════════════════════════════════════════════

/** 每个任务计入准备度的字段数（与「完整度」的 7 项一致）。 */
export const READINESS_FIELDS_PER_ITEM = 7

/** 计划月份对得上当前月（或更晚）时的得分。 */
const PLAN_MONTH_SCORE = 10

/** 任务字段填写的满分。 */
const ITEM_FIELD_SCORE = 65

/** 权重合计的满分。 */
const WEIGHT_SCORE = 25

/** 单个任务已填写的字段数。 */
export function countCompletedReadinessFields(item: MonthPlanEditableItem): number {
  return (
    (String(item.taskContent || '').trim() ? 1 : 0)
    + (String(item.deliverable || '').trim() ? 1 : 0)
    + (String(item.quality || '').trim() ? 1 : 0)
    + (String(item.estimatedStartDate || '').trim() ? 1 : 0)
    + (String(item.estimatedEndDate || '').trim() ? 1 : 0)
    + (Number(item.performanceWeight) > 0 ? 1 : 0)
    + (Number(item.estimatedValue) >= 0 ? 1 : 0)
  )
}

/**
 * 提交准备度（0–100）。
 *
 * ⚠️ **三段权重**：
 *   ① 计划月份是否合理 —— 10 分；
 *   ② 任务字段填写完整度 —— 65 分；
 *   ③ 权重合计是否接近 100 —— 25 分。
 */
export function computeMonthPlanReadiness(
  planMonth: string,
  items: MonthPlanEditableItem[],
  currentMonth: string,
): number {
  if (!items.length) return 0

  const planMonthReady = Boolean(planMonth && planMonth >= currentMonth)
  const planScore = planMonthReady ? PLAN_MONTH_SCORE : 0

  const itemFieldCount = items.length * READINESS_FIELDS_PER_ITEM
  const completedItemFields = items.reduce(
    (count, item) => count + countCompletedReadinessFields(item),
    0,
  )
  const itemScore = itemFieldCount ? (completedItemFields / itemFieldCount) * ITEM_FIELD_SCORE : 0

  const weightTotal = sumRegularWeights(items)
  const weightDistance = Math.min(Math.abs(weightTotal - REQUIRED_WEIGHT_TOTAL), 100)
  const weightScore = weightTotal > 0 ? (1 - weightDistance / 100) * WEIGHT_SCORE : 0

  return Math.max(0, Math.min(100, Math.round(planScore + itemScore + weightScore)))
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ AI 优化字段对比
// ═══════════════════════════════════════════════════════════════════════════

/** 可优化字段的展示定义。 */
export const OPTIMIZABLE_FIELDS: Array<{ key: OptimizableField; label: string }> = [
  { key: 'taskContent', label: '任务内容' },
  { key: 'deliverable', label: '交付物' },
  { key: 'quality', label: '质量' },
  { key: 'estimatedStartDate', label: '预估开始日期' },
  { key: 'estimatedEndDate', label: '预估结束日期' },
  { key: 'performanceWeight', label: '绩效权重' },
  { key: 'estimatedValue', label: '预估价值（元）' },
]

/**
 * 归一化字段值（用于「有没有变」的比较）。
 *
 * ⚠️ 数值字段用 ``Number``；非数值用 ``trim`` 后的字符串。
 * ⇒ ``'40'`` 与 ``40`` 视为**相同**（不误报「有变化」）。
 */
export function normalizeOptimizeValue(value: unknown, field: OptimizableField): number | string {
  if (field === 'performanceWeight' || field === 'estimatedValue') {
    const number = Number(value)
    return Number.isFinite(number) ? number : 0
  }
  return String(value ?? '').trim()
}

/** 从任意对象上取字段值（避开 ``noImplicitAny`` 的索引签名限制）。 */
function readField(source: object, key: OptimizableField): unknown {
  return (source as Record<string, unknown>)[key]
}

/** 两个条目的可优化字段是否完全一致。 */
export function sameOptimizableValues(left: object, right: object): boolean {
  return OPTIMIZABLE_FIELDS.every(({ key }) => (
    normalizeOptimizeValue(readField(left, key), key) === normalizeOptimizeValue(readField(right, key), key)
  ))
}

/** 构建「原值 vs AI 建议值」的对比列表。 */
export function buildOptimizePreview(
  source: object,
  suggestion: object,
): OptimizePreviewField[] {
  return OPTIMIZABLE_FIELDS.map(({ key, label }) => ({
    key,
    label,
    originalValue: readField(source, key),
    suggestedValue: readField(suggestion, key),
    changed: normalizeOptimizeValue(readField(source, key), key) !== normalizeOptimizeValue(readField(suggestion, key), key),
  }))
}

/** 默认勾选「有变化」的字段。 */
export function defaultSelectedOptimizeFields(fields: OptimizePreviewField[]): OptimizableField[] {
  return fields.filter((field) => field.changed).map((field) => field.key)
}

/** 展示用的字段值（权重 / 价值有专门格式）。 */
export function displayOptimizeValue(value: unknown, field: OptimizableField): string {
  if (field === 'performanceWeight') return value == null ? '未填写' : `${value}%`
  if (field === 'estimatedValue') {
    return value == null
      ? '未填写'
      : `¥${Number(value ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  }
  return String(value ?? '暂无')
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 保存前的字段规整
// ═══════════════════════════════════════════════════════════════════════════

/** 编辑态的数值：``0`` / 非法值 ⇒ ``null``（表示「未填」）。 */
export function editableNumber(value: unknown): number | null {
  const number = Number(value ?? 0)
  return Number.isFinite(number) && number !== 0 ? number : null
}

/** 保存态的数值：非法值 ⇒ ``0``。 */
export function numberForSave(value: unknown): number {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

/**
 * 任务名：没填就用任务内容的前 30 字兜底。
 *
 * ⚠️ 空白折叠 + 截断 ⇒ 保证列表里不会出现一长串空白。
 */
export function taskNameForSave(
  item: Pick<MonthPlanEditableItem, 'taskName' | 'taskContent'>,
): string {
  const existing = String(item.taskName || '').trim()
  if (existing) return existing
  return String(item.taskContent || '').replace(/\s+/g, ' ').trim().slice(0, 30)
}

/** 条目是否填了任务内容。 */
export function hasTaskContent(item: Pick<MonthPlanEditableItem, 'taskContent'>): boolean {
  return Boolean(String(item.taskContent || '').trim())
}

/**
 * 条目是否「完全空」（用于清空加载后的自动排期）。
 *
 * ⚠️ **8 个字段全空**才算空行 ⇒ 只填了权重的不算空行。
 */
export function isEmptyMonthPlanItem(item: MonthPlanEditableItem): boolean {
  return (
    !String(item.taskName || '').trim()
    && !String(item.taskContent || '').trim()
    && !String(item.deliverable || '').trim()
    && !String(item.quality || '').trim()
    && item.performanceWeight == null
    && item.estimatedValue == null
    && !String(item.estimatedStartDate || '').trim()
    && !String(item.estimatedEndDate || '').trim()
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 自动保存状态
// ═══════════════════════════════════════════════════════════════════════════

export type AutoSaveStatus = 'idle' | 'saving' | 'saved' | 'readonly' | 'error'

export interface AutoSaveState {
  text: string
  type: 'info' | 'warning' | 'success' | 'danger'
}

/**
 * 自动保存状态的文案与色板。
 *
 * ⚠️ **优先级**：只读 > 保存中 > 已保存 > 失败 > 空闲。
 */
export function resolveAutoSaveState(
  status: AutoSaveStatus,
  editable: boolean,
  dirty: boolean,
): AutoSaveState {
  if (!editable || status === 'readonly') return { text: '只读', type: 'info' }
  if (status === 'saving') return { text: '自动保存中', type: 'warning' }
  if (status === 'saved') return { text: '已自动保存', type: 'success' }
  if (status === 'error') return { text: '自动保存失败', type: 'danger' }
  return { text: dirty ? '有未保存修改' : '尚未修改', type: 'info' }
}
