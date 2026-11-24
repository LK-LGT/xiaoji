/**
 * 🧩 **月度成果归档详情的纯逻辑**（CI-5 阶段 2 第四轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``EmployeeMonthResultArchiveDetail.vue`` 有 **355 行**，其中「任务筛选 /
 * 失败任务收集 / 审批事件流 / 历史版本列表 / 各类数值格式化」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖路由 / 多个 API / Map 型响应式状态，挂载失败难定位。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/** 归档任务的状态。 */
export type ArchiveTaskStatus = 'REJECTED' | 'SUBMITTED' | 'CONFIRMED' | 'NOT_SUBMITTED'

/**
 * ⚠️ **归档任务的最小结构**。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型
 * （例如 ``MonthResultArchiveTask`` 还有 ``taskName`` 等字段）。
 */
export interface ArchiveTaskLike {
  id: string
  status: ArchiveTaskStatus
  latestVersion?: { id: string } | null
  estimatedStartDate?: string
  estimatedEndDate?: string
  deliverable?: string
  quality?: string
}

/** 成果详情（只取归档页用到的字段）。 */
export interface ArchiveResultDetail {
  status?: string
  submittedAt?: string
  suggestedAt?: string
  confirmedAt?: string
  leaderSuggestion?: string
  confirmComment?: string
  personalPerformance?: number
}

/** 详情加载状态。 */
export interface ArchiveTaskDetailState {
  state: 'idle' | 'loading' | 'loaded' | 'failed'
  detail: ArchiveResultDetail | null
  error: string
}

