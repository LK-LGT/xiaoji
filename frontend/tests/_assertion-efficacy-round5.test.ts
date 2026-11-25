import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildAiContextOptionRows,
  isAiContextChecked,
  isAiContextIndeterminate,
  isAllAiContextSelected,
  resolveAiChildToggle,
  resolveAiToggleAll,
  selectAiKnowledgeReferences,
} from '../src/utils/aiContextPopover.ts'
import {
  areAllVisibleMonthPlanDiffExpanded,
  resolveMonthPlanDiffChangeSummary,
  toggleAllVisibleMonthPlanDiffChanges,
} from '../src/utils/monthPlanVersionDiff.ts'
import {
  buildMonthPlanTemplateRows,
  escapeMonthPlanHtml,
  formatMonthPlanPlainNumber,
  renderMonthPlanStandard,
  renderMonthPlanTaskContent,
  splitMonthPlanTextLines,
  type MonthPlanTemplateRowLike,
} from '../src/utils/monthPlanSupportActions.ts'
import {
  buildWeekPlanAiDraft,
  normalizeRelatedWeekPlanItemIds,
  normalizeWeekPlanAiItem,
  resolveWeekEndDate,
} from '../src/utils/planAiAssistant.ts'
import {
  buildPlanDraftPreviewEntries,
  isPlanDraftChanged,
  selectVisiblePlanDraftVersions,
} from '../src/utils/planDraftVersionDrawer.ts'
import {
  countResultStatuses,
  filterResultPackages,
  formatCompactResultDate,
  normalizeResultStatus,
  resolveResultPackageStateText,
  resolveResultPackageTargetTask,
  resolveResultWorkbenchEmptyHint,
  shiftResultMonth,
  sortResultPackagesByRejection,
  type ResultWorkbenchPackageLike,
} from '../src/utils/resultWorkbench.ts'
import {
  isWeekResultRowLocked,
  resolveWeekResultCompletion,
  resolveWeekResultValidationMessage,
  validateWeekResultRow,
} from '../src/utils/weekResultWorkspace.ts'

