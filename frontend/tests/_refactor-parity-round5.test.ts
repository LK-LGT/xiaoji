import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildAiContextOptionRows,
  isAiContextChecked,
  isAiContextIndeterminate,
  isAllAiContextSelected,
  resolveAiChildToggle,
  resolveAiContextGroupState,
  resolveAiGroupToggle,
  resolveAiToggleAll,
  selectAiContextTypes,
  selectAiKnowledgeReferences,
  updateAiSelection,
} from '../src/utils/aiContextPopover.ts'
import {
  buildDefaultExpandedMonthPlanDiffKeys,
  buildMonthPlanDiffFilterOptions,
  buildMonthPlanDiffTaskDetailEntries,
  resolveMonthPlanDiffChangeSummary,
  selectVisibleMonthPlanDiffChanges,
  toggleAllVisibleMonthPlanDiffChanges,
  toggleMonthPlanDiffChange,
} from '../src/utils/monthPlanVersionDiff.ts'
import {
  buildMonthPlanPrintTitle,
  buildMonthPlanTemplatePrintRows,
  buildMonthPlanTemplateRows,
  canAdjustMonthPlan,
  escapeMonthPlanHtml,
  formatMonthPlanCurrency,
  formatMonthPlanPlainNumber,
  renderMonthPlanEstimatedTotal,
  renderMonthPlanStandard,
  renderMonthPlanTaskContent,
  splitMonthPlanTextLines,
} from '../src/utils/monthPlanSupportActions.ts'
import {
  buildDayPlanAiDraft,
  buildWeekPlanAiDraft,
  normalizeAiStringArray,
  normalizeRelatedWeekPlanItemIds,
  normalizeWeekPlanAiItem,
  resolveAiComposerPlaceholder,
  resolveAiComposerSendLabel,
  resolveWeekEndDate,
} from '../src/utils/planAiAssistant.ts'
import {
  buildPlanDraftPreviewEntries,
  buildPlanDraftVersionGroupOptions,
  findPlanDraftVersionById,
  formatPlanDraftFullTime,
  formatPlanDraftSnapshot,
  isPlanDraftChanged,
  selectVisiblePlanDraftVersions,
} from '../src/utils/planDraftVersionDrawer.ts'
import {
  countPendingResultTasks,
  countResultStatuses,
  filterResultPackages,
  formatCompactResultDate,
  normalizeResultStatus,
  resolveResultPackageActionLabel,
  resolveResultPackageStateText,
  resolveResultPackageTargetTask,
  resolveResultPackageTitle,
  resolveResultWeekTitle,
  resolveResultWorkbenchEmptyHint,
  resolveResultWorkbenchEmptyTitle,
  shiftResultMonth,
  sortResultPackagesByRejection,
  taskMatchesResultFilter,
  type ResultWorkbenchPackageLike,
} from '../src/utils/resultWorkbench.ts'
import {
  buildEditableWeekResultRow,
  isWeekResultRowLocked,
  resolveFocusWeekResultRow,
  resolveMonthEditableWeekResultRow,
  resolveWeekResultCompletion,
  resolveWeekResultDeliverable,
  resolveWeekResultStatusMeta,
  validateWeekResultRow,
} from '../src/utils/weekResultWorkspace.ts'

