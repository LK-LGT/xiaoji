/**
 * 🧩 **周成果工作区的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``EmployeeWeekResultWorkspace.vue`` 有 **315 行**，其中「完成情况回填 /
 * 交付物回填 / 行锁定判定 / 状态文案 / 焦点行定位」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖路由 / 文件上传 / 多个子组件，挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 周成果记录（最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型。
 */
export interface WeekResultRecordLike {
  status?: string
  completionStatus?: string
  content?: string
  deliverable?: string
  remark?: string
  evidences?: unknown[]
}

/**
 * 周计划任务（最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型。
 */
export interface WeekResultTaskLike {
  id: string
  content?: string
  deliverable?: string
}

/**
 * 周成果行（最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型。
 */
export interface WeekResultRowLike<
  TTask extends WeekResultTaskLike = WeekResultTaskLike,
  TResult extends WeekResultRecordLike = WeekResultRecordLike,
> {
  weekPlanId: string
  weekStart: string
  task: TTask
  latestResult?: TResult | null
}

/**
 * 可编辑的周成果行。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型。
 */
export interface EditableWeekResultRowLike<
  TTask extends WeekResultTaskLike = WeekResultTaskLike,
  TResult extends WeekResultRecordLike = WeekResultRecordLike,
  TExtra extends Record<string, unknown> = Record<string, unknown>,
> extends WeekResultRowLike<TTask, TResult> {
  status: string
  completionStatus: string
  deliverable: string
  remark: string
  hasEvidence: boolean
  file: File | null
}

/**
 * 可编辑的周成果行（带 ``key`` / ``planMonth``）。
 */
export interface EditableWeekResultGroupRowLike<
  TTask extends WeekResultTaskLike = WeekResultTaskLike,
  TResult extends WeekResultRecordLike = WeekResultRecordLike,
  TExtra extends Record<string, unknown> = Record<string, unknown>,
> extends EditableWeekResultRowLike<TTask, TResult> {
  key: string
  planMonth: string
}

