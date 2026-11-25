import assert from 'node:assert/strict'
import test from 'node:test'
import {
  countPendingResultTasks,
  countResultStatuses,
  filterResultPackages,
  formatCompactResultDate,
  isValidResultMonthKey,
  normalizeResultStatus,
  resolveResultMonthFromQuery,
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

function makePackage(
  overrides: Partial<ResultWorkbenchPackageLike> = {},
): ResultWorkbenchPackageLike {
  return {
    type: 'MONTH',
    planId: 'p1',
    title: '11月月计划',
    counts: { pending: 0, rejected: 0, submitted: 0 },
    tasks: [],
    ...overrides,
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 状态归一化与筛选
// ═══════════════════════════════════════════════════════════════════════════

test('状态归一化：DRAFT / TODO / PENDING ⇒ 待办', () => {
  assert.equal(normalizeResultStatus('DRAFT'), 'TODO')
  assert.equal(normalizeResultStatus('TODO'), 'TODO')
  assert.equal(normalizeResultStatus('PENDING'), 'TODO')
})

test('状态归一化：REJECTED / RETURNED ⇒ 已退回', () => {
  assert.equal(normalizeResultStatus('REJECTED'), 'RETURNED')
  assert.equal(normalizeResultStatus('RETURNED'), 'RETURNED')
})

test('状态归一化：SUBMITTED / IN_REVIEW ⇒ 审批中', () => {
  assert.equal(normalizeResultStatus('SUBMITTED'), 'IN_REVIEW')
  assert.equal(normalizeResultStatus('IN_REVIEW'), 'IN_REVIEW')
})

test('状态归一化：APPROVED / CONFIRMED / DONE ⇒ 已完成', () => {
  assert.equal(normalizeResultStatus('APPROVED'), 'DONE')
  assert.equal(normalizeResultStatus('CONFIRMED'), 'DONE')
  assert.equal(normalizeResultStatus('DONE'), 'DONE')
})

test('状态归一化：大小写不敏感', () => {
  assert.equal(normalizeResultStatus('draft'), 'TODO')
  assert.equal(normalizeResultStatus('approved'), 'DONE')
})

test('状态归一化：未知状态按「待办」处理（不被静默丢掉）', () => {
  assert.equal(normalizeResultStatus('WHATEVER'), 'TODO')
  assert.equal(normalizeResultStatus(''), 'TODO')
})

test('筛选匹配：待处理 = 待办 + 已退回', () => {
  assert.equal(taskMatchesResultFilter('DRAFT', 'PENDING'), true)
  assert.equal(taskMatchesResultFilter('REJECTED', 'PENDING'), true)
  assert.equal(taskMatchesResultFilter('SUBMITTED', 'PENDING'), false)
})

test('筛选匹配：审批中只看 IN_REVIEW 类', () => {
  assert.equal(taskMatchesResultFilter('SUBMITTED', 'IN_REVIEW'), true)
  assert.equal(taskMatchesResultFilter('APPROVED', 'IN_REVIEW'), false)
})

test('筛选匹配：已完成只看 DONE 类', () => {
  assert.equal(taskMatchesResultFilter('APPROVED', 'DONE'), true)
  assert.equal(taskMatchesResultFilter('DRAFT', 'DONE'), false)
})

test('状态计数：四类各自累加', () => {
  const counts = countResultStatuses(['DRAFT', 'REJECTED', 'SUBMITTED', 'APPROVED'])
  assert.equal(counts.todo, 1)
  assert.equal(counts.returned, 1)
  assert.equal(counts.inReview, 1)
  assert.equal(counts.done, 1)
  assert.equal(counts.total, 4)
})

test('状态计数：空数组 ⇒ 全 0', () => {
  const counts = countResultStatuses([])
  assert.deepEqual(counts, { todo: 0, returned: 0, inReview: 0, done: 0, total: 0 })
})

test('待处理计数 = 待办 + 已退回', () => {
  assert.equal(countPendingResultTasks({ todo: 3, returned: 2 }), 5)
})

test('过滤成果包：只保留至少有一个任务命中筛选的包', () => {
  const packages = [
    makePackage({ planId: 'a', tasks: [{ status: 'DRAFT' }] }),
    makePackage({ planId: 'b', tasks: [{ status: 'APPROVED' }] }),
    makePackage({ planId: 'c', tasks: [] }),
  ]
  const visible = filterResultPackages(packages, 'PENDING')
  assert.deepEqual(visible.map((item) => item.planId), ['a'])
})

test('过滤成果包：审批中筛选只留 SUBMITTED', () => {
  const packages = [
    makePackage({ planId: 'a', tasks: [{ status: 'SUBMITTED' }] }),
    makePackage({ planId: 'b', tasks: [{ status: 'DRAFT' }] }),
  ]
  assert.deepEqual(filterResultPackages(packages, 'IN_REVIEW').map((item) => item.planId), ['a'])
})

test('排序：待处理档位下有退回的排前面', () => {
  const packages = [
    makePackage({ planId: 'a', counts: { pending: 1, rejected: 0, submitted: 0 } }),
    makePackage({ planId: 'b', counts: { pending: 1, rejected: 2, submitted: 0 } }),
  ]
  assert.deepEqual(sortResultPackagesByRejection(packages, 'PENDING').map((item) => item.planId), ['b', 'a'])
})

test('排序：非待处理档位保持原顺序（返回原数组引用）', () => {
  const packages = [
    makePackage({ planId: 'a', counts: { pending: 1, rejected: 0, submitted: 0 } }),
    makePackage({ planId: 'b', counts: { pending: 1, rejected: 2, submitted: 0 } }),
  ]
  const sorted = sortResultPackagesByRejection(packages, 'IN_REVIEW')
  assert.equal(sorted, packages)
  assert.deepEqual(sorted.map((item) => item.planId), ['a', 'b'])
})

test('排序：同组内保持原顺序（排序稳定）', () => {
  const packages = [
    makePackage({ planId: 'a', counts: { pending: 1, rejected: 0, submitted: 0 } }),
    makePackage({ planId: 'b', counts: { pending: 1, rejected: 0, submitted: 0 } }),
  ]
  assert.deepEqual(sortResultPackagesByRejection(packages, 'PENDING').map((item) => item.planId), ['a', 'b'])
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 空态文案
// ═══════════════════════════════════════════════════════════════════════════

test('空态标题：三档各有文案', () => {
  assert.equal(resolveResultWorkbenchEmptyTitle('PENDING'), '当前月份没有待处理成果')
  assert.equal(resolveResultWorkbenchEmptyTitle('IN_REVIEW'), '当前月份没有审批中的成果')
  assert.equal(resolveResultWorkbenchEmptyTitle('DONE'), '当前月份没有已完成成果')
})

test('空态提示：一个任务都没有 ⇒ 提示会自动生成', () => {
  assert.equal(resolveResultWorkbenchEmptyHint(0), '计划审批通过后，将自动生成对应成果任务。')
})

test('空态提示：有任务只是筛选没命中 ⇒ 提示切换状态', () => {
  assert.equal(resolveResultWorkbenchEmptyHint(5), '可切换上方状态查看其他成果。')
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 展示文案
// ═══════════════════════════════════════════════════════════════════════════

test('紧凑日期：11-24 ⇒ 11.24', () => {
  assert.equal(formatCompactResultDate('2026-11-24'), '11.24')
})

test('紧凑日期：空值 ⇒ --', () => {
  assert.equal(formatCompactResultDate(''), '--')
  assert.equal(formatCompactResultDate(undefined), '--')
})

test('紧凑日期：长度不足 10 位原样返回', () => {
  assert.equal(formatCompactResultDate('11-24'), '11-24')
})

test('周标题：按「几月第几周」换算', () => {
  assert.equal(resolveResultWeekTitle({ title: '原标题', startDate: '2026-11-01' }), '11月第1周')
  assert.equal(resolveResultWeekTitle({ title: '原标题', startDate: '2026-11-24' }), '11月第4周')
})

test('周标题：日期无效 ⇒ 回落到原标题（不显示 NaN）', () => {
  assert.equal(resolveResultWeekTitle({ title: '原标题', startDate: 'not-a-date' }), '原标题')
  assert.equal(resolveResultWeekTitle({ title: '原标题' }), '原标题')
})

test('包标题：月包用原标题', () => {
  assert.equal(resolveResultPackageTitle(makePackage({ type: 'MONTH', title: '11月月计划' })), '11月月计划')
})

test('包标题：周包加「执行成果」后缀', () => {
  const item = makePackage({ type: 'WEEK', title: '原标题', startDate: '2026-11-01' })
  assert.equal(resolveResultPackageTitle(item), '11月第1周执行成果')
})

test('包状态文案：审批中 ⇒ N项审批中', () => {
  const item = makePackage({ counts: { pending: 1, rejected: 0, submitted: 3 } })
  assert.equal(resolveResultPackageStateText(item, 'IN_REVIEW'), '3项审批中')
})

test('包状态文案：已完成 ⇒ 按任务实际数量统计', () => {
  const item = makePackage({
    counts: { pending: 0, rejected: 0, submitted: 0 },
    tasks: [{ status: 'APPROVED' }, { status: 'DRAFT' }],
  })
  assert.equal(resolveResultPackageStateText(item, 'DONE'), '1项已完成')
})

test('包状态文案：待处理 ⇒ pending 要减掉退回数', () => {
  const item = makePackage({ counts: { pending: 3, rejected: 3, submitted: 0 } })
  assert.equal(resolveResultPackageStateText(item, 'PENDING'), '3项被退回')
})

test('包状态文案：待处理 ⇒ 有待填写项时用「，」连接', () => {
  const item = makePackage({ counts: { pending: 5, rejected: 2, submitted: 0 } })
  assert.equal(resolveResultPackageStateText(item, 'PENDING'), '2项被退回，3项待填写')
})

test('包状态文案：待处理 ⇒ 全是 0 时为空串', () => {
  const item = makePackage({ counts: { pending: 0, rejected: 0, submitted: 0 } })
  assert.equal(resolveResultPackageStateText(item, 'PENDING'), '')
})

test('操作按钮：审批中 ⇒ 查看进度', () => {
  const item = makePackage({ counts: { pending: 0, rejected: 0, submitted: 1 } })
  assert.equal(resolveResultPackageActionLabel(item, 'IN_REVIEW'), '查看进度')
})

test('操作按钮：已完成 ⇒ 查看成果', () => {
  const item = makePackage()
  assert.equal(resolveResultPackageActionLabel(item, 'DONE'), '查看成果')
})

test('操作按钮：有退回 ⇒ 处理退回（优先于草稿）', () => {
  const item = makePackage({
    counts: { pending: 1, rejected: 1, submitted: 0 },
    tasks: [{ status: 'DRAFT' }],
  })
  assert.equal(resolveResultPackageActionLabel(item, 'PENDING'), '处理退回')
})

test('操作按钮：有草稿 ⇒ 继续填写', () => {
  const item = makePackage({ tasks: [{ status: 'DRAFT' }] })
  assert.equal(resolveResultPackageActionLabel(item, 'PENDING'), '继续填写')
})

test('操作按钮：其它 ⇒ 去填写', () => {
  const item = makePackage({ tasks: [{ status: 'TODO' }] })
  assert.equal(resolveResultPackageActionLabel(item, 'PENDING'), '去填写')
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 月份切换
// ═══════════════════════════════════════════════════════════════════════════

test('月份偏移：正常跨月', () => {
  assert.equal(shiftResultMonth('2026-11', 1), '2026-12')
  assert.equal(shiftResultMonth('2026-11', -1), '2026-10')
})

test('月份偏移：跨年自动进位', () => {
  assert.equal(shiftResultMonth('2026-12', 1), '2027-01')
  assert.equal(shiftResultMonth('2026-01', -1), '2025-12')
})

test('月份偏移：结果总是补零的 YYYY-MM', () => {
  assert.equal(shiftResultMonth('2026-01', 0), '2026-01')
  assert.equal(shiftResultMonth('2026-09', 1), '2026-10')
})

test('月份格式校验：只认严格的 YYYY-MM', () => {
  assert.equal(isValidResultMonthKey('2026-11'), true)
  assert.equal(isValidResultMonthKey('2026-1'), false)
  assert.equal(isValidResultMonthKey('202611'), false)
  assert.equal(isValidResultMonthKey(''), false)
  assert.equal(isValidResultMonthKey(null), false)
  assert.equal(isValidResultMonthKey(202611), false)
})

test('从 query 取月份：普通值', () => {
  assert.equal(resolveResultMonthFromQuery({ month: '2026-11' }), '2026-11')
})

test('从 query 取月份：数组取第一个', () => {
  assert.equal(resolveResultMonthFromQuery({ month: ['2026-11', '2026-12'] }), '2026-11')
})

test('从 query 取月份：非法 ⇒ 空串（不猜）', () => {
  assert.equal(resolveResultMonthFromQuery({ month: 'bad' }), '')
  assert.equal(resolveResultMonthFromQuery({}), '')
  assert.equal(resolveResultMonthFromQuery(undefined), '')
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 打开包时定位任务
// ═══════════════════════════════════════════════════════════════════════════

test('定位任务：待处理优先退回项', () => {
  const item = makePackage({
    tasks: [
      { taskId: '1', status: 'DRAFT' },
      { taskId: '2', status: 'REJECTED' },
    ],
  })
  assert.equal(resolveResultPackageTargetTask(item, 'PENDING')?.taskId, '2')
})

test('定位任务：待处理没有退回项 ⇒ 草稿', () => {
  const item = makePackage({
    tasks: [
      { taskId: '1', status: 'TODO' },
      { taskId: '2', status: 'DRAFT' },
    ],
  })
  assert.equal(resolveResultPackageTargetTask(item, 'PENDING')?.taskId, '2')
})

test('定位任务：待处理前两个都没有 ⇒ 任意命中项', () => {
  const item = makePackage({
    tasks: [
      { taskId: '1', status: 'SUBMITTED' },
      { taskId: '2', status: 'TODO' },
    ],
  })
  assert.equal(resolveResultPackageTargetTask(item, 'PENDING')?.taskId, '2')
})

test('定位任务：审批中取第一个命中项（不看退回）', () => {
  const item = makePackage({
    tasks: [
      { taskId: '1', status: 'REJECTED' },
      { taskId: '2', status: 'SUBMITTED' },
    ],
  })
  assert.equal(resolveResultPackageTargetTask(item, 'IN_REVIEW')?.taskId, '2')
})

test('定位任务：全都不命中 ⇒ 第一个任务（不能打开空包）', () => {
  const item = makePackage({
    tasks: [
      { taskId: '1', status: 'REJECTED' },
      { taskId: '2', status: 'DRAFT' },
    ],
  })
  assert.equal(resolveResultPackageTargetTask(item, 'DONE')?.taskId, '1')
})

test('定位任务：包没有任务 ⇒ undefined', () => {
  assert.equal(resolveResultPackageTargetTask(makePackage(), 'PENDING'), undefined)
})