/** 历史版本条目。 */
export interface ArchiveResultVersion {
  id: string
  versionNo: number
  monthPlanId?: string
  monthPlanItemId?: string
  submittedAt?: string
  status?: string
  leaderSuggestion?: string
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 任务筛选与状态
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 归档页只展示「有版本」的任务。
 *
 * ⚠️ **没有 ``latestVersion`` 的任务不展示** ⇒ 用户点了也加载不出内容。
 */
export function selectArchiveResultTasks<T extends ArchiveTaskLike>(tasks: T[]): T[] {
  return tasks.filter((task) => task.latestVersion)
}

/**
 * 选中的任务：优先用户点选的那个，否则回落到第一个。
 *
 * ⚠️ **点选的任务若已被过滤掉** ⇒ 也要回落到第一个（不能出现空详情页）。
 */
export function resolveSelectedArchiveTask<T extends ArchiveTaskLike>(
  tasks: T[],
  selectedTaskId: string | null,
): T | null {
  const visible = selectArchiveResultTasks(tasks)
  return visible.find((task) => task.id === selectedTaskId) ?? visible[0] ?? null
}

/**
 * 加载失败的任务。
 *
 * ⚠️ **只看 ``state === 'failed'``** ⇒ 加载中 / 未加载的不算失败。
 */
export function selectFailedArchiveTasks<T extends ArchiveTaskLike>(
  tasks: T[],
  taskDetails: Map<string, ArchiveTaskDetailState>,
): T[] {
  return tasks.filter((task) => taskDetails.get(task.id)?.state === 'failed')
}

/** 失败任务的 ID 列表（用于批量重试）。 */
export function selectFailedArchiveTaskIds<T extends ArchiveTaskLike>(
  tasks: T[],
  taskDetails: Map<string, ArchiveTaskDetailState>,
): string[] {
  return selectFailedArchiveTasks(tasks, taskDetails).map((task) => task.id)
}

/** 是否还有任务在加载中。 */
export function hasLoadingArchiveTask(
  taskDetails: Map<string, ArchiveTaskDetailState>,
): boolean {
  return [...taskDetails.values()].some((item) => item.state === 'loading')
}

/** 是否有任务被退回。 */
export function hasRejectedArchiveTask<T extends ArchiveTaskLike>(tasks: T[]): boolean {
  return tasks.some((task) => task.status === 'REJECTED')
}

/** 某个任务的详情状态（没加载过时回落到「空闲」）。 */
export function resolveArchiveTaskState<T extends ArchiveTaskDetailState>(
  taskId: string,
  taskDetails: Map<string, T>,
): T {
  return taskDetails.get(taskId) ?? ({ state: 'idle', detail: null, error: '' } as T)
}

/**
 * 任务状态展示信息。
 *
 * ⚠️ **未知状态返回 ``undefined``** ⇒ 组件可不渲染标签（而不是渲染一个空标签）。
 */
export function resolveArchiveTaskStatusMeta(task: { status: ArchiveTaskStatus }) {
  const map = {
    REJECTED: { label: '已退回', type: 'danger' as const },
    SUBMITTED: { label: '审批中', type: 'primary' as const },
    CONFIRMED: { label: '已确认', type: 'success' as const },
    NOT_SUBMITTED: { label: '未提交', type: 'info' as const },
  }
  return map[task.status]
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 绩效合计与打印数据
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 已加载详情里的个人绩效合计。
 *
 * ⚠️ **只算加载成功的** ⇒ 加载失败的任务不会算成 0 分混进合计。
 */
export function sumArchivePersonalPerformance(
  taskDetails: Map<string, ArchiveTaskDetailState>,
): number {
  return [...taskDetails.values()].reduce(
    (sum, item) => sum + Number(item.detail?.personalPerformance || 0),
    0,
  )
}

/**
 * 打印用的详情映射（只含加载成功的）。
 *
 * ⚠️ **加载中的也要带上旧详情**（不能因为刷新就丢内容）⇒ 判据是「有 detail」。
 */
export function buildArchivePrintDetails<D extends ArchiveResultDetail>(
  taskDetails: Map<string, { detail: D | null }>,
): Record<string, D> {
  return Object.fromEntries(
    [...taskDetails.entries()].flatMap(([taskId, value]) => (
      value.detail ? [[taskId, value.detail]] : []
    )),
  ) as Record<string, D>
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 审批事件流
// ═══════════════════════════════════════════════════════════════════════════

export interface ArchiveApprovalEvent {
  label: string
  operatorName: string
  occurredAt?: string
  comment?: string
  state: 'SUBMITTED' | 'REJECTED' | 'APPROVED' | 'CONFIRMED'
  current: boolean
}

/**
 * 构造审批事件流。
 *
 * ⚠️ **只有真的发生过的时间点才产生事件** ⇒ 领导没处理过就不会有「领导处理」行。
 * ⚠️ **状态映射**：
 *   领导处理时若成果被退回 ⇒ ``REJECTED``，否则 ``APPROVED``；
 *   「员工提交」行在成果仍是提交态时标 ``current: true``。
 */
export function buildArchiveApprovalEvents<D extends ArchiveResultDetail>(
  detail: D | null,
): ArchiveApprovalEvent[] {
  if (!detail) return []
  return [
    {
      label: '员工提交',
      operatorName: '本人',
      occurredAt: detail.submittedAt,
      state: 'SUBMITTED',
      current: detail.status === 'submitted',
    },
    ...(detail.suggestedAt ? [{
      label: '领导处理',
      operatorName: '直属领导',
      occurredAt: detail.suggestedAt,
      comment: detail.leaderSuggestion,
      state: detail.status === 'rejected' ? 'REJECTED' as const : 'APPROVED' as const,
      current: false,
    }] : []),
    ...(detail.confirmedAt ? [{
      label: '部门确认',
      operatorName: '部门负责人',
      occurredAt: detail.confirmedAt,
      comment: detail.confirmComment,
      state: 'CONFIRMED' as const,
      current: false,
    }] : []),
  ]
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 历史版本列表
// ═══════════════════════════════════════════════════════════════════════════

export interface ArchiveVersionRow {
  resultId: string
  versionNo: number | string
  submittedAt: string
  changeSummary: string
  rejectionComment: string
  current: boolean
}

/**
 * ⚠️ **组件期望的行类型**（``ResultVersionHistory.vue`` 的 props）。
 *
 * ⚠️ 这些字段都是**可选**的，`versionNo` 声明为 ``string``
 *    （BUG-R6-14：成果 ID / 版本号都是字符串主键）。
 */
export interface ArchiveVersionRowLike {
  resultId: string
  versionNo?: string
  submittedAt?: string
  changeSummary?: string
  rejectionComment?: string
  current?: boolean
}

/**
 * 构造某个任务的历史版本列表。
 *
 * ⚠️ **过滤条件**：
 *   ① 必须属于当前计划 + 当前任务；
 *   ② **排除草稿和未提交**（那些不是「历史版本」）。
 * ⚠️ **排序**：提交时间倒序 ⇒ 同时间再按 ID 倒序（保证稳定）。
 * ⚠️ **当前版本**用 ``latestVersion.id`` 匹配 ⇒ 标 ``current: true``。
 */
export function buildArchiveVersionRows<
  V extends { id: string; versionNo: number | string; monthPlanId?: string; monthPlanItemId?: string; submittedAt?: string; status?: string; leaderSuggestion?: string },
>(
  versions: V[],
  options: {
    planId?: string
    taskId?: string
    latestVersionId?: string | null
  },
): ArchiveVersionRowLike[] {
  const { planId, taskId, latestVersionId } = options
  if (!taskId) return []
  return versions
    .filter((version) => (
      version.monthPlanId === planId
      && version.monthPlanItemId === taskId
      && !['draft', 'not_submitted'].includes(version.status ?? '')
    ))
    .sort((left, right) => (
      String(right.submittedAt).localeCompare(String(left.submittedAt))
      || String(right.id).localeCompare(String(left.id))
    ))
    .map((version) => ({
      resultId: version.id,
      versionNo: String(version.versionNo),
      submittedAt: formatArchiveDateTime(version.submittedAt),
      changeSummary: version.status === 'rejected'
        ? '成果退回'
        : version.status === 'confirmed' ? '成果确认' : '提交成果',
      rejectionComment: version.status === 'rejected' ? (version.leaderSuggestion ?? '') : '',
      current: version.id === latestVersionId,
    }))
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 数值与文件大小格式化
// ═══════════════════════════════════════════════════════════════════════════

/** 时间戳格式化（截到分钟；空 ⇒ ``--``）。 */
export function formatArchiveDateTime(value?: string): string {
  return value ? value.replace('T', ' ').slice(0, 16) : '--'
}

/** 普通数字（最多两位小数）。 */
export function formatArchiveNumber(value?: number): string {
  return Number(value || 0).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

/** 金额（固定两位小数）。 */
export function formatArchiveMoney(value?: number): string {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * 可选金额。
 *
 * ⚠️ **``null`` / ``undefined`` ⇒ 「未填写」**（不是 0.00）
 *    ⇒ 用户能看出「没填」和「填了 0」的区别。
 */
export function formatArchiveOptionalMoney(value?: number): string {
  return value == null ? '未填写' : formatArchiveMoney(value)
}

/**
 * 附件大小。
 *
 * ⚠️ **小于 1MB 用 KB，四舍五入到整数**；否则用 MB，保留两位小数。
 * ⚠️ **0 / 未知 ⇒ 「未知大小」**。
 */
export function formatArchiveFileSize(value?: number): string {
  if (!value) return '未知大小'
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`
  return `${(value / 1024 / 1024).toFixed(2)} MB`
}