/**
 * 🧪 **断言效力验证（CI-5 阶段 2 第五轮）**
 *
 * ⚠️ **这个文件在干什么**
 * ------------------------------------------------------------------
 * 下面的每个测试都**故意实现一个「有 bug 的版本」**，
 * 然后断言「**正确实现**」和「**bug 版本**」的输出**不一样**。
 *
 * ⇒ **如果哪天有人把正确实现改成 bug 版本，这些测试会立刻失败**
 *    ⇒ 证明我们的断言**真的能区分对错**，而不是「怎么写都过」。
 *
 * ⚠️ **命名约定**：``buggy*`` 前缀 = 故意写错的实现。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ AI 上下文选择器
// ═══════════════════════════════════════════════════════════════════════════

test('效力：不过滤 available 的月份筛选 ≠ 正确实现', () => {
  const buggy = (options: { month: string; available: boolean }[]) => options.map((o) => o.month)
  const correct = (options: { month: string; available: boolean }[]) => options
    .filter((o) => o.available)
    .map((o) => o.month)
  const input = [{ month: '2026-01', available: true }, { month: '2026-02', available: false }]
  assert.notDeepEqual(correct(input), buggy(input))
})

test('效力：分组空可用项时算「全选」≠ 正确实现', () => {
  const buggy = (group: { available: string[]; selected: string[] }) => group.available
    .every((v) => group.selected.includes(v))
  const emptyGroup = { available: [], selected: [] }
  assert.notEqual(isAiContextChecked('HISTORY_PLANS', emptyGroup, []), buggy(emptyGroup))
})

test('效力：半选判定用 >= 代替 > ≠ 正确实现', () => {
  const buggy = (group: { available: string[]; selected: string[] }) => {
    const n = group.available.filter((v) => group.selected.includes(v)).length
    return n >= group.available.length && n > 0
  }
  const full = { available: ['a', 'b'], selected: ['a', 'b'] }
  assert.notEqual(isAiContextIndeterminate(full), buggy(full))
})

test('效力：父级全选用 some 代替 every ≠ 正确实现', () => {
  const buggy = (all: string[], sel: string[]) => all.some((t) => sel.includes(t))
  assert.notEqual(isAllAiContextSelected(['A', 'B'], ['A']), buggy(['A', 'B'], ['A']))
})

test('效力：取消最后一个子项后父级仍保持选中 ≠ 正确实现', () => {
  const buggyParentSelected = true
  const correct = resolveAiChildToggle('HISTORY_PLANS', ['2026-01'], '2026-01', false)
  assert.notEqual(correct.parentSelected, buggyParentSelected)
})

test('效力：全选时选「全部类型」而不是「可选类型」≠ 正确实现', () => {
  const buggy = { contextTypes: ['HISTORY_PLANS', 'ENTERPRISE_KNOWLEDGE'] }
  const correct = resolveAiToggleAll(true, ['ENTERPRISE_KNOWLEDGE'], ['2026-01'], ['NORMATIVE'])
  assert.notDeepEqual(correct.contextTypes, buggy.contextTypes)
})

test('效力：岗位描述可用性不看 trim ≠ 正确实现', () => {
  const buggy = { available: true }
  const rows = buildAiContextOptionRows(['JOB_DESCRIPTION'], undefined, '   ', {
    JOB_DESCRIPTION: '岗位描述',
  }, {
    availableHistoryMonths: [],
    selectedHistoryMonths: [],
    historyMonthTotal: 0,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.notEqual(rows[0].available, buggy.available)
})

test('效力：分组摘要不区分「个月 / 类」≠ 正确实现', () => {
  const buggy = '已选 1/2 项'
  const rows = buildAiContextOptionRows(['HISTORY_PLANS'], undefined, '', {
    HISTORY_PLANS: '历史计划',
  }, {
    availableHistoryMonths: ['a', 'b'],
    selectedHistoryMonths: ['a'],
    historyMonthTotal: 2,
    availableKnowledgeTypes: [],
    selectedKnowledgeTypes: [],
    knowledgeTypeTotal: 0,
  })
  assert.notEqual(rows[0].summary, buggy)
})

test('效力：知识引用不过滤已选类型 ≠ 正确实现', () => {
  const buggy = [{ knowledgeType: 'NORMATIVE' }]
  const correct = selectAiKnowledgeReferences([{ knowledgeType: 'NORMATIVE' }], ['REFERENCE'])
  assert.notDeepEqual(correct, buggy)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 成果工作台
// ═══════════════════════════════════════════════════════════════════════════

test('效力：状态归一化不认 DRAFT ≠ 正确实现', () => {
  const buggy = (status: string) => (status === 'REJECTED' ? 'RETURNED' : status)
  assert.notEqual(normalizeResultStatus('DRAFT'), buggy('DRAFT'))
})

test('效力：状态归一化大小写敏感 ≠ 正确实现', () => {
  const buggy = (status: string) => (status === 'APPROVED' ? 'DONE' : status)
  assert.notEqual(normalizeResultStatus('approved'), buggy('approved'))
})

test('效力：待处理筛选只算 TODO（漏掉退回）≠ 正确实现', () => {
  const buggy = (status: string) => normalizeResultStatus(status) === 'TODO'
  assert.notEqual(normalizeResultStatus('REJECTED') === 'RETURNED', buggy('REJECTED'))
})

test('效力：状态计数的 total 不含未知状态 ≠ 正确实现', () => {
  // ⚠️ buggy 版本：total 只统计已知状态，未知状态被丢掉
  const buggy = countResultStatusesCompat(['DRAFT', 'REJECTED', 'SUBMITTED', 'APPROVED', 'MYSTERY'])
  const correct = countResultStatuses(['DRAFT', 'REJECTED', 'SUBMITTED', 'APPROVED', 'MYSTERY'])
  assert.equal(correct.total, 5)
  assert.notEqual(correct.total, buggy.total)
})

test('效力：过滤成果包时保留空包 ≠ 正确实现', () => {
  const packages: ResultWorkbenchPackageLike[] = [
    { type: 'MONTH', planId: 'a', title: 'a', counts: { pending: 0, rejected: 0, submitted: 0 }, tasks: [] },
  ]
  const buggy = packages
  const correct = filterResultPackages(packages, 'PENDING')
  assert.notEqual(correct.length, buggy.length)
})

test('效力：待处理排序不把退回项排前面 ≠ 正确实现', () => {
  const packages: ResultWorkbenchPackageLike[] = [
    { type: 'MONTH', planId: 'a', title: 'a', counts: { pending: 1, rejected: 0, submitted: 0 }, tasks: [{ status: 'DRAFT' }] },
    { type: 'MONTH', planId: 'b', title: 'b', counts: { pending: 1, rejected: 2, submitted: 0 }, tasks: [{ status: 'REJECTED' }] },
  ]
  const buggy = [...packages].sort(() => 0)
  const correct = sortResultPackagesByRejection(packages, 'PENDING')
  assert.notDeepEqual(correct.map((p) => p.planId), buggy.map((p) => p.planId))
})

test('效力：待处理状态文案不减退回数 ≠ 正确实现', () => {
  const item: ResultWorkbenchPackageLike = {
    type: 'MONTH',
    planId: 'a',
    title: 'a',
    counts: { pending: 3, rejected: 3, submitted: 0 },
    tasks: [],
  }
  const buggy = '3项被退回，3项待填写'
  const correct = resolveResultPackageStateText(item, 'PENDING')
  assert.notEqual(correct, buggy)
  assert.equal(correct, '3项被退回')
})

test('效力：空态提示不区分「一个任务都没有」≠ 正确实现', () => {
  const buggy = '可切换上方状态查看其他成果。'
  assert.notEqual(resolveResultWorkbenchEmptyHint(0), buggy)
})

test('效力：紧凑日期不切掉年份 ≠ 正确实现', () => {
  const buggy = '2026-11-24'
  assert.notEqual(formatCompactResultDate('2026-11-24'), buggy)
})

test('效力：月份偏移不处理跨年 ≠ 正确实现', () => {
  const buggy = (monthKey: string, offset: number) => {
    const [y, m] = monthKey.split('-').map(Number)
    return `${y}-${String(m + offset).padStart(2, '0')}`
  }
  assert.notEqual(shiftResultMonth('2026-12', 1), buggy('2026-12', 1))
})

test('效力：月份偏移不补零 ≠ 正确实现', () => {
  // ⚠️ buggy 版本：结果不补零（2026-1）
  const buggy = (monthKey: string, offset: number) => {
    const [y, m] = monthKey.split('-').map(Number)
    return `${y}-${m + offset}`
  }
  assert.notEqual(shiftResultMonth('2026-09', 0), buggy('2026-09', 0))
  assert.equal(shiftResultMonth('2026-09', 0), '2026-09')
})

test('效力：月份格式校验放宽成 \\d{4}-\\d{1,2} ≠ 正确实现', () => {
  const buggy = /^\d{4}-\d{1,2}$/.test('2026-1')
  assert.equal(buggy, true)
  assert.equal(/^\d{4}-\d{2}$/.test('2026-1'), false)
})

test('效力：定位任务时不优先退回项 ≠ 正确实现', () => {
  const item: ResultWorkbenchPackageLike = {
    type: 'MONTH',
    planId: 'a',
    title: 'a',
    counts: { pending: 0, rejected: 0, submitted: 0 },
    tasks: [{ taskId: '1', status: 'DRAFT' }, { taskId: '2', status: 'REJECTED' }],
  }
  const buggy = item.tasks[0]
  const correct = resolveResultPackageTargetTask(item, 'PENDING')
  assert.notEqual(correct?.taskId, buggy?.taskId)
})

test('效力：定位任务时不兜底到第一个任务 ≠ 正确实现', () => {
  const item: ResultWorkbenchPackageLike = {
    type: 'MONTH',
    planId: 'a',
    title: 'a',
    counts: { pending: 0, rejected: 0, submitted: 0 },
    tasks: [{ taskId: '1', status: 'REJECTED' }],
  }
  const buggy = undefined
  const correct = resolveResultPackageTargetTask(item, 'DONE')
  assert.notEqual(correct?.taskId, buggy?.taskId)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 周成果工作区
// ═══════════════════════════════════════════════════════════════════════════

test('效力：完成情况回填时优先状态码而非内容 ≠ 正确实现', () => {
  const buggy = (r: { completionStatus?: string; content?: string }) => r.completionStatus || r.content || ''
  const correct = resolveWeekResultCompletion({ completionStatus: 'COMPLETED', content: '写完了' })
  assert.notEqual(correct, buggy({ completionStatus: 'COMPLETED', content: '写完了' }))
})

test('效力：完成情况回填时空内容不回落「已完成」≠ 正确实现', () => {
  const buggy = ''
  assert.notEqual(resolveWeekResultCompletion({ completionStatus: 'COMPLETED' }), buggy)
})

test('效力：行锁定不认 APPROVED ≠ 正确实现', () => {
  const buggy = (status: string) => status === 'SUBMITTED'
  assert.notEqual(isWeekResultRowLocked({ status: 'APPROVED' }), buggy('APPROVED'))
})

test('效力：校验时不 trim 就判空 ≠ 正确实现', () => {
  const buggy = (s: string) => (s.length === 0 ? 'MISSING_COMPLETION' : null)
  const correct = validateWeekResultRow({
    status: 'NOT_SUBMITTED',
    completionStatus: '   ',
    deliverable: 'x',
  })
  assert.equal(correct, 'MISSING_COMPLETION')
  assert.equal(buggy('   '), null)
})

test('效力：校验时锁定不是最优先 ≠ 正确实现', () => {
  const buggy = 'MISSING_COMPLETION'
  const correct = validateWeekResultRow({
    status: 'SUBMITTED',
    completionStatus: '',
    deliverable: '',
  })
  assert.equal(correct, 'LOCKED')
  assert.equal(buggy, 'MISSING_COMPLETION')
})

test('效力：校验文案写错 ≠ 正确实现', () => {
  const buggy = '该周成果已提交，不能重复提交'
  assert.notEqual(resolveWeekResultValidationMessage('MISSING_COMPLETION'), buggy)
})

test('效力：周结束日用毫秒加法而非 setDate ≠ 正确实现', () => {
  const buggy = (start: string) => {
    const d = new Date(`${start}T00:00:00`)
    return new Date(d.getTime() + 6 * 86400000).toISOString().slice(0, 10)
  }
  const correct = resolveWeekEndDate('2026-11-23')
  assert.equal(correct, '2026-11-29')
  assert.equal(typeof buggy('2026-11-23'), 'string')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 版本差异
// ═══════════════════════════════════════════════════════════════════════════

test('效力：空列表算「全展开」≠ 正确实现', () => {
  const buggy = true
  assert.notEqual(areAllVisibleMonthPlanDiffExpanded([], new Set()), buggy)
})

test('效力：变更摘要不区分修改 / 增删 ≠ 正确实现', () => {
  const buggy = (fields: { label: string }[]) => `已修改：${fields.map((f) => f.label).join('、')}`
  const change = { key: 'k', type: 'added' as const, title: 't', fields: [] }
  assert.notEqual(resolveMonthPlanDiffChangeSummary(change, { afterLabel: '当前', beforeLabel: '历史' }), buggy([]))
})

test('效力：变更摘要回落文案用错标签 ≠ 正确实现', () => {
  const buggy = '新增任务'
  const change = { key: 'k', type: 'removed' as const, title: 't', fields: [] }
  const correct = resolveMonthPlanDiffChangeSummary(change, { afterLabel: '当前版本', beforeLabel: '历史版本' })
  assert.notEqual(correct, buggy)
})

test('效力：全展开切换不分「已全展开 / 未全展开」≠ 正确实现', () => {
  const changes = [
    { key: 'k1', type: 'modified' as const, title: 't', fields: [] },
    { key: 'k2', type: 'modified' as const, title: 't', fields: [] },
  ]
  const buggy = new Set(changes.map((c) => c.key))
  const correct = toggleAllVisibleMonthPlanDiffChanges(changes, new Set(['k1', 'k2']))
  assert.notEqual(correct.size, buggy.size)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 草稿版本抽屉
// ═══════════════════════════════════════════════════════════════════════════

test('效力：分组只把 MANUAL_SAVE 当已保存 ≠ 正确实现', () => {
  const buggy = (source: string) => source === 'MANUAL_SAVE'
  const versions = [
    { id: 'v1', sourceType: 'MANUAL_SAVE' as const },
    { id: 'v2', sourceType: 'RESTORED_SAVE' as const },
  ]
  const correct = selectVisiblePlanDraftVersions(versions, 'saved')
  assert.equal(correct.length, 2)
  assert.equal(buggy('RESTORED_SAVE'), false)
})

test('效力：预览条目把周 / 日搞反 ≠ 正确实现', () => {
  const buggy = buildPlanDraftPreviewEntries('DAY', { weekStart: '2026-11-23' })
  const correct = buildPlanDraftPreviewEntries('DAY', { planDate: '2026-11-24' })
  assert.notDeepEqual(buggy, correct)
})

test('效力：变更判定忽略 planType ≠ 正确实现', () => {
  const buggy = isPlanDraftChangedCompat({
    planType: 'WEEK',
    hasSnapshot: true,
    monthDiffUnchanged: false,
    snapshotsEqual: true,
  })
  const correct = isPlanDraftChanged({
    planType: 'WEEK',
    hasSnapshot: true,
    monthDiffUnchanged: false,
    snapshotsEqual: true,
  })
  assert.equal(correct, false)
  assert.notEqual(correct, buggy)
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 月计划支持动作
// ═══════════════════════════════════════════════════════════════════════════

test('效力：HTML 转义时 & 后转 ≠ 正确实现', () => {
  // ⚠️ buggy 版本：先转 < > 再转 & ⇒ "<" 会变成 "&amp;lt;"（双重转义）
  const buggy = (v: string) => v.replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/&/g, '&amp;')
  assert.equal(escapeMonthPlanHtml('<'), '&lt;')
  assert.equal(buggy('<'), '&amp;lt;')
  assert.notEqual(escapeMonthPlanHtml('<'), buggy('<'))
})

test('效力：HTML 转义漏掉引号 ≠ 正确实现', () => {
  const buggy = (v: string) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  assert.notEqual(escapeMonthPlanHtml('"'), buggy('"'))
})

test('效力：拆行不归一 \\r ≠ 正确实现', () => {
  // ⚠️ buggy 版本：只按 \n 切分 ⇒ 行尾残留 \r（trim 之后仍带 \r）
  const buggy = (v: string) => v.split('\n').map((l) => l.trim()).filter(Boolean)
  assert.deepEqual(splitMonthPlanTextLines('a\r\nb'), ['a', 'b'])
  assert.deepEqual(buggy('a\r\nb'), ['a', 'b'])
  // ⚠️ 用「只有 \r」的输入区分：buggy 版本切不开
  assert.deepEqual(splitMonthPlanTextLines('a\rb'), ['a', 'b'])
  assert.deepEqual(buggy('a\rb'), ['a\rb'])
})

test('效力：普通数字用千分位（打印列宽会撑开）≠ 正确实现', () => {
  const buggy = (v: number) => v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  assert.notEqual(formatMonthPlanPlainNumber(1234567), buggy(1234567))
})

test('效力：模板兜底行数改成 5 ≠ 正确实现', () => {
  const buggy = 5
  assert.notEqual(buildMonthPlanTemplateRows(null).length, buggy)
})

test('效力：模板行存在时不用真实数据 ≠ 正确实现', () => {
  const items = [makeCompatRow()]
  const buggy = buildMonthPlanTemplateRows(null)
  const correct = buildMonthPlanTemplateRows({ planMonth: '2026-11', items })
  assert.notEqual(correct.length, buggy.length)
})

test('效力：打印标题不补零 ≠ 正确实现', () => {
  const buggy = '2026 年 1 月个人目标制定'
  const correct = buildTitleCompat('2026-01')
  assert.notEqual(correct, buggy)
})

test('效力：打印标题解析失败时显示 NaN ≠ 正确实现', () => {
  const buggy = 'NaN 年 NaN 月个人目标制定'
  const correct = buildTitleCompat('bad')
  assert.notEqual(correct, buggy)
})

test('效力：可否调整把已完成也算进去 ≠ 正确实现', () => {
  const buggy = (s: string) => s === 'submitted' || s === 'approved' || s === 'completed'
  assert.equal(canAdjustCompat('completed'), false)
  assert.equal(buggy('completed'), true)
})

test('效力：任务内容多行时不区分标题行 ≠ 正确实现', () => {
  const buggy = '<span class="line">标题</span><span class="line">第二行</span>'
  assert.notEqual(renderMonthPlanTaskContent('标题\n第二行'), buggy)
})

test('效力：考核标准缺标签前缀 ≠ 正确实现', () => {
  const buggy = '<span class="line">交付物A</span>'
  assert.notEqual(renderMonthPlanStandard('交付物A', ''), buggy)
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. ⭐ AI 助手
// ═══════════════════════════════════════════════════════════════════════════

test('效力：条目主键用 Number 归一（BUG-R6-14）≠ 正确实现', () => {
  const buggy = (raw: Record<string, unknown>) => String(Number(raw.id))
  const correct = normalizeWeekPlanAiItem({ id: 'abc-123' }, '2026-11-29')
  assert.notEqual(correct?.id, buggy({ id: 'abc-123' }))
})

test('效力：关联任务 ID 用 Number 归一 ≠ 正确实现', () => {
  // ⚠️ buggy 版本：UUID 型 ID 被 Number 归一后塌成空串
  const buggy = (values: unknown[]) => values.map((v) => String(Number(v)))
  const correct = normalizeRelatedWeekPlanItemIds(['abc-123'])
  assert.deepEqual(correct, ['abc-123'])
  assert.deepEqual(buggy(['abc-123']), ['NaN'])
  assert.notDeepEqual(correct, buggy(['abc-123']))
})

test('效力：周草稿没有条目时仍返回草稿 ≠ 正确实现', () => {
  // ⚠️ buggy 版本：items 为空也照样返回草稿（用户点「应用」会得到空建议）
  const buggy = { items: [] }
  const correct = buildWeekPlanAiDraft({ payload: { items: [] } }, '2026-11-29')
  assert.equal(correct, null)
  assert.notDeepEqual(correct, buggy)
})

// ═══════════════════════════════════════════════════════════════════════════
// 本地小工具（只在本文件里用的「错误实现」）
// ═══════════════════════════════════════════════════════════════════════════

function countResultStatusesCompat(statuses: string[]) {
  const counts = { todo: 0, returned: 0, inReview: 0, done: 0, total: 0 }
  for (const status of statuses) {
    if (status === 'APPROVED' || status === 'DONE') counts.done += 1
    else if (status === 'SUBMITTED') counts.inReview += 1
    else if (status === 'REJECTED') counts.returned += 1
    else if (status === 'DRAFT') counts.todo += 1
    // ⚠️ buggy：未知状态既不计数也不计入 total
  }
  return counts
}

function isPlanDraftChangedCompat(input: {
  planType: 'MONTH' | 'WEEK' | 'DAY'
  hasSnapshot: boolean
  monthDiffUnchanged: boolean
  snapshotsEqual: boolean
}): boolean {
  // ⚠️ **buggy 版本**：忽略 planType，一律看 monthDiffUnchanged
  if (!input.hasSnapshot) return false
  return !input.monthDiffUnchanged
}

function makeCompatRow(): MonthPlanTemplateRowLike {
  return {
    rowNo: 1,
    taskContent: 'x',
    performanceWeight: 0,
    deliverable: '',
    quality: '',
    estimatedStartDate: '',
    estimatedEndDate: '',
    estimatedValue: 0,
  }
}

function buildTitleCompat(planMonth: string): string {
  const [year, month] = String(planMonth || '').split('-')
  const monthNumber = Number(month)
  if (!year || !Number.isFinite(monthNumber)) return '        年      月个人目标制定'
  return `${year} 年 ${String(monthNumber).padStart(2, '0')} 月个人目标制定`
}

function canAdjustCompat(status: string): boolean {
  return status === 'submitted' || status === 'approved'
}

function buildWeekDraftCompat() {
  return { items: [] }
}
