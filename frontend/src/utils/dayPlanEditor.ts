/**
 * 🧩 **日计划编辑器的纯逻辑**（CI-5 阶段 2 第四轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``EmployeeDayPlanEditor.vue`` 有 **815 行**，其中「日期标签 / 排班规则提示 /
 * 脏检查快照 / 提交前检查」全是**纯计算**——不需要挂载组件就能测。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖 auth store / 路由 / 五个 API / 多个子组件，
 *   挂载一旦失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

export type DayPlanStatus =
  | 'draft'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'confirmed'
  | 'paused'
  | 'canceled'
  | 'archived'

export interface DayPlanStatusMeta {
  label: string
  type: 'info' | 'warning' | 'success' | 'danger'
}

/** 日计划校验问题（与 ``PlanValidationAside.vue`` 的类型对齐）。 */
export interface DayPlanValidationIssue {
  key: string
  label: string
  targetId?: string
  blocking?: boolean
}

/**
 * ⚠️ **泛型版本**：让调用方保留自己更精确的类型
 * （例如 ``PlanValidationIssue`` 有 ``blocking: boolean`` 必填）。
 */
export type DayPlanValidationIssueLike<T extends DayPlanValidationIssue = DayPlanValidationIssue> = T

/** 日计划摘要项（与 ``PlanValidationAside.vue`` 的类型对齐）。 */
export interface DayPlanSummaryItem {
  label: string
  value: string
}

/** 排班规则（``EmployeeWorkdayCalendarItem`` 的子集）。 */
export interface DayPlanCalendarRule {
  ruleType: string
  explicit?: boolean
  forceReport?: boolean
  description?: string
}