/**
 * 🔁 **重构等价性自检（CI-5 阶段 2 第五轮）**
 *
 * ⚠️ **这个文件在干什么**
 * ------------------------------------------------------------------
 * 下面的 ``legacy*`` 函数是**重构前组件里的原始实现**（逐字复制）。
 *
 * ⇒ 每一组测试都断言：**新抽出来的纯函数** 和 **重构前的原始实现**
 *    在一批输入上**输出完全一致**（``deepEqual``）。
 *
 * ⚠️ **为什么重要**
 *   抽纯函数时最容易犯的错就是「顺手改了一点逻辑」。
 *   这些等价性断言能**自动发现**那种改动。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ AI 上下文选择器（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacyAvailableHistoryMonths(options: { month: string; available: boolean }[] | undefined) {
  return (options || []).filter((item) => item.available).map((item) => item.month)
}
function legacyAvailableKnowledgeTypes(options: { type: string; available: boolean }[] | undefined) {
  return (options || []).filter((item) => item.available).map((item) => item.type)
}
function legacyIsGrouped(type: string) {
  return type === 'HISTORY_PLANS' || type === 'ENTERPRISE_KNOWLEDGE'
}
function legacyGroupState(
  type: string,
  input: {
    availableHistoryMonths: string[]
    selectedHistoryMonths: string[]
    historyMonthTotal: number
    availableKnowledgeTypes: string[]
    selectedKnowledgeTypes: string[]
    knowledgeTypeTotal: number
  },
) {
  if (type === 'HISTORY_PLANS') {
    return { available: input.availableHistoryMonths, selected: input.selectedHistoryMonths, total: input.historyMonthTotal }
  }
  if (type === 'ENTERPRISE_KNOWLEDGE') {
    return { available: input.availableKnowledgeTypes, selected: input.selectedKnowledgeTypes, total: input.knowledgeTypeTotal }
  }
  return undefined
}
function legacyContextChecked(type: string, group: ReturnType<typeof legacyGroupState>, selected: string[]) {
  return group
    ? group.available.length > 0 && group.available.every((value) => group.selected.includes(value))
    : selected.includes(type)
}
function legacyContextIndeterminate(group: ReturnType<typeof legacyGroupState>) {
  if (!group) return false
  const selected = group.available.filter((value) => group.selected.includes(value)).length
  return selected > 0 && selected < group.available.length
}
function legacySelectableContextTypes(allTypes: string[], historyMonths: string[], knowledgeTypes: string[]) {
  return allTypes.filter((type) => {
    if (type === 'HISTORY_PLANS') return historyMonths.length > 0
    if (type === 'ENTERPRISE_KNOWLEDGE') return knowledgeTypes.length > 0
    return true
  })
}
function legacyAllSelected(selectable: string[], selected: string[]) {
  return selectable.every((type) => selected.includes(type))
}
function legacySelectionIndeterminate(selectable: string[], selected: string[]) {
  const count = selected.length
  const all = legacyAllSelected(selectable, selected)
  return count > 0 && !all
}
function legacyUpdateSelection(values: string[], value: string, selected: boolean) {
  const next = new Set(values)
  if (selected) next.add(value)
  else next.delete(value)
  return [...next]
}
function legacyContextOptions(
  allTypes: string[],
  contextInfo: { contextOptions?: { type: string; label?: string; available?: boolean; summary?: string }[]; availableContext?: string[] } | undefined,
  jobDescription: string,
  localLabels: Record<string, string>,
  input: {
    availableHistoryMonths: string[]
    selectedHistoryMonths: string[]
    historyMonthTotal: number
    availableKnowledgeTypes: string[]
    selectedKnowledgeTypes: string[]
    knowledgeTypeTotal: number
  },
) {
  return allTypes.map((type) => {
    const remote = contextInfo?.contextOptions?.find((item) => item.type === type)
    const jobSummary = jobDescription.trim()
    const group = legacyGroupState(type, input)
    return {
      type,
      label: remote?.label || localLabels[type],
      available: group ? group.available.length > 0 : type === 'JOB_DESCRIPTION'
        ? Boolean(jobSummary)
        : remote?.available ?? Boolean(contextInfo?.availableContext?.includes(localLabels[type])),
      summary: group
        ? `已选 ${group.selected.length}/${group.total} ${type === 'HISTORY_PLANS' ? '个月' : '类'}`
        : type === 'JOB_DESCRIPTION' ? jobDescription.trim() : String(remote?.summary || '').trim(),
    }
  })
}

test('等价：可用月份 / 知识类型筛选', () => {
  const months = [{ month: '2026-01', available: true }, { month: '2026-02', available: false }]
  const types = [{ type: 'NORMATIVE', available: true }, { type: 'EXAMPLE', available: false }]
  assert.deepEqual(selectAvailableHistoryMonthsCompat(months), legacyAvailableHistoryMonths(months))
  assert.deepEqual(selectAvailableHistoryMonthsCompat(undefined), legacyAvailableHistoryMonths(undefined))
  assert.deepEqual(selectAvailableKnowledgeTypesCompat(types), legacyAvailableKnowledgeTypes(types))
  assert.deepEqual(selectAvailableKnowledgeTypesCompat(undefined), legacyAvailableKnowledgeTypes(undefined))
})

test('等价：分组判定与分组状态', () => {
  const input = {
    availableHistoryMonths: ['2026-01'],
    selectedHistoryMonths: ['2026-01'],
    historyMonthTotal: 3,
    availableKnowledgeTypes: ['NORMATIVE'],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 2,
  }
  for (const type of ['HISTORY_PLANS', 'ENTERPRISE_KNOWLEDGE', 'JOB_DESCRIPTION']) {
    assert.equal(isGroupedCompat(type), legacyIsGrouped(type))
    assert.deepEqual(resolveAiContextGroupState(type, input), legacyGroupState(type, input))
  }
})

test('等价：选中 / 半选判定（输入矩阵）', () => {
  const groups = [
    undefined,
    { available: [], selected: [], total: 0 },
    { available: ['a'], selected: [], total: 1 },
    { available: ['a'], selected: ['a'], total: 1 },
    { available: ['a', 'b'], selected: ['a'], total: 2 },
    { available: ['a', 'b'], selected: ['a', 'b'], total: 2 },
  ]
  for (const group of groups) {
    assert.equal(isAiContextChecked('HISTORY_PLANS', group, []), legacyContextChecked('HISTORY_PLANS', group, []))
    assert.equal(isAiContextIndeterminate(group), legacyContextIndeterminate(group))
  }
})

test('等价：可选上下文类型', () => {
  const all = ['HISTORY_PLANS', 'ENTERPRISE_KNOWLEDGE', 'JOB_DESCRIPTION', 'PERFORMANCE_HISTORY']
  for (const months of [[], ['2026-01']]) {
    for (const types of [[], ['NORMATIVE']]) {
      assert.deepEqual(selectAiContextTypes(all, months, types), legacySelectableContextTypes(all, months, types))
    }
  }
})

test('等价：父级全选 / 半选', () => {
  const selectable = ['A', 'B']
  for (const selected of [[], ['A'], ['A', 'B']]) {
    assert.equal(isAllAiContextSelected(selectable, selected), legacyAllSelected(selectable, selected))
    assert.equal(
      isAiContextSelectionIndeterminateCompat(selectable, selected),
      legacySelectionIndeterminate(selectable, selected),
    )
  }
})

test('等价：增删选择', () => {
  for (const values of [[], ['a'], ['a', 'b']]) {
    for (const value of ['a', 'c']) {
      for (const selected of [true, false]) {
        assert.deepEqual(updateAiSelection(values, value, selected), legacyUpdateSelection(values, value, selected))
      }
    }
  }
})

test('等价：切换分组 / 子项的联动', () => {
  const months = ['2026-01', '2026-02']
  const types = ['NORMATIVE']
  for (const selected of [true, false]) {
    assert.deepEqual(
      resolveAiGroupToggle('HISTORY_PLANS', selected, months, types),
      legacyGroupToggle('HISTORY_PLANS', selected, months, types),
    )
    assert.deepEqual(
      resolveAiGroupToggle('ENTERPRISE_KNOWLEDGE', selected, months, types),
      legacyGroupToggle('ENTERPRISE_KNOWLEDGE', selected, months, types),
    )
    assert.deepEqual(
      resolveAiChildToggle('HISTORY_PLANS', ['2026-01'], '2026-01', selected),
      legacyChildToggle(['2026-01'], '2026-01', selected),
    )
  }
})

test('等价：全选 / 全不选', () => {
  const selectable = ['ENTERPRISE_KNOWLEDGE']
  const months = ['2026-01']
  const types = ['NORMATIVE']
  for (const value of [true, false]) {
    assert.deepEqual(
      resolveAiToggleAll(value, selectable, months, types),
      legacyToggleAll(value, selectable, months, types),
    )
  }
})

test('等价：上下文展示行（输入矩阵）', () => {
  const localLabels: Record<string, string> = {
    HISTORY_PLANS: '历史计划',
    ENTERPRISE_KNOWLEDGE: '企业知识',
    JOB_DESCRIPTION: '岗位描述',
    PERFORMANCE_HISTORY: '绩效历史',
  }
  const allTypes = Object.keys(localLabels)
  const jobDescriptions = ['', '  后端开发  ']
  const contextInfos = [
    undefined,
    { availableContext: ['绩效历史'] },
    { contextOptions: [{ type: 'PERFORMANCE_HISTORY', label: '远端', available: false, summary: '  摘要  ' }] },
  ]
  const inputs = [
    {
      availableHistoryMonths: [], selectedHistoryMonths: [], historyMonthTotal: 0,
      availableKnowledgeTypes: [], selectedKnowledgeTypes: [], knowledgeTypeTotal: 0,
    },
    {
      availableHistoryMonths: ['2026-01'], selectedHistoryMonths: ['2026-01'], historyMonthTotal: 2,
      availableKnowledgeTypes: ['NORMATIVE'], selectedKnowledgeTypes: ['NORMATIVE'], knowledgeTypeTotal: 3,
    },
  ]
  for (const jobDescription of jobDescriptions) {
    for (const contextInfo of contextInfos) {
      for (const input of inputs) {
        assert.deepEqual(
          buildAiContextOptionRows(allTypes, contextInfo, jobDescription, localLabels, input),
          legacyContextOptions(allTypes, contextInfo, jobDescription, localLabels, input),
        )
      }
    }
  }
})

test('等价：知识引用过滤', () => {
  const refs = [{ knowledgeType: 'NORMATIVE' }, { knowledgeType: 'REFERENCE' }]
  for (const selected of [[], ['NORMATIVE'], ['NORMATIVE', 'REFERENCE']]) {
    assert.deepEqual(selectAiKnowledgeReferences(refs, selected), legacyKnowledgeReferences(refs, selected))
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 成果工作台（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacyNormalizeResultStatus(status: string) {
  const upper = String(status || '').toUpperCase()
  if (upper === 'DRAFT' || upper === 'TODO' || upper === 'PENDING') return 'TODO'
  if (upper === 'REJECTED' || upper === 'RETURNED') return 'RETURNED'
  if (upper === 'SUBMITTED' || upper === 'IN_REVIEW') return 'IN_REVIEW'
  if (upper === 'APPROVED' || upper === 'CONFIRMED' || upper === 'DONE') return 'DONE'
  return 'TODO'
}
function legacyTaskMatchesFilter(status: string, filter: 'PENDING' | 'IN_REVIEW' | 'DONE') {
  const normalized = legacyNormalizeResultStatus(status)
  if (filter === 'PENDING') return normalized === 'TODO' || normalized === 'RETURNED'
  if (filter === 'IN_REVIEW') return normalized === 'IN_REVIEW'
  return normalized === 'DONE'
}
function legacyCompactDate(value?: string) {
  if (!value) return '--'
  return value.length >= 10 ? value.slice(5, 10).replace('-', '.') : value
}
function legacyWeekTitle(item: { title: string; startDate?: string }) {
  const start = item.startDate ? new Date(`${item.startDate}T00:00:00`) : null
  if (!start || Number.isNaN(start.getTime())) return item.title
  return `${start.getMonth() + 1}月第${Math.ceil(start.getDate() / 7)}周`
}
function legacyPackageTitle(item: ResultWorkbenchPackageLike) {
  return item.type === 'MONTH' ? item.title : `${legacyWeekTitle(item)}执行成果`
}
function legacyPackageStateText(item: ResultWorkbenchPackageLike, filter: 'PENDING' | 'IN_REVIEW' | 'DONE') {
  if (filter === 'IN_REVIEW') return `${item.counts.submitted}项审批中`
  if (filter === 'DONE') {
    const completed = item.tasks.filter((task) => legacyTaskMatchesFilter(task.status, 'DONE')).length
    return `${completed}项已完成`
  }
  const pending = item.counts.pending - item.counts.rejected
  return [
    item.counts.rejected ? `${item.counts.rejected}项被退回` : '',
    pending ? `${pending}项待填写` : '',
  ].filter(Boolean).join('，')
}
function legacyPackageActionLabel(item: ResultWorkbenchPackageLike, filter: 'PENDING' | 'IN_REVIEW' | 'DONE') {
  if (filter === 'IN_REVIEW') return '查看进度'
  if (filter === 'DONE') return '查看成果'
  if (item.counts.rejected) return '处理退回'
  if (item.tasks.some((task) => task.status === 'DRAFT')) return '继续填写'
  return '去填写'
}
function legacyChangeMonth(monthKey: string, offset: number) {
  const [year, month] = monthKey.split('-').map(Number)
  const target = new Date(year, month - 1 + offset, 1)
  return `${target.getFullYear()}-${String(target.getMonth() + 1).padStart(2, '0')}`
}
function legacyEmptyTitle(filter: 'PENDING' | 'IN_REVIEW' | 'DONE') {
  return ({
    PENDING: '当前月份没有待处理成果',
    IN_REVIEW: '当前月份没有审批中的成果',
    DONE: '当前月份没有已完成成果',
  } as const)[filter]
}
function legacyEmptyHint(total: number) {
  if (!total) return '计划审批通过后，将自动生成对应成果任务。'
  return '可切换上方状态查看其他成果。'
}
function legacyOpenPackageTarget(item: ResultWorkbenchPackageLike, filter: 'PENDING' | 'IN_REVIEW' | 'DONE') {
  const preferred = filter === 'PENDING'
    ? item.tasks.find((task) => task.status === 'REJECTED')
      || item.tasks.find((task) => task.status === 'DRAFT')
      || item.tasks.find((task) => legacyTaskMatchesFilter(task.status, filter))
    : item.tasks.find((task) => legacyTaskMatchesFilter(task.status, filter))
  return preferred || item.tasks[0]
}

test('等价：状态归一化（状态矩阵）', () => {
  const statuses = ['DRAFT', 'TODO', 'PENDING', 'REJECTED', 'RETURNED', 'SUBMITTED', 'IN_REVIEW', 'APPROVED', 'CONFIRMED', 'DONE', 'draft', 'approved', 'WEIRD', '']
  for (const status of statuses) {
    assert.equal(normalizeResultStatus(status), legacyNormalizeResultStatus(status))
    for (const filter of ['PENDING', 'IN_REVIEW', 'DONE'] as const) {
      assert.equal(taskMatchesResultFilter(status, filter), legacyTaskMatchesFilter(status, filter))
    }
  }
})

test('等价：状态计数', () => {
  const sets = [[], ['DRAFT'], ['DRAFT', 'REJECTED', 'SUBMITTED', 'APPROVED'], ['WEIRD', 'DRAFT']]
  for (const statuses of sets) {
    assert.deepEqual(countResultStatuses(statuses), legacyCountStatuses(statuses))
  }
})

test('等价：过滤 / 排序成果包', () => {
  const packages: ResultWorkbenchPackageLike[] = [
    { type: 'MONTH', planId: 'a', title: 'a', counts: { pending: 1, rejected: 0, submitted: 0 }, tasks: [{ status: 'DRAFT' }] },
    { type: 'MONTH', planId: 'b', title: 'b', counts: { pending: 1, rejected: 2, submitted: 0 }, tasks: [{ status: 'REJECTED' }] },
    { type: 'WEEK', planId: 'c', title: 'c', counts: { pending: 0, rejected: 0, submitted: 1 }, tasks: [{ status: 'SUBMITTED' }] },
  ]
  for (const filter of ['PENDING', 'IN_REVIEW', 'DONE'] as const) {
    const legacyFiltered = packages.filter((item) => item.tasks.some((task) => legacyTaskMatchesFilter(task.status, filter)))
    assert.deepEqual(
      filterResultPackages(packages, filter).map((p) => p.planId),
      legacyFiltered.map((p) => p.planId),
    )
    // ⚠️ 组件里的顺序是「先过滤、再排序」⇒ 排序函数收到的是**已过滤**的列表
    const legacySorted = filter !== 'PENDING'
      ? legacyFiltered
      : [...legacyFiltered].sort((left, right) => Number(right.counts.rejected > 0) - Number(left.counts.rejected > 0))
    assert.deepEqual(
      sortResultPackagesByRejection(legacyFiltered, filter).map((p) => p.planId),
      legacySorted.map((p) => p.planId),
    )
  }
})

test('等价：展示文案（包矩阵）', () => {
  const packages: ResultWorkbenchPackageLike[] = [
    { type: 'MONTH', planId: 'a', title: 'a', startDate: '2026-11-01', counts: { pending: 3, rejected: 1, submitted: 2 }, tasks: [{ status: 'DRAFT' }, { status: 'APPROVED' }] },
    { type: 'WEEK', planId: 'b', title: 'b', startDate: '2026-11-24', counts: { pending: 0, rejected: 0, submitted: 0 }, tasks: [] },
  ]
  for (const item of packages) {
    for (const filter of ['PENDING', 'IN_REVIEW', 'DONE'] as const) {
      assert.equal(resolveResultPackageTitle(item), legacyPackageTitle(item))
      assert.equal(resolveResultPackageStateText(item, filter), legacyPackageStateText(item, filter))
      assert.equal(resolveResultPackageActionLabel(item, filter), legacyPackageActionLabel(item, filter))
      assert.equal(resolveResultPackageTargetTask(item, filter)?.taskId, legacyOpenPackageTarget(item, filter)?.taskId)
    }
  }
})

test('等价：紧凑日期 / 周标题 / 空态文案', () => {
  for (const value of ['', '2026-11-24', '11-24', undefined]) {
    assert.equal(formatCompactResultDate(value), legacyCompactDate(value))
  }
  for (const item of [{ title: 't', startDate: '2026-11-01' }, { title: 't' }, { title: 't', startDate: 'bad' }]) {
    assert.equal(resolveResultWeekTitle(item), legacyWeekTitle(item))
  }
  for (const filter of ['PENDING', 'IN_REVIEW', 'DONE'] as const) {
    assert.equal(resolveResultWorkbenchEmptyTitle(filter), legacyEmptyTitle(filter))
  }
  for (const total of [0, 5]) {
    assert.equal(resolveResultWorkbenchEmptyHint(total), legacyEmptyHint(total))
  }
})

test('等价：月份偏移', () => {
  for (const monthKey of ['2026-01', '2026-09', '2026-12', '2026-11']) {
    for (const offset of [-1, 0, 1]) {
      assert.equal(shiftResultMonth(monthKey, offset), legacyChangeMonth(monthKey, offset))
    }
  }
})

test('等价：待处理计数', () => {
  for (const counts of [{ todo: 0, returned: 0 }, { todo: 3, returned: 2 }]) {
    assert.equal(countPendingResultTasks(counts), counts.todo + counts.returned)
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 周成果工作区（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacyResultCompletion(result?: { completionStatus?: string; content?: string } | null) {
  return result?.completionStatus === 'COMPLETED' ? result.content || '已完成' : result?.completionStatus || result?.content || ''
}
function legacyResultDeliverable(row: { latestResult?: { deliverable?: string } | null; task: { deliverable?: string } }) {
  return row.latestResult?.deliverable || row.task.deliverable || ''
}
function legacyRowLocked(row: { status: string }) {
  return row.status === 'SUBMITTED' || row.status === 'APPROVED'
}
function legacyStatusMeta(status: string) {
  return ({
    SUBMITTED: { label: '待审批', type: 'warning' as const },
    APPROVED: { label: '已通过', type: 'success' as const },
    REJECTED: { label: '已驳回', type: 'danger' as const },
    NOT_SUBMITTED: { label: '未提交', type: 'info' as const },
  }[status] ?? { label: status || '未提交', type: 'info' as const })
}

test('等价：完成情况回填', () => {
  const results = [
    null,
    undefined,
    {},
    { completionStatus: 'COMPLETED' },
    { completionStatus: 'COMPLETED', content: '写完了' },
    { completionStatus: 'COMPLETED', content: '' },
    { completionStatus: 'IN_PROGRESS', content: '一半' },
    { content: '只有内容' },
  ]
  for (const result of results) {
    assert.equal(resolveWeekResultCompletion(result), legacyResultCompletion(result))
  }
})

test('等价：交付物回填', () => {
  const rows = [
    { latestResult: null, task: { deliverable: '标准' } },
    { latestResult: { deliverable: '用户填的' }, task: { deliverable: '标准' } },
    { latestResult: {}, task: {} },
  ]
  for (const row of rows) {
    assert.equal(resolveWeekResultDeliverable(row), legacyResultDeliverable(row))
  }
})

test('等价：行锁定 / 状态展示', () => {
  for (const status of ['SUBMITTED', 'APPROVED', 'REJECTED', 'NOT_SUBMITTED', 'X', '']) {
    assert.equal(isWeekResultRowLocked({ status }), legacyRowLocked({ status }))
    assert.deepEqual(resolveWeekResultStatusMeta(status), legacyStatusMeta(status))
  }
})

test('等价：构造可编辑行', () => {
  const rows = [
    { weekPlanId: 'w1', weekStart: '2026-11-23', task: { id: 't1', content: 'c', deliverable: 'd' }, latestResult: null, key: 'k1' },
    { weekPlanId: 'w2', weekStart: '2026-11-24', task: { id: 't2' }, latestResult: { status: 'REJECTED', completionStatus: 'COMPLETED', content: 'x', deliverable: 'dd', remark: 'rr', evidences: [{ id: 'e' }] }, key: 'k2' },
  ]
  for (const row of rows) {
    const legacy = {
      ...row,
      status: row.latestResult?.status || 'NOT_SUBMITTED',
      completionStatus: legacyResultCompletion(row.latestResult),
      deliverable: legacyResultDeliverable(row),
      remark: row.latestResult?.remark || '',
      hasEvidence: Boolean(row.latestResult?.evidences?.length),
      file: null,
    }
    assert.deepEqual(buildEditableWeekResultRow(row), legacy)
  }
})

test('等价：焦点行定位', () => {
  const rows = [
    { weekPlanId: 'w1', status: 'SUBMITTED' },
    { weekPlanId: 'w2', status: 'NOT_SUBMITTED' },
    { weekPlanId: 'w3', status: 'APPROVED' },
  ] as never[]
  for (const focusPlanId of ['', 'w1', 'w2', 'w3', 'nope']) {
    const legacy = rows.find((row: { weekPlanId: string }) => row.weekPlanId === focusPlanId)
      || rows.find((row: { status: string }) => !legacyRowLocked(row))
      || rows[0]
    assert.equal(resolveFocusWeekResultRow(rows, focusPlanId)?.weekPlanId, legacy?.weekPlanId)
  }
})

test('等价：月份内定位', () => {
  const rows = [
    { weekPlanId: 'w1', planMonth: '2026-11', status: 'SUBMITTED' },
    { weekPlanId: 'w2', planMonth: '2026-11', status: 'NOT_SUBMITTED' },
    { weekPlanId: 'w3', planMonth: '2026-10', status: 'NOT_SUBMITTED' },
  ] as never[]
  for (const monthKey of ['2026-11', '2026-10', '2026-12']) {
    const legacy = rows.find((row: { planMonth: string; status: string }) => row.planMonth === monthKey && !legacyRowLocked(row))
      || rows.find((row: { planMonth: string }) => row.planMonth === monthKey)
    assert.equal(resolveMonthEditableWeekResultRow(rows, monthKey)?.weekPlanId, legacy?.weekPlanId)
  }
})

test('等价：提交前校验', () => {
  const inputs = [
    { status: 'SUBMITTED', completionStatus: '', deliverable: '' },
    { status: 'NOT_SUBMITTED', completionStatus: '  ', deliverable: 'x' },
    { status: 'NOT_SUBMITTED', completionStatus: 'x', deliverable: '  ' },
    { status: 'NOT_SUBMITTED', completionStatus: 'x', deliverable: 'y' },
    { status: 'APPROVED', completionStatus: 'x', deliverable: 'y' },
  ]
  for (const input of inputs) {
    const legacy = legacyRowLocked(input)
      ? 'LOCKED'
      : !input.completionStatus.trim()
        ? 'MISSING_COMPLETION'
        : !input.deliverable.trim()
          ? 'MISSING_DELIVERABLE'
          : null
    assert.equal(validateWeekResultRow(input), legacy)
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 版本差异（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacyFilterOptions(diff: { items: unknown[]; counts: { modified: number; added: number; removed: number } }) {
  return [
    { label: `全部 ${diff.items.length}`, value: 'ALL' },
    { label: `修改 ${diff.counts.modified}`, value: 'modified' },
    { label: `新增 ${diff.counts.added}`, value: 'added' },
    { label: `删除 ${diff.counts.removed}`, value: 'removed' },
  ]
}
function legacyAllVisibleExpanded(items: { key: string }[], expanded: Set<string>) {
  return items.length > 0 && items.every((change) => expanded.has(change.key))
}
function legacyToggleChange(expanded: Set<string>, key: string) {
  const next = new Set(expanded)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  return next
}
function legacyToggleAllVisible(items: { key: string }[], expanded: Set<string>) {
  const next = new Set(expanded)
  if (legacyAllVisibleExpanded(items, expanded)) {
    items.forEach((change) => next.delete(change.key))
  } else {
    items.forEach((change) => next.add(change.key))
  }
  return next
}
function legacyChangeSummary(change: { type: string; fields?: { label: string }[]; after?: { taskContent?: string; deliverable?: string }; before?: { taskContent?: string; deliverable?: string } }, afterLabel: string, beforeLabel: string) {
  if (change.type === 'modified') {
    return `已修改：${(change.fields ?? []).map((field) => field.label).join('、')}`
  }
  const item = change.after ?? change.before
  return item?.taskContent || item?.deliverable || `${change.type === 'added' ? afterLabel : beforeLabel}任务`
}

test('等价：筛选选项', () => {
  const diff = { items: [{ key: 'k1' }], counts: { modified: 1, added: 0, removed: 2 } }
  assert.deepEqual(buildMonthPlanDiffFilterOptions(diff), legacyFilterOptions(diff))
})

test('等价：按筛选过滤', () => {
  const items = [
    { key: 'k1', type: 'modified' as const, title: 't', fields: [] },
    { key: 'k2', type: 'added' as const, title: 't', fields: [] },
  ]
  for (const filter of ['ALL', 'modified', 'added', 'removed'] as const) {
    const legacy = filter === 'ALL' ? items : items.filter((change) => change.type === filter)
    assert.deepEqual(selectVisibleMonthPlanDiffChanges(items, filter), legacy)
  }
})

test('等价：展开态切换', () => {
  const items = [
    { key: 'k1', type: 'modified' as const, title: 't', fields: [] },
    { key: 'k2', type: 'modified' as const, title: 't', fields: [] },
  ]
  for (const expanded of [[], ['k1'], ['k1', 'k2']]) {
    const set = new Set(expanded)
    assert.deepEqual([...toggleMonthPlanDiffChange(set, 'k1')], [...legacyToggleChange(set, 'k1')])
    assert.deepEqual(
      [...toggleAllVisibleMonthPlanDiffChanges(items, set)],
      [...legacyToggleAllVisible(items, set)],
    )
  }
})

test('等价：默认展开的 key', () => {
  const diff = {
    items: [
      { key: 'k1', type: 'modified' as const, title: 't', fields: [] },
      { key: 'k2', type: 'added' as const, title: 't', fields: [] },
      { key: 'k3', type: 'modified' as const, title: 't', fields: [] },
    ],
    counts: { modified: 2, added: 1, removed: 0 },
  }
  assert.deepEqual(
    [...buildDefaultExpandedMonthPlanDiffKeys(diff)],
    [...legacyDefaultExpanded(diff)],
  )
})

test('等价：变更摘要', () => {
  const changes = [
    { key: 'k', type: 'modified' as const, title: 't', fields: [{ label: 'A' }, { label: 'B' }] },
    { key: 'k', type: 'modified' as const, title: 't', fields: [] },
    { key: 'k', type: 'added' as const, title: 't', fields: [], after: { taskContent: '内容' } },
    { key: 'k', type: 'added' as const, title: 't', fields: [], after: { deliverable: '交付' } },
    { key: 'k', type: 'removed' as const, title: 't', fields: [], before: { taskContent: '被删' } },
    { key: 'k', type: 'removed' as const, title: 't', fields: [] },
  ]
  for (const change of changes) {
    assert.equal(
      resolveMonthPlanDiffChangeSummary(change, { afterLabel: '当前', beforeLabel: '历史' }),
      legacyChangeSummary(change, '当前', '历史'),
    )
  }
})

test('等价：任务详情条目', () => {
  // ⚠️ 真实类型 ``NormalizedMonthPlanItem`` 的字段全是必填 ⇒ 这里用完整对象
  const items = [
    undefined,
    null,
    { workType: 'TASK', taskName: 'n', taskContent: 'c', deliverable: 'd', quality: 'q', estimatedStartDate: 's', estimatedEndDate: 'e', performanceWeight: 10, estimatedValue: 100 },
    { workType: 'UNKNOWN', taskName: 'n', taskContent: '', deliverable: '', quality: '', estimatedStartDate: '', estimatedEndDate: '', performanceWeight: 0, estimatedValue: 0 },
  ]
  for (const item of items) {
    assert.deepEqual(
      buildMonthPlanDiffTaskDetailEntries(item, { formatStandard: legacyStd, formatPeriod: legacyPeriod }),
      legacyTaskDetailEntries(item),
    )
  }
})

function legacyStd(deliverable?: string, quality?: string, fallback = '未填写') {
  return [deliverable, quality].filter((v) => v && v.trim()).join(' / ') || fallback
}
function legacyPeriod(start?: string, end?: string, fallback = '未填写') {
  return start && end ? `${start} ~ ${end}` : fallback
}
function legacyTaskDetailEntries(item?: Record<string, unknown> | null) {
  if (!item) return []
  const workTypeLabels: Record<string, string> = {
    TASK: '任务', METRIC: '指标', DOCUMENT: '文档', PROJECT: '项目',
    COMMUNICATION: '沟通协作', ROUTINE: '日常工作', UNKNOWN: '未分类',
  }
  return [
    { label: '工作类型', value: workTypeLabels[item.workType as string] ?? item.workType ?? '未分类' },
    { label: '任务内容', value: (item.taskContent as string) || '未填写' },
    { label: '考核标准（交付物、质量）', value: legacyStd(item.deliverable as string, item.quality as string, '未填写') },
    { label: '完成时间段', value: legacyPeriod(item.estimatedStartDate as string, item.estimatedEndDate as string, '未填写') },
    { label: '绩效权重', value: `${item.performanceWeight as number}%` },
    { label: '预估价值', value: `¥${(item.estimatedValue as number).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}` },
  ]
}
// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 草稿版本抽屉（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacySavedVersions(versions: { sourceType: string }[]) {
  return versions.filter((item) => item.sourceType === 'MANUAL_SAVE' || item.sourceType === 'RESTORED_SAVE')
}
function legacyAiVersions(versions: { sourceType: string }[]) {
  return versions.filter((item) => item.sourceType === 'AI_GENERATE' || item.sourceType === 'AI_OPTIMIZE')
}
function legacyVersionGroups(versions: { sourceType: string }[]) {
  return [
    { label: `已保存版本 ${legacySavedVersions(versions).length}`, value: 'saved' as const },
    { label: `AI 生成记录 ${legacyAiVersions(versions).length}`, value: 'ai' as const },
  ]
}
function legacyPreviewEntries(planType: string, snapshot: Record<string, unknown>) {
  if (planType === 'WEEK') return [
    { label: '周开始日', value: String(snapshot.weekStart ?? '-') },
    { label: '任务数量', value: `${Array.isArray(snapshot.items) ? snapshot.items.length : 0} 项` },
  ]
  return [
    { label: '计划日期', value: String(snapshot.planDate ?? '-') },
    { label: '关联周任务', value: `${Array.isArray(snapshot.relatedWeekPlanItemIds) ? snapshot.relatedWeekPlanItemIds.length : 0} 项` },
    { label: '工作内容', value: String(snapshot.content ?? '未填写') },
  ]
}
function legacyFormatFullTime(value: string) {
  return value ? value.slice(0, 16).replace('T', ' ') : '-'
}

test('等价：版本分组', () => {
  const versions = [
    { id: 'v1', sourceType: 'MANUAL_SAVE' as const },
    { id: 'v2', sourceType: 'AI_GENERATE' as const },
    { id: 'v3', sourceType: 'RESTORED_SAVE' as const },
    { id: 'v4', sourceType: 'AI_OPTIMIZE' as const },
  ]
  assert.deepEqual(selectVisiblePlanDraftVersions(versions, 'saved'), legacySavedVersions(versions))
  assert.deepEqual(selectVisiblePlanDraftVersions(versions, 'ai'), legacyAiVersions(versions))
  assert.deepEqual(buildPlanDraftVersionGroupOptions(versions), legacyVersionGroups(versions))
})

test('等价：预览条目', () => {
  const snapshots = [
    {},
    { weekStart: '2026-11-23', items: [{ id: 1 }] },
    { planDate: '2026-11-24', relatedWeekPlanItemIds: ['a'], content: 'c' },
  ]
  for (const planType of ['WEEK', 'DAY', 'MONTH']) {
    for (const snapshot of snapshots) {
      assert.deepEqual(
        buildPlanDraftPreviewEntries(planType, snapshot),
        legacyPreviewEntries(planType, snapshot),
      )
    }
  }
})

test('等价：时间格式化 / 快照格式化', () => {
  for (const value of ['', '2026-11-24T10:30:00']) {
    assert.equal(formatPlanDraftFullTime(value), legacyFormatFullTime(value))
  }
  assert.equal(formatPlanDraftSnapshot({ a: 1 }), JSON.stringify({ a: 1 }, null, 2))
  assert.equal(formatPlanDraftSnapshot(undefined), '')
})

test('等价：按 ID 找版本 / 变更判定', () => {
  const versions = [{ id: 'v1', sourceType: 'MANUAL_SAVE' as const }]
  assert.equal(findPlanDraftVersionById(versions, 'v1')?.id, 'v1')
  assert.equal(findPlanDraftVersionById(versions, 'nope'), undefined)
  for (const planType of ['MONTH', 'WEEK', 'DAY'] as const) {
    for (const monthDiffUnchanged of [true, false]) {
      for (const snapshotsEqual of [true, false]) {
        for (const hasSnapshot of [true, false]) {
          const legacy = hasSnapshot
            ? (planType === 'MONTH' ? !monthDiffUnchanged : !snapshotsEqual)
            : false
          assert.equal(
            isPlanDraftChanged({ planType, hasSnapshot, monthDiffUnchanged, snapshotsEqual }),
            legacy,
          )
        }
      }
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 月计划支持动作（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacyEscapeHtml(value: unknown) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}
function legacyLinesOf(value?: string | null) {
  return String(value || '').replace(/\r\n?/g, '\n').split('\n').map((line) => line.trim()).filter(Boolean)
}
function legacyRenderTaskContent(value?: string | null) {
  const lines = legacyLinesOf(value)
  if (!lines.length) return ''
  if (lines.length === 1) return legacyEscapeHtml(lines[0])
  const [title, ...rest] = lines
  return `<span class="task-title">${legacyEscapeHtml(title)}</span>${rest.map((line) => `<span class="line">${legacyEscapeHtml(line)}</span>`).join('')}`
}
function legacyRenderStandard(deliverable?: string | null, quality?: string | null) {
  const deliverableText = String(deliverable || '').trim()
  const qualityText = String(quality || '').trim()
  return [
    deliverableText ? `<span class="standard-label">交付物：</span>${legacyEscapeHtml(deliverableText)}` : '',
    qualityText ? `<span class="standard-label">质量：</span>${legacyEscapeHtml(qualityText)}` : '',
  ].filter(Boolean).map((line) => `<span class="line">${line}</span>`).join('')
}
function legacyPlanMonthTitle(planMonth: string) {
  const [year, month] = String(planMonth || '').split('-')
  const monthNumber = Number(month)
  if (!year || !Number.isFinite(monthNumber)) return '        年      月个人目标制定'
  return `${year} 年 ${String(monthNumber).padStart(2, '0')} 月个人目标制定`
}
function legacyFormatPlainNumber(value?: number | null) {
  const numericValue = Number(value || 0)
  if (!Number.isFinite(numericValue)) return ''
  return numericValue.toLocaleString('zh-CN', { maximumFractionDigits: 2, useGrouping: false })
}
function legacyTemplateRows(template: { items?: unknown[] } | null) {
  if (template?.items.length) return template.items
  return Array.from({ length: 8 }, (_, index) => ({
    rowNo: index + 1,
    taskContent: '',
    performanceWeight: 0,
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    estimatedValue: 0,
  }))
}

test('等价：HTML 转义', () => {
  for (const value of ['<', '&', '"', "'", '&lt;', '<b>x</b>', null, undefined, 123]) {
    assert.equal(escapeMonthPlanHtml(value), legacyEscapeHtml(value))
  }
})

test('等价：拆行', () => {
  for (const value of ['', 'a', 'a\nb', 'a\r\nb', 'a\rb', '  a  \n\n  b  ', null]) {
    assert.deepEqual(splitMonthPlanTextLines(value), legacyLinesOf(value))
  }
})

test('等价：打印渲染', () => {
  for (const value of ['', '单行', '标题\n第二行', 'a\r\nb', '<b>x</b>', null]) {
    assert.equal(renderMonthPlanTaskContent(value), legacyRenderTaskContent(value))
  }
  for (const deliverable of ['', '交付物', ' 交付物 ']) {
    for (const quality of ['', '质量', ' 质量 ']) {
      assert.equal(renderMonthPlanStandard(deliverable, quality), legacyRenderStandard(deliverable, quality))
    }
  }
})

test('等价：标题 / 数字格式化 / 权限', () => {
  for (const planMonth of ['2026-11', '2026-01', 'bad', '', '2026']) {
    assert.equal(buildMonthPlanPrintTitle(planMonth), legacyPlanMonthTitle(planMonth))
  }
  for (const value of [0, 1234567, 12.345, undefined, null, Number.POSITIVE_INFINITY]) {
    assert.equal(formatMonthPlanPlainNumber(value), legacyFormatPlainNumber(value))
  }
  assert.equal(formatMonthPlanCurrency(1234567), '¥1,234,567')
  for (const status of ['submitted', 'approved', 'draft', 'completed', '']) {
    assert.equal(canAdjustMonthPlan(status), status === 'submitted' || status === 'approved')
  }
})

test('等价：模板兜底行', () => {
  for (const template of [null, undefined, { items: [] }, { items: [{ rowNo: 1 }] }]) {
    assert.deepEqual(buildMonthPlanTemplateRows(template), legacyTemplateRows(template))
  }
})

test('等价：打印单元格', () => {
  const rows = [
    { rowNo: 1, taskContent: '任务', performanceWeight: 10, deliverable: 'D', quality: 'Q', estimatedStartDate: 'S', estimatedEndDate: 'E', estimatedValue: 100 },
    { rowNo: 2, taskContent: '', performanceWeight: 0, deliverable: '', quality: '', estimatedStartDate: '', estimatedEndDate: '', estimatedValue: 0 },
  ]
  const legacy = rows.map((row) => `
    <tr class="template-print-item-row">
      <td class="cell-center">${legacyEscapeHtml(row.rowNo)}</td>
      <td>${legacyRenderTaskContent(row.taskContent)}</td>
      <td class="cell-center">${row.performanceWeight || ''}</td>
      <td>${legacyRenderStandard(row.deliverable, row.quality)}</td>
      <td class="cell-center">${legacyEscapeHtml(legacyPeriod(row.estimatedStartDate, row.estimatedEndDate, ''))}</td>
      <td class="cell-center">${row.estimatedValue ? legacyEscapeHtml(legacyFormatPlainNumber(row.estimatedValue)) : ''}</td>
    </tr>
  `).join('')
  assert.equal(
    buildMonthPlanTemplatePrintRows(rows, { formatPeriod: legacyPeriod }),
    legacy,
  )
  assert.equal(renderMonthPlanEstimatedTotal(0), '')
  assert.equal(renderMonthPlanEstimatedTotal(100), '100')
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ AI 助手（legacy 实现）
// ═══════════════════════════════════════════════════════════════════════════

function legacyWeekEnd(value: string) {
  const dateValue = new Date(`${value}T00:00:00`)
  dateValue.setDate(dateValue.getDate() + 6)
  return `${dateValue.getFullYear()}-${String(dateValue.getMonth() + 1).padStart(2, '0')}-${String(dateValue.getDate()).padStart(2, '0')}`
}
function legacyStringArray(value: unknown) {
  return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : []
}
function legacyNormalizeWeekItem(raw: unknown, fallbackFinishDate: string) {
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

test('等价：周结束日', () => {
  for (const value of ['2026-11-23', '2026-11-27', '2026-12-29', '2026-01-01']) {
    assert.equal(resolveWeekEndDate(value), legacyWeekEnd(value))
  }
})

test('等价：字符串数组归一', () => {
  for (const value of [null, undefined, 'x', 123, ['a', '', 'b'], [1, 2]]) {
    assert.deepEqual(normalizeAiStringArray(value), legacyStringArray(value))
  }
})

test('等价：周条目归一', () => {
  const raws = [
    null,
    'str',
    {},
    { id: 'a', monthPlanItemId: 'b', content: 'c', deliverable: 'd', quality: 'q', plannedFinishDate: 'pd' },
    { id: null, monthPlanItemId: null },
    { id: 123, monthPlanItemId: '   ' },
  ]
  for (const raw of raws) {
    assert.deepEqual(normalizeWeekPlanAiItem(raw, '2026-11-29'), legacyNormalizeWeekItem(raw, '2026-11-29'))
  }
})

test('等价：周草稿构造', () => {
  const messages = [
    { payload: { items: [] } },
    { payload: {} },
    { suggestionId: 'sg', payload: { items: [{ id: 'a' }], warnings: ['w'], missingContext: ['m'], notice: 'n', draftVersionId: 1, draftVersionNo: 2 } },
    { payload: { items: [{ id: 'a' }], suggestionId: 'p' } },
  ]
  for (const message of messages) {
    const payload = message.payload || {}
    const items = (Array.isArray(payload.items) ? payload.items : [])
      .map((raw: unknown) => legacyNormalizeWeekItem(raw, '2026-11-29'))
      .filter(Boolean)
    const legacy = items.length
      ? {
        suggestionId: message.suggestionId || String(payload.suggestionId || ''),
        items,
        warnings: legacyStringArray(payload.warnings),
        missingContext: legacyStringArray(payload.missingContext),
        notice: String(payload.notice || ''),
        draftVersionId: Number(payload.draftVersionId || 0),
        draftVersionNo: Number(payload.draftVersionNo || 0),
      }
      : null
    assert.deepEqual(buildWeekPlanAiDraft(message, '2026-11-29'), legacy)
  }
})

test('等价：日草稿构造', () => {
  const messages = [
    { payload: {} },
    { payload: { content: 123 } },
    { suggestionId: 'sg', payload: { content: 'c', remark: 'r', relatedWeekPlanItemIds: ['a', 'b'], warnings: ['w'] } },
    { payload: { content: 'c', remark: 123 } },
  ]
  for (const message of messages) {
    const payload = message.payload || {}
    const legacy = typeof payload.content !== 'string'
      ? null
      : {
        suggestionId: message.suggestionId || String(payload.suggestionId || ''),
        relatedWeekPlanItemIds: Array.isArray(payload.relatedWeekPlanItemIds)
          ? payload.relatedWeekPlanItemIds.map((item: unknown) => String(item).trim()).filter(Boolean)
          : [],
        content: payload.content,
        remark: typeof payload.remark === 'string' ? payload.remark : '',
        warnings: legacyStringArray(payload.warnings),
        missingContext: legacyStringArray(payload.missingContext),
        notice: String(payload.notice || ''),
        draftVersionId: Number(payload.draftVersionId || 0),
        draftVersionNo: Number(payload.draftVersionNo || 0),
      }
    assert.deepEqual(buildDayPlanAiDraft(message), legacy)
  }
})

test('等价：输入框文案', () => {
  for (const mode of ['week', 'day'] as const) {
    for (const hasDraft of [true, false]) {
      const legacyPlaceholder = hasDraft
        ? '继续补充要求或调整当前建议'
        : (mode === 'week' ? '描述本周工作重点和交付要求' : '描述今日工作重点和交付要求')
      const legacySendLabel = hasDraft
        ? '继续调整'
        : (mode === 'week' ? '生成周计划' : '生成日计划')
      assert.equal(resolveAiComposerPlaceholder(mode, hasDraft), legacyPlaceholder)
      assert.equal(resolveAiComposerSendLabel(mode, hasDraft), legacySendLabel)
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 本地 legacy 辅助
// ═══════════════════════════════════════════════════════════════════════════

function selectAvailableHistoryMonthsCompat(options: { month: string; available: boolean }[] | undefined) {
  return (options || []).filter((item) => item.available).map((item) => item.month)
}
function selectAvailableKnowledgeTypesCompat(options: { type: string; available: boolean }[] | undefined) {
  return (options || []).filter((item) => item.available).map((item) => item.type)
}
function isGroupedCompat(type: string) {
  return legacyIsGrouped(type)
}
function isAiContextSelectionIndeterminateCompat(selectable: string[], selected: string[]) {
  const count = selected.length
  const all = isAllAiContextSelected(selectable, selected)
  return count > 0 && !all
}
function legacyGroupToggle(type: string, selected: boolean, historyMonths: string[], knowledgeTypes: string[]) {
  if (type === 'HISTORY_PLANS') {
    const values = selected ? [...historyMonths] : []
    return { values, parentSelected: values.length > 0 }
  }
  const values = selected ? [...knowledgeTypes] : []
  return { values, parentSelected: values.length > 0 }
}
function legacyChildToggle(values: string[], value: string, selected: boolean) {
  const next = legacyUpdateSelection(values, value, selected)
  return { values: next, parentSelected: next.length > 0 }
}
function legacyToggleAll(value: boolean | string | number, selectable: string[], historyMonths: string[], knowledgeTypes: string[]) {
  const selected = Boolean(value)
  return {
    contextTypes: selected ? [...selectable] : [],
    historyMonths: selected ? [...historyMonths] : [],
    knowledgeTypes: selected ? [...knowledgeTypes] : [],
  }
}
function legacyKnowledgeReferences(references: { knowledgeType: string }[], selected: string[]) {
  return references.filter((item) => selected.includes(item.knowledgeType))
}
function legacyCountStatuses(statuses: string[]) {
  const counts = { todo: 0, returned: 0, inReview: 0, done: 0, total: 0 }
  for (const status of statuses) {
    counts.total += 1
    const normalized = legacyNormalizeResultStatus(status)
    if (normalized === 'TODO') counts.todo += 1
    else if (normalized === 'RETURNED') counts.returned += 1
    else if (normalized === 'IN_REVIEW') counts.inReview += 1
    else counts.done += 1
  }
  return counts
}
function legacyDefaultExpanded(diff: { items: { type: string; key: string }[] }) {
  return new Set(diff.items
    .filter((change) => change.type === 'modified')
    .map((change) => change.key))
}
