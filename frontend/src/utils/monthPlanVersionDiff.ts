/**
 * 🧩 **月计划版本差异的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``MonthPlanVersionDiff.vue`` 有 **301 行**，其中「筛选 / 展开态 / 变更摘要 /
 * 任务详情条目」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖 el-collapse / 复杂的 props 契约，挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/** 差异筛选档位。 */
export type MonthPlanDiffFilter = 'ALL' | 'modified' | 'added' | 'removed'

/** 变更类型。 */
export type MonthPlanDiffChangeType = 'modified' | 'added' | 'removed'

/**
 * ⚠️ **变更字段的最小结构**。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型
 * （真实类型 ``FieldChange`` 还有 ``key`` / ``before`` / ``after``）。
 */
export interface MonthPlanDiffFieldLike {
  label: string
}

/**
 * ⚠️ **计划事项的最小结构**。
 *
 * ⚠️ **字段全是可选** ⇒ 调用方可以传更精确的
 * ``NormalizedMonthPlanItem``（字段必填）—— 结构化子类型兼容。
 */
export interface MonthPlanDiffItemLike {
  workType?: string
  taskContent?: string
  deliverable?: string
  quality?: string
  estimatedStartDate?: string
  estimatedEndDate?: string
  performanceWeight?: number
  estimatedValue?: number
}

/**
 * ⚠️ **单条变更的最小结构**。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型（真实类型还有 ``title``）。
 */
export interface MonthPlanDiffChangeLike<
  TItem extends MonthPlanDiffItemLike = MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike = MonthPlanDiffFieldLike,
> {
  key: string
  type: MonthPlanDiffChangeType
  title: string
  fields: TField[]
  before?: TItem
  after?: TItem
}

/**
 * ⚠️ **差异结果的最小结构**。
 */
export interface MonthPlanDiffLike<
  TItem extends MonthPlanDiffItemLike = MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike = MonthPlanDiffFieldLike,
> {
  items: MonthPlanDiffChangeLike<TItem, TField>[]
  counts: {
    modified: number
    added: number
    removed: number
  }
}

