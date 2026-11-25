/**
 * 🧩 **成果工作台的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``EmployeeResultWorkbench.vue`` 有 **299 行**，其中「筛选匹配 / 包排序 /
 * 空态文案 / 月份切换 / 周次换算」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖路由 / 两个 API / 三个视图组件，挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/** 工作台的筛选档位。 */
export type ResultWorkbenchFilter = 'PENDING' | 'IN_REVIEW' | 'DONE'

/** 归一化后的成果状态。 */
export type NormalizedResultStatus = 'TODO' | 'RETURNED' | 'IN_REVIEW' | 'DONE'

/**
 * 成果任务（工作台用的最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己的精确类型
 * （真实类型的 ``taskId`` 是**必填的字符串** —— BUG-R6-14）。
 */
export interface ResultWorkbenchTaskLike {
  taskId?: string | number
  status: string
}

/**
 * 成果包（工作台用的最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型
 * （真实类型还有 ``key`` / ``endDate`` 等字段）。
 */
export interface ResultWorkbenchPackageLike<T extends ResultWorkbenchTaskLike = ResultWorkbenchTaskLike> {
  type: string
  planId: string
  title: string
  startDate?: string
  counts: {
    pending: number
    rejected: number
    submitted: number
  }
  tasks: T[]
}

/** 各状态计数。 */
export interface ResultStatusCounts {
  todo: number
  returned: number
  inReview: number
  done: number
  total: number
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 状态归一化与筛选
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 后端状态 ⇒ 归一化状态。
 *
 * ⚠️ **未知状态按「待办」处理** ⇒ 新状态不会被静默丢掉。
 */
export function normalizeResultStatus(status: string): NormalizedResultStatus {
  const upper = String(status || '').toUpperCase()
  if (upper === 'DRAFT' || upper === 'TODO' || upper === 'PENDING') return 'TODO'
  if (upper === 'REJECTED' || upper === 'RETURNED') return 'RETURNED'
  if (upper === 'SUBMITTED' || upper === 'IN_REVIEW') return 'IN_REVIEW'
  if (upper === 'APPROVED' || upper === 'CONFIRMED' || upper === 'DONE') return 'DONE'
  return 'TODO'
}

/**
 * 任务是否命中当前筛选。
 *
 * ⚠️ **「待处理」= 待办 + 已退回** ⇒ 用户一处就能看到所有要动手的。
 */
export function taskMatchesResultFilter(
  status: string,
  filter: ResultWorkbenchFilter,
): boolean {
  const normalized = normalizeResultStatus(status)
  if (filter === 'PENDING') return normalized === 'TODO' || normalized === 'RETURNED'
  if (filter === 'IN_REVIEW') return normalized === 'IN_REVIEW'
  return normalized === 'DONE'
}

/**
 * 统计各状态的任务数。
 *
 * ⚠️ **``total`` 是原始条数**（不等于四类之和 —— 未知状态也算一条）。
 */
export function countResultStatuses(statuses: string[]): ResultStatusCounts {
  const counts: ResultStatusCounts = { todo: 0, returned: 0, inReview: 0, done: 0, total: 0 }
  for (const status of statuses) {
    counts.total += 1
    const normalized = normalizeResultStatus(status)
    if (normalized === 'TODO') counts.todo += 1
    else if (normalized === 'RETURNED') counts.returned += 1
    else if (normalized === 'IN_REVIEW') counts.inReview += 1
    else counts.done += 1
  }
  return counts
}

/**
 * 按筛选过滤成果包。
 *
 * ⚠️ **只保留「至少有一个任务命中筛选」的包** ⇒ 空包不占版面。
 */
export function filterResultPackages<T extends ResultWorkbenchPackageLike>(
  packages: T[],
  filter: ResultWorkbenchFilter,
): T[] {
  return packages.filter((item) => item.tasks.some((task) => taskMatchesResultFilter(task.status, filter)))
}

/**
 * 「待处理」档位的包排序：**有退回的排前面**。
 *
 * ⚠️ **只在「待处理」档位排序** ⇒ 其他档位保持后端给的顺序。
 * ⚠️ **排序是稳定的**（``sort`` 在现代引擎里稳定）⇒ 同组内保持原顺序。
 */
export function sortResultPackagesByRejection<T extends ResultWorkbenchPackageLike>(
  packages: T[],
  filter: ResultWorkbenchFilter,
): T[] {
  if (filter !== 'PENDING') return packages
  return [...packages].sort(
    (left, right) => Number(right.counts.rejected > 0) - Number(left.counts.rejected > 0),
  )
}

/**
 * 「待处理」档位的任务数（待办 + 已退回）。
 */
export function countPendingResultTasks(counts: { todo: number; returned: number }): number {
  return counts.todo + counts.returned
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 空态文案
// ═══════════════════════════════════════════════════════════════════════════

/** 各档位的空态标题。 */
export const RESULT_WORKBENCH_EMPTY_TITLES: Record<ResultWorkbenchFilter, string> = {
  PENDING: '当前月份没有待处理成果',
  IN_REVIEW: '当前月份没有审批中的成果',
  DONE: '当前月份没有已完成成果',
}

/**
 * 空态标题。
 */
export function resolveResultWorkbenchEmptyTitle(filter: ResultWorkbenchFilter): string {
  return RESULT_WORKBENCH_EMPTY_TITLES[filter]
}

/**
 * 空态提示。
 *
 * ⚠️ **一个任务都没有 ⇒ 「计划审批通过后会自动生成成果任务」**
 *    ⇒ 告诉用户为什么是空的。
 * ⚠️ **有任务只是当前筛选没命中 ⇒ 「可切换状态查看」**
 *    ⇒ 告诉用户还有别的可看。
 */
export function resolveResultWorkbenchEmptyHint(total: number): string {
  if (!total) return '计划审批通过后，将自动生成对应成果任务。'
  return '可切换上方状态查看其他成果。'
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 展示文案
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 紧凑日期（``11-24`` ⇒ ``11.24``）。
 *
 * ⚠️ **长度不足 10 位原样返回** ⇒ 已经是短格式时不截坏。
 */
export function formatCompactResultDate(value?: string): string {
  if (!value) return '--'
  return value.length >= 10 ? value.slice(5, 10).replace('-', '.') : value
}

/**
 * 周包标题（``11月第4周``）。
 *
 * ⚠️ **日期无效时回落到原标题** ⇒ 不显示「NaN月第NaN周」。
 */
export function resolveResultWeekTitle(item: {
  title: string
  startDate?: string
}): string {
  const start = item.startDate ? new Date(`${item.startDate}T00:00:00`) : null
  if (!start || Number.isNaN(start.getTime())) return item.title
  return `${start.getMonth() + 1}月第${Math.ceil(start.getDate() / 7)}周`
}

/**
 * 包标题。
 *
 * ⚠️ **月包用原标题；周包加「执行成果」后缀**。
 */
export function resolveResultPackageTitle<T extends ResultWorkbenchPackageLike>(item: T): string {
  return item.type === 'MONTH' ? item.title : `${resolveResultWeekTitle(item)}执行成果`
}

/**
 * 包的状态文案。
 *
 * ⚠️ **「待处理」档位下 ``pending`` 含退回项** ⇒ 要减掉退回数，
 *    否则会同时显示「3项被退回」和「3项待填写」（实际只有 0 项待填）。
 */
export function resolveResultPackageStateText<T extends ResultWorkbenchPackageLike>(
  item: T,
  filter: ResultWorkbenchFilter,
): string {
  if (filter === 'IN_REVIEW') return `${item.counts.submitted}项审批中`
  if (filter === 'DONE') {
    const completed = item.tasks.filter((task) => taskMatchesResultFilter(task.status, 'DONE')).length
    return `${completed}项已完成`
  }
  const pending = item.counts.pending - item.counts.rejected
  return [
    item.counts.rejected ? `${item.counts.rejected}项被退回` : '',
    pending ? `${pending}项待填写` : '',
  ].filter(Boolean).join('，')
}

/**
 * 包的操作按钮文案。
 *
 * ⚠️ **优先级**：审批中 ⇒ 查看进度；已完成 ⇒ 查看成果；
 *    有退回 ⇒ 处理退回；有待填写 ⇒ 继续填写；否则 ⇒ 去填写。
 */
export function resolveResultPackageActionLabel<T extends ResultWorkbenchPackageLike>(
  item: T,
  filter: ResultWorkbenchFilter,
): string {
  if (filter === 'IN_REVIEW') return '查看进度'
  if (filter === 'DONE') return '查看成果'
  if (item.counts.rejected) return '处理退回'
  if (item.tasks.some((task) => task.status === 'DRAFT')) return '继续填写'
  return '去填写'
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 月份切换
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 月份偏移（前后翻月）。
 *
 * ⚠️ **用 ``new Date(year, month - 1 + offset, 1)``**
 *    ⇒ 跨年自动进位（12 月 +1 ⇒ 次年 1 月）。
 * ⚠️ **结果补零** ⇒ 始终是 ``YYYY-MM``。
 */
export function shiftResultMonth(monthKey: string, offset: number): string {
  const [year, month] = monthKey.split('-').map(Number)
  const target = new Date(year, month - 1 + offset, 1)
  return `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, '0')}`
}

/**
 * 路由 query 里的月份是否是合法的 ``YYYY-MM``。
 *
 * ⚠️ **必须严格匹配 4 位年 + 2 位月** ⇒ `2026-1` / `202611` 都判为非法。
 */
export function isValidResultMonthKey(value: unknown): boolean {
  return typeof value === 'string' && /^\d{4}-\d{2}$/.test(value)
}

/**
 * 从路由 query 取合法的月份。
 *
 * ⚠️ **query 可能是数组**（重复参数）⇒ 取第一个。
 */
export function resolveResultMonthFromQuery(query: unknown): string {
  const raw = Array.isArray((query as { month?: unknown })?.month)
    ? (query as { month: unknown[] }).month[0]
    : (query as { month?: unknown })?.month
  return isValidResultMonthKey(raw) ? (raw as string) : ''
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 打开包时定位任务
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 打开成果包时应该定位到哪个任务。
 *
 * ⚠️ **「待处理」优先定位退回项** ⇒ 用户一眼看到要返工的东西；
 *    其次是草稿；再次是任意命中项。
 * ⚠️ **其他档位取第一个命中项**。
 * ⚠️ **全都不命中 ⇒ 第一个任务**（不能打开一个空包）。
 */
export function resolveResultPackageTargetTask<T extends ResultWorkbenchPackageLike>(
  item: T,
  filter: ResultWorkbenchFilter,
): T['tasks'][number] | undefined {
  const preferred = filter === 'PENDING'
    ? item.tasks.find((task) => task.status === 'REJECTED')
      || item.tasks.find((task) => task.status === 'DRAFT')
      || item.tasks.find((task) => taskMatchesResultFilter(task.status, filter))
    : item.tasks.find((task) => taskMatchesResultFilter(task.status, filter))
  return preferred || item.tasks[0]
}