/** 状态展示信息。 */
export interface WeekResultStatusMeta {
  label: string
  type: 'warning' | 'success' | 'danger' | 'info'
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 完成情况 / 交付物回填
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 回填「完成情况」。
 *
 * ⚠️ **已完成 ⇒ 优先用成果内容**，没内容则显示「已完成」；
 *    **未完成 ⇒ 用原始状态码**，再没有就用内容。
 */
export function resolveWeekResultCompletion<TResult extends WeekResultRecordLike>(
  result?: TResult | null,
): string {
  return result?.completionStatus === 'COMPLETED'
    ? result.content || '已完成'
    : result?.completionStatus || result?.content || ''
}

/**
 * 回填「交付物」。
 *
 * ⚠️ **优先用成果里的交付物** ⇒ 用户之前填过的不该被任务的标准覆盖。
 */
export function resolveWeekResultDeliverable<
  TTask extends WeekResultTaskLike,
  TResult extends WeekResultRecordLike,
>(row: WeekResultRowLike<TTask, TResult>): string {
  return row.latestResult?.deliverable || row.task.deliverable || ''
}

/**
 * 行是否被锁定（不可再编辑）。
 *
 * ⚠️ **已提交 / 已通过都锁定** ⇒ 不能改已经流转过的成果。
 */
export function isWeekResultRowLocked(row: { status: string }): boolean {
  return row.status === 'SUBMITTED' || row.status === 'APPROVED'
}

/**
 * 构造可编辑行（从只读行 + 最新成果派生）。
 *
 * ⚠️ **证据存在性用 ``Boolean(evidences.length)``**
 *    ⇒ 数组为空时不算有证据。
 */
export function buildEditableWeekResultRow<
  TTask extends WeekResultTaskLike,
  TResult extends WeekResultRecordLike,
  TRow extends WeekResultRowLike<TTask, TResult>,
>(
  row: TRow,
): TRow & EditableWeekResultRowLike<TTask, TResult> {
  return {
    ...row,
    status: row.latestResult?.status || 'NOT_SUBMITTED',
    completionStatus: resolveWeekResultCompletion(row.latestResult),
    deliverable: resolveWeekResultDeliverable(row),
    remark: row.latestResult?.remark || '',
    hasEvidence: Boolean(row.latestResult?.evidences?.length),
    file: null,
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 状态展示
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 成果状态展示信息。
 *
 * ⚠️ **未知状态回落到「原样显示状态码」**
 *    ⇒ 后端加状态时前端不会显示成空白。
 */
export function resolveWeekResultStatusMeta(status: string): WeekResultStatusMeta {
  const map: Record<string, WeekResultStatusMeta> = {
    SUBMITTED: { label: '待审批', type: 'warning' },
    APPROVED: { label: '已通过', type: 'success' },
    REJECTED: { label: '已驳回', type: 'danger' },
    NOT_SUBMITTED: { label: '未提交', type: 'info' },
  }
  return map[status] ?? { label: status || '未提交', type: 'info' }
}

/**
 * 周期文案（``2026-11-24 至 2026-11-30``）。
 */
export function formatWeekResultPeriod(start: string, end: string): string {
  return `${start} 至 ${end}`
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 焦点行定位
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 定位焦点行。
 *
 * ⚠️ **优先级**：
 *   ① 路由指定的计划 ID；
 *   ② **第一个未锁定的行**（用户能动手的）；
 *   ③ 第一行。
 * ⚠️ **计划 ID 必须转字符串**（BUG-R6-14：ID 是字符串主键）
 *    ⇒ 数字强转会塌成 0，永远匹配不到。
 */
export function resolveFocusWeekResultRow<
  TTask extends WeekResultTaskLike,
  TResult extends WeekResultRecordLike,
  TRow extends EditableWeekResultRowLike<TTask, TResult>,
>(
  rows: TRow[],
  focusPlanId: string,
): TRow | undefined {
  return rows.find((row) => row.weekPlanId === focusPlanId)
    || rows.find((row) => !isWeekResultRowLocked(row))
    || rows[0]
}

/**
 * 展开月份时定位到该月第一个可编辑的行。
 *
 * ⚠️ **优先未锁定的行**；该月全锁 ⇒ 退到该月第一行。
 */
export function resolveMonthEditableWeekResultRow<
  TTask extends WeekResultTaskLike,
  TResult extends WeekResultRecordLike,
  TRow extends EditableWeekResultRowLike<TTask, TResult> & { planMonth?: string },
>(
  rows: TRow[],
  monthKey: string,
): TRow | undefined {
  return rows.find((row) => row.planMonth === monthKey && !isWeekResultRowLocked(row))
    || rows.find((row) => row.planMonth === monthKey)
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 路由参数
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 从路由 query 取合法的 ``YYYY-MM`` 月份。
 *
 * ⚠️ **query 可能是数组**（重复参数）⇒ 取第一个。
 * ⚠️ **格式不合法 ⇒ 空串**（不猜）。
 */
export function resolveWeekMonthFromQuery(query: unknown): string {
  const raw = Array.isArray((query as { weekMonth?: unknown })?.weekMonth)
    ? (query as { weekMonth: unknown[] }).weekMonth[0]
    : (query as { weekMonth?: unknown })?.weekMonth
  return typeof raw === 'string' && /^\d{4}-\d{2}$/.test(raw) ? raw : ''
}

/**
 * 从路由 query 取计划 ID。
 *
 * ⚠️ **必须转字符串**（BUG-R6-14）⇒ 数字 ID 也能匹配。
 */
export function resolveWeekPlanIdFromQuery(query: unknown): string {
  const raw = Array.isArray((query as { weekPlanId?: unknown })?.weekPlanId)
    ? (query as { weekPlanId: unknown[] }).weekPlanId[0]
    : (query as { weekPlanId?: unknown })?.weekPlanId
  return String(raw ?? '')
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 提交前校验
// ═══════════════════════════════════════════════════════════════════════════

/** 校验失败的原因。 */
export type WeekResultValidationError = 'LOCKED' | 'MISSING_COMPLETION' | 'MISSING_DELIVERABLE'

/**
 * 校验一行能否提交。
 *
 * ⚠️ **锁定行最优先** ⇒ 提示「已提交，不能重复提交」。
 * ⚠️ **完成情况和交付物都必须非空**（去空白后）。
 */
export function validateWeekResultRow(
  row: { status: string; completionStatus: string; deliverable: string },
): WeekResultValidationError | null {
  if (isWeekResultRowLocked(row)) return 'LOCKED'
  if (!row.completionStatus.trim()) return 'MISSING_COMPLETION'
  if (!row.deliverable.trim()) return 'MISSING_DELIVERABLE'
  return null
}

/**
 * 校验失败 ⇒ 用户看的提示文案。
 */
export function resolveWeekResultValidationMessage(error: WeekResultValidationError): string {
  if (error === 'LOCKED') return '该周成果已提交，不能重复提交'
  if (error === 'MISSING_COMPLETION') return '请填写完成情况'
  return '请填写交付物'
}