/** 筛选选项行。 */
export interface MonthPlanDiffFilterOption {
  label: string
  value: MonthPlanDiffFilter
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 筛选
// ═══════════════════════════════════════════════════════════════════════════

/** 变更类型 ⇒ 中文文案。 */
export const MONTH_PLAN_DIFF_CHANGE_LABELS: Record<MonthPlanDiffChangeType, string> = {
  added: '新增',
  modified: '修改',
  removed: '删除',
}

/**
 * 变更类型 ⇒ 中文文案。
 */
export function resolveMonthPlanDiffChangeLabel(type: MonthPlanDiffChangeType): string {
  return MONTH_PLAN_DIFF_CHANGE_LABELS[type] ?? type
}

/**
 * 构造筛选选项（带各类型计数）。
 */
export function buildMonthPlanDiffFilterOptions<
  TItem extends MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike,
>(diff: MonthPlanDiffLike<TItem, TField>): MonthPlanDiffFilterOption[] {
  return [
    { label: `全部 ${diff.items.length}`, value: 'ALL' },
    { label: `修改 ${diff.counts.modified}`, value: 'modified' },
    { label: `新增 ${diff.counts.added}`, value: 'added' },
    { label: `删除 ${diff.counts.removed}`, value: 'removed' },
  ]
}

/**
 * 按筛选过滤变更列表。
 *
 * ⚠️ **「全部」直接返回原数组**（不复制）⇒ 保持引用稳定。
 */
export function selectVisibleMonthPlanDiffChanges<
  TItem extends MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike,
>(
  items: MonthPlanDiffChangeLike<TItem, TField>[],
  filter: MonthPlanDiffFilter,
): MonthPlanDiffChangeLike<TItem, TField>[] {
  return filter === 'ALL' ? items : items.filter((change) => change.type === filter)
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 展开态
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 某条变更是否已展开。
 */
export function isMonthPlanDiffExpanded(expandedKeys: Set<string>, key: string): boolean {
  return expandedKeys.has(key)
}

/**
 * 是否「全部可见变更都已展开」。
 *
 * ⚠️ **空列表 ⇒ false**（不是 true）
 *    ⇒ 否则「全展开」按钮在没内容时会被点亮。
 */
export function areAllVisibleMonthPlanDiffExpanded<
  TItem extends MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike,
>(
  visibleChanges: MonthPlanDiffChangeLike<TItem, TField>[],
  expandedKeys: Set<string>,
): boolean {
  return visibleChanges.length > 0
    && visibleChanges.every((change) => expandedKeys.has(change.key))
}

/**
 * 切换单条变更的展开态。
 *
 * ⚠️ **返回新 Set**（不改原 Set）⇒ Vue 的响应式才能感知到。
 */
export function toggleMonthPlanDiffChange(
  expandedKeys: Set<string>,
  key: string,
): Set<string> {
  const next = new Set(expandedKeys)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}

/**
 * 切换所有可见变更的展开态。
 *
 * ⚠️ **已全展开 ⇒ 全部收起；否则全部展开**。
 */
export function toggleAllVisibleMonthPlanDiffChanges<
  TItem extends MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike,
>(
  visibleChanges: MonthPlanDiffChangeLike<TItem, TField>[],
  expandedKeys: Set<string>,
): Set<string> {
  const next = new Set(expandedKeys)
  if (areAllVisibleMonthPlanDiffExpanded(visibleChanges, expandedKeys)) {
    visibleChanges.forEach((change) => next.delete(change.key))
  } else {
    visibleChanges.forEach((change) => next.add(change.key))
  }
  return next
}

/**
 * 差异数据变化时，默认展开「修改」类的变更。
 *
 * ⚠️ **只有「修改」默认展开** ⇒ 新增 / 删除默认收起（用户先看改动最大的）。
 */
export function buildDefaultExpandedMonthPlanDiffKeys<
  TItem extends MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike,
>(diff: MonthPlanDiffLike<TItem, TField>): Set<string> {
  return new Set(diff.items
    .filter((change) => change.type === 'modified')
    .map((change) => change.key))
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 变更摘要
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 变更摘要文案。
 *
 * ⚠️ **「修改」列出改动的字段名**（`已修改：任务内容、考核标准`）。
 * ⚠️ **「新增 / 删除」优先用事项内容**，其次交付物，
 *    最后回落「{标签}任务」（如「当前版本任务」）。
 */
export function resolveMonthPlanDiffChangeSummary<
  TItem extends MonthPlanDiffItemLike,
  TField extends MonthPlanDiffFieldLike,
>(
  change: MonthPlanDiffChangeLike<TItem, TField>,
  labels: { afterLabel: string; beforeLabel: string },
): string {  if (change.type === 'modified') {
    return `已修改：${change.fields.map((field) => field.label).join('、')}`
  }
  const item = change.after ?? change.before
  return item?.taskContent
    || item?.deliverable
    || `${change.type === 'added' ? labels.afterLabel : labels.beforeLabel}任务`
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 任务详情条目
// ═══════════════════════════════════════════════════════════════════════════

/** 工作类型 ⇒ 中文文案。 */
export const MONTH_PLAN_WORK_TYPE_LABELS: Record<string, string> = {
  TASK: '任务',
  METRIC: '指标',
  DOCUMENT: '文档',
  PROJECT: '项目',
  COMMUNICATION: '沟通协作',
  ROUTINE: '日常工作',
  UNKNOWN: '未分类',
}

/**
 * 构造任务详情条目（用于「展开看明细」）。
 *
 * ⚠️ **每项都要有兜底文案** ⇒ 详情区不会出现空白行。
 * ⚠️ **预估价值用 ``toLocaleString``** ⇒ 大数字有千分位。
 */
export function buildMonthPlanDiffTaskDetailEntries<TItem extends MonthPlanDiffItemLike>(
  item: TItem | undefined | null,
  formatters: {
    formatStandard: (deliverable: string | undefined, quality: string | undefined, fallback: string) => string
    formatPeriod: (start: string | undefined, end: string | undefined, fallback: string) => string
  },
): { label: string; value: string }[] {
  if (!item) return []
  return [
    {
      label: '工作类型',
      value: MONTH_PLAN_WORK_TYPE_LABELS[item.workType ?? ''] ?? item.workType ?? '未分类',
    },
    { label: '任务内容', value: item.taskContent || '未填写' },
    {
      label: '考核标准（交付物、质量）',
      value: formatters.formatStandard(item.deliverable, item.quality, '未填写'),
    },
    {
      label: '完成时间段',
      value: formatters.formatPeriod(item.estimatedStartDate, item.estimatedEndDate, '未填写'),
    },
    { label: '绩效权重', value: `${item.performanceWeight}%` },
    {
      label: '预估价值',
      value: `¥${Number(item.estimatedValue ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`,
    },
  ]
}