/** 日计划表单（参与脏检查与快照的字段）。 */
export interface DayPlanFormState {
  planDate: string
  relatedWeekPlanItemIds: string[]
  content: string
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 状态映射
// ═══════════════════════════════════════════════════════════════════════════

/** 日计划状态 ⇒ 展示文案与色板。 */
export const DAY_PLAN_STATUS_MAP: Record<DayPlanStatus, DayPlanStatusMeta> = {
  draft: { label: '草稿', type: 'info' },
  submitted: { label: '已提交', type: 'warning' },
  approved: { label: '已通过', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' },
  confirmed: { label: '已确认', type: 'success' },
  paused: { label: '已暂停', type: 'warning' },
  canceled: { label: '已撤销', type: 'danger' },
  archived: { label: '已归档', type: 'info' },
}

/**
 * 状态展示信息。
 *
 * ⚠️ **未知状态回落到「草稿」** ⇒ 不会因为后端新增状态而白屏。
 */
export function resolveDayPlanStatusMeta(status?: string): DayPlanStatusMeta {
  return DAY_PLAN_STATUS_MAP[status as DayPlanStatus] ?? DAY_PLAN_STATUS_MAP.draft
}

/**
 * 计划是否可编辑。
 *
 * ⚠️ **只有「草稿」和「已驳回」可编辑** —— 其余状态只读。
 */
export function isDayPlanEditable(status?: string): boolean {
  return status === 'draft' || status === 'rejected'
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 日期与时间展示
// ═══════════════════════════════════════════════════════════════════════════

const WEEKDAY_LABELS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六']

/** 短日期（``2026-11-24`` ⇒ ``11/24``；空 ⇒ ``--``）。 */
export function shortDate(value?: string): string {
  return value ? value.slice(5).replace('-', '/') : '--'
}

/**
 * 星期几（``2026-11-24`` ⇒ ``星期二``；空 ⇒ 空串）。
 *
 * ⚠️ 用 ``T00:00:00`` 避免时区把日期挪走。
 */
export function weekdayLabel(value?: string): string {
  if (!value) return ''
  return WEEKDAY_LABELS[new Date(`${value}T00:00:00`).getDay()]
}

/** 页面标题用的日期标签（``11/24 星期二``）。 */
export function buildDayPlanDateLabel(value?: string): string {
  return `${shortDate(value)} ${weekdayLabel(value)}`
}

/** 时间戳格式化（截到分钟；空 ⇒ 空串）。 */
export function formatDayPlanTime(value?: string): string {
  return value ? value.replace('T', ' ').slice(0, 16) : ''
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 排班规则提示
// ═══════════════════════════════════════════════════════════════════════════

/** 排班类型 ⇒ 中文文案。 */
export const DAY_PLAN_RULE_LABELS: Record<string, string> = {
  WORKDAY: '工作日',
  WEEKEND: '周末',
  HOLIDAY: '节假日',
  LEAVE: '请假',
  BUSINESS_TRIP: '出差',
  SPECIAL_SHIFT: '特殊排班',
}

/**
 * 排班规则提示文案。
 *
 * ⚠️ **默认的工作日规则不提示**（``explicit`` 为假且类型是 ``WORKDAY``）
 *    —— 那是常态，不是例外 ⇒ 不该弹提示打扰用户。
 * ⚠️ **强制填报与非强制填报都要提示**（用户需要知道今天要不要填）。
 */
export function buildCalendarRuleNotice(rule?: DayPlanCalendarRule | null): string {
  if (!rule || (!rule.explicit && rule.ruleType === 'WORKDAY')) return ''
  const label = DAY_PLAN_RULE_LABELS[rule.ruleType] || rule.ruleType
  const forceText = rule.forceReport ? '当日需要填报日计划' : '当日不强制填报日计划'
  const description = rule.description ? `；${rule.description}` : ''
  return `${label}：${forceText}${description}`
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 脏检查快照
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 把表单状态序列化成**可比对的快照字符串**。
 *
 * ⚠️ **关联任务 ID 要排序** ⇒ 否则用户只是换了勾选顺序也会被判成「已修改」。
 * ⚠️ **内容要 trim** ⇒ 末尾多敲一个空格不算改动。
 */
export function normalizeDayPlanForm(form: DayPlanFormState): string {
  return JSON.stringify({
    planDate: form.planDate,
    relatedWeekPlanItemIds: [...form.relatedWeekPlanItemIds].sort(),
    content: form.content.trim(),
  })
}

/**
 * 是否有未保存的修改。
 *
 * ⚠️ **保存过之后（快照非空）才可能「有改动」**
 *    ⇒ 刚进页面、还没保存过时不算「脏」。
 */
export function isDayPlanDirty(form: DayPlanFormState, savedSnapshot: string): boolean {
  return Boolean(savedSnapshot) && normalizeDayPlanForm(form) !== savedSnapshot
}

/**
 * 草稿是否有内容（决定「一键清空」按钮是否可用）。
 */
export function hasDayPlanDraftContent(form: {
  content: string
  relatedWeekPlanItemIds: string[]
  remark: string
}): boolean {
  return Boolean(
    form.content.trim()
    || form.relatedWeekPlanItemIds.length
    || form.remark.trim(),
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 提交前检查与准备度
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 收集日计划的校验问题。
 *
 * ⚠️ **目前只有「工作内容」一项**，但**必须标成 ``blocking: true``**
 *    ⇒ 否则提交时不会拦住空计划。
 *
 * ⚠️ **泛型**：调用方可指定更精确的 issue 类型（``PlanValidationIssue`` 等）。
 */
export function collectDayPlanIssues<
  T extends DayPlanValidationIssue = DayPlanValidationIssue,
>(content: string): T[] {
  const result: T[] = []
  if (!content.trim()) {
    result.push({
      key: 'content',
      label: '请填写当天工作内容',
      targetId: 'day-plan-content',
      blocking: true,
    } as T)
  }
  return result
}

/** 只保留阻塞型问题（提交时用）。 */
export function findBlockingDayPlanIssue<
  T extends DayPlanValidationIssue = DayPlanValidationIssue,
>(issues: T[]): T | undefined {
  return issues.find((issue) => issue.blocking)
}

/** 阻塞型问题的数量（进度条状态用）。 */
export function countBlockingDayPlanIssues<
  T extends DayPlanValidationIssue = DayPlanValidationIssue,
>(issues: T[]): number {
  return issues.filter((issue) => issue.blocking).length
}

/**
 * 提交准备度（0–100）。
 *
 * ⚠️ 日计划只有一个字段 ⇒ **填了就是 100，没填就是 0**（没有中间态）。
 */
export function computeDayPlanReadiness(content: string): number {
  return content.trim() ? 100 : 0
}

/**
 * 是否展示「工作内容未填」的错误态。
 *
 * ⚠️ **只在「已经触发过检查」之后才展示** ⇒ 否则用户还没动手就看到红框。
 */
export function shouldShowDayPlanContentError(content: string, feedbackVisible: boolean): boolean {
  return feedbackVisible && !content.trim()
}

/**
 * 提交前检查摘要项（给 ``PlanValidationAside`` 用）。
 */
export function buildDayPlanSummaryItems(content: string, workPointCount: number): DayPlanSummaryItem[] {
  return [
    { label: '工作内容', value: content.trim() ? '已填写' : '未填写' },
    { label: '工作要点', value: `${workPointCount} 项` },
  ]
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 视图模式持久化
// ═══════════════════════════════════════════════════════════════════════════

export type DayPlanTaskViewMode = 'table' | 'cards'

/** 视图模式的存储键。 */
export const DAY_PLAN_VIEW_MODE_KEY = 'day-plan-task-view'

/**
 * 从 localStorage 读视图模式。
 *
 * ⚠️ **只认 ``cards``**，其余一律回落 ``table``
 *    ⇒ 存储里被写脏了也不会进到一个不存在的模式。
 * ⚠️ **存储不可用（隐私模式）时静默回落** ⇒ 不能因此让页面崩掉。
 */
export function readDayPlanViewMode(storage?: Pick<Storage, 'getItem'> | null): DayPlanTaskViewMode {
  try {
    const saved = storage?.getItem(DAY_PLAN_VIEW_MODE_KEY)
    return saved === 'cards' ? 'cards' : 'table'
  } catch {
    return 'table'
  }
}

/**
 * 归一化视图模式的切换值。
 *
 * ⚠️ ``el-radio-group`` 的 ``change`` 事件可能给字符串/数字/布尔 ⇒ 统一收敛。
 */
export function normalizeDayPlanViewMode(value: string | number | boolean | undefined): DayPlanTaskViewMode {
  return value === 'cards' ? 'cards' : 'table'
}

/**
 * 持久化视图模式。
 *
 * ⚠️ **失败静默** —— 存不了偏好不该影响本次编辑。
 */
export function persistDayPlanViewMode(
  mode: DayPlanTaskViewMode,
  storage?: Pick<Storage, 'setItem'> | null,
): boolean {
  try {
    storage?.setItem(DAY_PLAN_VIEW_MODE_KEY, mode)
    return true
  } catch {
    return false
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ 历史版本恢复
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 从历史版本快照里取出可恢复的字段。
 *
 * ⚠️ **关联任务 ID 要转字符串并去掉空串** ⇒ 快照里的数字 ID 也能用。
 */
export function parseDayPlanVersionSnapshot(snapshot: Record<string, unknown>): {
  relatedWeekPlanItemIds: string[]
  content: string
} {
  const relatedWeekPlanItemIds = Array.isArray(snapshot.relatedWeekPlanItemIds)
    ? snapshot.relatedWeekPlanItemIds.map((item) => String(item).trim()).filter(Boolean)
    : []
  return {
    relatedWeekPlanItemIds,
    content: String(snapshot.content ?? ''),
  }
}
