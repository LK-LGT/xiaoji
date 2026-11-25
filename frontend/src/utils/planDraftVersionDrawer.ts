/**
 * 🧩 **草稿版本抽屉的纯逻辑**（CI-5 阶段 2 第五轮 —— 从重组件抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``PlanDraftVersionDrawer.vue`` 有 **327 行**，其中「版本分组 / 可见版本 /
 * 预览条目 / 时间格式化 / 变更判定」全是**纯计算**。
 *
 * ⚠️ **为什么不整页挂载测**
 *   组件依赖 el-drawer / 三个 API / 路由，挂载失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════════════════════

/** 版本来源类型。 */
export type PlanDraftVersionSource = 'AI_GENERATE' | 'AI_OPTIMIZE' | 'MANUAL_SAVE' | 'RESTORED_SAVE'

/** 版本分组。 */
export type PlanDraftVersionGroup = 'saved' | 'ai'

/** 计划类型。 */
export type PlanDraftPlanType = 'MONTH' | 'WEEK' | 'DAY'

/**
 * 版本摘要（最小结构）。
 *
 * ⚠️ **泛型参数**让调用方保留自己更精确的类型
 * （真实类型 ``PlanDraftVersionSummary`` 的 ``id`` 是**数字**）。
 */
export interface PlanDraftVersionSummaryLike<TId = string | number> {
  id: TId
  versionNo?: number
  sourceType: PlanDraftVersionSource
}

/** 版本详情（最小结构）。 */
export interface PlanDraftVersionDetailLike {
  id: string | number
  snapshot: Record<string, unknown>
}

/** 预览条目。 */
export interface PlanDraftPreviewEntry {
  label: string
  value: string
}

/** 分组选项。 */
export interface PlanDraftVersionGroupOption {
  label: string
  value: PlanDraftVersionGroup
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 版本分组
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 手动保存 / 恢复后保存 ⇒ 「已保存版本」。
 *
 * ⚠️ **这两类都是用户自己存的** ⇒ 归到同一组。
 */
export function isSavedPlanDraftVersion(sourceType: PlanDraftVersionSource): boolean {
  return sourceType === 'MANUAL_SAVE' || sourceType === 'RESTORED_SAVE'
}

/**
 * AI 生成 / AI 优化 ⇒ 「AI 生成记录」。
 */
export function isAiPlanDraftVersion(sourceType: PlanDraftVersionSource): boolean {
  return sourceType === 'AI_GENERATE' || sourceType === 'AI_OPTIMIZE'
}

/**
 * 按来源分出「已保存版本」和「AI 生成记录」。
 *
 * ⚠️ **保持原顺序**（不排序）⇒ 后端返回的顺序就是用户看到的顺序。
 */
export function groupPlanDraftVersions<T extends PlanDraftVersionSummaryLike>(
  versions: T[],
): { saved: T[]; ai: T[] } {
  return {
    saved: versions.filter((item) => isSavedPlanDraftVersion(item.sourceType)),
    ai: versions.filter((item) => isAiPlanDraftVersion(item.sourceType)),
  }
}

/**
 * 当前分组下可见的版本列表。
 */
export function selectVisiblePlanDraftVersions<T extends PlanDraftVersionSummaryLike>(
  versions: T[],
  group: PlanDraftVersionGroup,
): T[] {
  const grouped = groupPlanDraftVersions(versions)
  return group === 'saved' ? grouped.saved : grouped.ai
}
/**
 * 构造分组选项（带各组计数）。
 */
export function buildPlanDraftVersionGroupOptions<T extends PlanDraftVersionSummaryLike>(
  versions: T[],
): PlanDraftVersionGroupOption[] {
  const grouped = groupPlanDraftVersions(versions)
  return [
    { label: `已保存版本 ${grouped.saved.length}`, value: 'saved' },
    { label: `AI 生成记录 ${grouped.ai.length}`, value: 'ai' },
  ]
}

/**
 * 在可见版本里找指定 ID 的版本。
 */
export function findPlanDraftVersionById<
  T extends PlanDraftVersionSummaryLike,
  TId = T['id'],
>(
  versions: T[],
  id: TId | undefined,
): T | undefined {
  if (id === undefined || id === null) return undefined
  return versions.find((version) => version.id === id)
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 预览条目
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 构造快照预览条目。
 *
 * ⚠️ **周计划**展示开始日 + 任务数；
 *    **日计划**展示日期 + 关联周任务数 + 工作内容。
 * ⚠️ **缺失值统一显示「-」或「未填写」** ⇒ 预览区不留空白。
 */
export function buildPlanDraftPreviewEntries(
  planType: PlanDraftPlanType,
  snapshot: Record<string, unknown> | undefined,
): PlanDraftPreviewEntry[] {
  const data = snapshot ?? {}
  if (planType === 'WEEK') {
    return [
      { label: '周开始日', value: String(data.weekStart ?? '-') },
      { label: '任务数量', value: `${Array.isArray(data.items) ? data.items.length : 0} 项` },
    ]
  }
  return [
    { label: '计划日期', value: String(data.planDate ?? '-') },
    {
      label: '关联周任务',
      value: `${Array.isArray(data.relatedWeekPlanItemIds) ? data.relatedWeekPlanItemIds.length : 0} 项`,
    },
    { label: '工作内容', value: String(data.content ?? '未填写') },
  ]
}

/**
 * 格式化快照 JSON。
 *
 * ⚠️ **没有选中版本 ⇒ 空串**（不显示 ``undefined``）。
 */
export function formatPlanDraftSnapshot(
  snapshot: Record<string, unknown> | undefined,
): string {
  return snapshot ? JSON.stringify(snapshot, null, 2) : ''
}
// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 时间格式化
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 格式化完整时间（``2026-11-24T10:30:00`` ⇒ ``2026-11-24 10:30``）。
 *
 * ⚠️ **空值 ⇒ 「-」**。
 */
export function formatPlanDraftFullTime(value: string): string {
  return value ? value.slice(0, 16).replace('T', ' ') : '-'
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 变更判定
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 选中版本与当前草稿是否有差异。
 *
 * ⚠️ **月计划用 diff 结果的 ``unchanged``**；
 *    **周 / 日计划用快照全等比较**（不同类型不能共用 diff）。
 * ⚠️ **没有选中版本 ⇒ 没变**。
 */
export function isPlanDraftChanged(input: {
  planType: PlanDraftPlanType
  hasSnapshot: boolean
  monthDiffUnchanged: boolean
  snapshotsEqual: boolean
}): boolean {
  if (!input.hasSnapshot) return false
  return input.planType === 'MONTH'
    ? !input.monthDiffUnchanged
    : !input.snapshotsEqual
}
