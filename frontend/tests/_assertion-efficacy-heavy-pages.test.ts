import assert from 'node:assert/strict'
import test from 'node:test'
import {
  collectMonthPlanIssues,
  computeMonthPlanReadiness,
  countHiddenMonthPlanIssues,
  previewMonthPlanIssueGroups,
  sumRegularWeights,
  type MonthPlanEditableItem,
} from '../src/utils/monthPlanEditor.ts'
import {
  computePackagePercentage,
  countEmployeesByOrganization,
  filterDepartmentEmployees,
  resolveSelectedOrgIds,
  type DepartmentEmployeeRow,
  type OrgScopeNode,
} from '../src/utils/departmentDashboard.ts'

/**
 * 🔴 **断言有效性自检 —— 重页面抽纯函数**（CI-5 阶段 2 第三轮）
 *
 * ⚠️ **这个文件是干什么的**
 * ------------------------------------------------------------------
 * 抽纯函数最大的风险是「**测了，但测不出东西**」——
 * 断言写得太松，或者断言的恰好是「无论逻辑对错都成立」的东西。
 *
 * ⚠️ **这里的做法**
 *   在**测试内**复刻一份「**故意写错的实现**」，
 *   断言它与正确实现的**输出必须不同**。
 *
 *   ⇒ 证明纯函数测试里那几条断言用的判据，
 *     **确实能区分「逻辑对」和「逻辑错」**，不是在凑数。
 *
 * ⚠️ **为什么不直接改产品源码做实验**
 *   改 ``src/`` 里的工具模块来验证 ⇒ 会污染工作区、可能被误提交。
 *   这里复刻逻辑、只动测试文件 ⇒ **零产品代码风险**。
 *
 * ⚠️ **注意**：这些用例**永远通过** ——
 *   它们验证的是「两种实现有差异」这个事实，
 *   而不是「某个实现对不对」。
 *   对不对由 ``month-plan-editor-utils.test.ts`` / ``department-dashboard-utils.test.ts`` 负责。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

function fullItem(overrides: Partial<MonthPlanEditableItem> = {}): MonthPlanEditableItem {
  return {
    taskName: '任务名',
    taskContent: '任务内容',
    deliverable: '交付物',
    quality: '质量标准',
    estimatedStartDate: '2026-11-01',
    estimatedEndDate: '2026-11-30',
    performanceWeight: 100,
    estimatedValue: 1000,
    ...overrides,
  }
}

const ORG_TREE: OrgScopeNode[] = [
  { id: 'A', children: [{ id: 'B' }, { id: 'C' }] },
]

function employee(overrides: Partial<DepartmentEmployeeRow> & { ownerId: string }): DepartmentEmployeeRow {
  return {
    employeeNo: 'E001',
    employeeName: '张三',
    orgId: 'A',
    monthPlanCount: 0,
    resultCount: 0,
    ...overrides,
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. 权重合计的浮点精度
// ═══════════════════════════════════════════════════════════════════════════

test('❌ 权重合计若直接求和 ⇒ 浮点误差会让 0.1+0.2 ≠ 0.3（判据有区分力）', () => {
  // ❌ **错误实现**：不做 toFixed(2)
  function sumNaive(items: Array<{ performanceWeight: number | null }>) {
    return items.reduce((sum, item) => sum + (Number(item.performanceWeight) || 0), 0)
  }

  const items = [{ performanceWeight: 0.1 }, { performanceWeight: 0.2 }]
  assert.equal(sumNaive(items) === 0.3, false)
  assert.equal(sumRegularWeights(items) === 0.3, true)
})

test('❌ 权重合计若不做四舍五入 ⇒ 二进制浮点误差会让合法权重被判为不等于 100（判据有区分力）', () => {
  // ❌ **错误实现**：直接比较浮点求和
  function totalIsExactly100(items: Array<{ performanceWeight: number | null }>) {
    const total = items.reduce((sum, item) => sum + (Number(item.performanceWeight) || 0), 0)
    return total === 100
  }

  // ⚠️ ``0.01 + 64.04 + 35.95`` 在 IEEE754 下 = ``100.00000000000001`` ⇒ 朴素比较会**误报**权重不足。
  const items = [
    { performanceWeight: 0.01 },
    { performanceWeight: 64.04 },
    { performanceWeight: 35.95 },
  ]
  assert.equal(totalIsExactly100(items), false)
  assert.equal(sumRegularWeights(items) === 100, true)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 校验规则的边界
// ═══════════════════════════════════════════════════════════════════════════

test('❌ 若把权重校验写成 >= 0 ⇒ 权重 0 会被漏掉（判据有区分力）', () => {
  // ❌ **错误实现**：把 `!weight` 写成 `weight < 0`
  function naiveWeightIssue(item: MonthPlanEditableItem) {
    if (!(Number(item.performanceWeight) < 0 || Number(item.performanceWeight) > 100)) return null
    return '任务 1：请填写有效绩效权重'
  }

  const zeroWeight = fullItem({ performanceWeight: 0 })
  assert.equal(naiveWeightIssue(zeroWeight), null)

  const issues = collectMonthPlanIssues('2026-11', [zeroWeight])
  assert.equal(issues.some((issue) => issue.id === '0-performanceWeight'), true)
})

test('❌ 若把价值上限写成 1e10 ⇒ 超出 9999999999.99 的值会被放过（判据有区分力）', () => {
  // ❌ **错误实现**：上限写成 1e10
  function naiveValueIssue(item: MonthPlanEditableItem) {
    const value = Number(item.estimatedValue)
    if (value >= 0 && value <= 1e10) return null
    return '超范围'
  }

  const overLimit = fullItem({ estimatedValue: 9999999999.995 })
  assert.equal(naiveValueIssue(overLimit), null)

  const issues = collectMonthPlanIssues('2026-11', [overLimit])
  assert.equal(issues.some((issue) => issue.id === '0-estimatedValue'), true)
})

test('❌ 若日期比较写成 <= ⇒ 同一天会被误报（判据有区分力）', () => {
  // ❌ **错误实现**：把 `>` 写成 `>=`（同一天也算「结束早于开始」）
  function naiveDateIssue(item: MonthPlanEditableItem) {
    if (item.estimatedStartDate && item.estimatedEndDate && item.estimatedStartDate >= item.estimatedEndDate) {
      return '顺序错误'
    }
    return null
  }

  const sameDay = fullItem({ estimatedStartDate: '2026-11-01', estimatedEndDate: '2026-11-01' })
  assert.equal(naiveDateIssue(sameDay), '顺序错误')

  const issues = collectMonthPlanIssues('2026-11', [sameDay])
  assert.equal(issues.some((issue) => issue.id === '0-estimatedEndDate-order'), false)
})

test('❌ 若空任务也报权重合计问题 ⇒ 空计划会多出噪音提示（判据有区分力）', () => {
  // ❌ **错误实现**：去掉 `items.length &&` 前置条件
  function naiveWeightTotalIssue(planMonth: string, items: MonthPlanEditableItem[]) {
    if (sumRegularWeights(items) !== 100) {
      return { id: 'weight-total', message: '常规任务权重合计需为 100%' }
    }
    return null
  }

  assert.notEqual(naiveWeightTotalIssue('2026-11', []), null)

  const issues = collectMonthPlanIssues('2026-11', [])
  assert.equal(issues.some((issue) => issue.id === 'weight-total'), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 准备度打分的分段权重
// ═══════════════════════════════════════════════════════════════════════════

test('❌ 若计划月份段不参与计分 ⇒ 过去月份会被高估（判据有区分力）', () => {
  // ❌ **错误实现**：永远给计划月份 10 分
  function naiveReadiness(planMonth: string, items: MonthPlanEditableItem[], currentMonth: string) {
    if (!items.length) return 0
    const itemFieldCount = items.length * 7
    const completed = items.length * 7
    const itemScore = (completed / itemFieldCount) * 65
    const weightScore = 25
    return Math.max(0, Math.min(100, Math.round(10 + itemScore + weightScore)))
  }

  const current = '2026-11'
  const past = computeMonthPlanReadiness('2026-10', [fullItem()], current)
  const naivePast = naiveReadiness('2026-10', [fullItem()], current)
  assert.notEqual(past, naivePast)
  assert.equal(naivePast, 100)
})

test('❌ 若权重段按「距离 100 的差值」直接扣分 ⇒ 权重为 0 会得到负分（判据有区分力）', () => {
  // ❌ **错误实现**：weightScore = -distance（不做归一化）
  function naiveWeightScore(weightTotal: number) {
    return -Math.abs(weightTotal - 100)
  }

  assert.ok(naiveWeightScore(0) < 0)

  const score = computeMonthPlanReadiness('2026-11', [fullItem({ performanceWeight: 0 })], '2026-11')
  assert.ok(score >= 0)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 问题折叠的分组上限
// ═══════════════════════════════════════════════════════════════════════════

test('❌ 若两组都用同一个上限 ⇒ 任务组会少显示一条（判据有区分力）', () => {
  // ❌ **错误实现**：任务组也用 3 条上限
  function naivePreview(issues: ReturnType<typeof collectMonthPlanIssues>) {
    return [
      { key: 'plan', issues: issues.filter((i) => i.itemIndex === undefined).slice(0, 3) },
      { key: 'tasks', issues: issues.filter((i) => i.itemIndex !== undefined).slice(0, 3) },
    ].filter((group) => group.issues.length)
  }

  const issues = Array.from({ length: 6 }, (_, index) => ({
    id: `task-${index}`,
    itemIndex: 0,
    field: 'taskContent',
    message: `问题 ${index}`,
  }))

  const naive = naivePreview(issues)
  const correct = previewMonthPlanIssueGroups(issues)
  assert.equal(naive[0].issues.length, 3)
  assert.equal(correct[0].issues.length, 4)
})

test('❌ 若隐藏数只算第一组 ⇒ 折叠提示会少报（判据有区分力）', () => {
  // ❌ **错误实现**：只统计 plan 组
  function naiveHiddenCount(issues: ReturnType<typeof collectMonthPlanIssues>) {
    const plan = issues.filter((i) => i.itemIndex === undefined)
    return Math.max(0, plan.length - 3)
  }

  const issues = [
    ...Array.from({ length: 5 }, (_, index) => ({
      id: `plan-${index}`,
      field: 'planMonth',
      message: `计划 ${index}`,
    })),
    ...Array.from({ length: 7 }, (_, index) => ({
      id: `task-${index}`,
      itemIndex: 0,
      field: 'taskContent',
      message: `任务 ${index}`,
    })),
  ]

  assert.notEqual(naiveHiddenCount(issues), countHiddenMonthPlanIssues(issues))
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 组织范围的过滤语义
// ═══════════════════════════════════════════════════════════════════════════

test('❌ 若组织过滤只看直属不看后代 ⇒ 下级员工会被漏掉（判据有区分力）', () => {
  // ❌ **错误实现**：只收集自身 ID，不含后代
  function naiveSelectedIds(tree: OrgScopeNode[], id: string) {
    const node = tree.find((item) => item.id === id)
    return new Set(node ? [node.id] : [])
  }

  const employees = [
    employee({ ownerId: '1', orgId: 'A' }),
    employee({ ownerId: '2', orgId: 'B' }),
  ]

  const naive = filterDepartmentEmployees(employees, { organizationIds: naiveSelectedIds(ORG_TREE, 'A') })
  const correct = filterDepartmentEmployees(employees, { organizationIds: resolveSelectedOrgIds(ORG_TREE, 'A') })
  assert.equal(naive.length, 1)
  assert.equal(correct.length, 2)
})

test('❌ 若「找不到组织」返回 null 而不是空集合 ⇒ 会退化成不过滤（判据有区分力）', () => {
  // ❌ **错误实现**：找不到时返回 null
  function naiveSelectedIds(tree: OrgScopeNode[], id: string) {
    const node = tree.find((item) => item.id === id)
    return node ? new Set([node.id]) : null
  }

  const employees = [employee({ ownerId: '1', orgId: 'A' })]
  const naive = filterDepartmentEmployees(employees, { organizationIds: naiveSelectedIds(ORG_TREE, 'NOPE') })
  const correct = filterDepartmentEmployees(employees, { organizationIds: resolveSelectedOrgIds(ORG_TREE, 'NOPE') })
  assert.equal(naive.length, 1)
  assert.equal(correct.length, 0)
})

test('❌ 若组织人数只统计直属 ⇒ 父组织人数会偏小（判据有区分力）', () => {
  // ❌ **错误实现**：不做子树累加
  function naiveCounts(tree: OrgScopeNode[], employees: DepartmentEmployeeRow[]) {
    const totals = new Map<string, number>()
    tree.forEach((node) => {
      totals.set(node.id, employees.filter((e) => e.orgId === node.id).length)
    })
    return totals
  }

  const employees = [
    employee({ ownerId: '1', orgId: 'A' }),
    employee({ ownerId: '2', orgId: 'B' }),
  ]

  const naive = naiveCounts(ORG_TREE, employees)
  const correct = countEmployeesByOrganization(ORG_TREE, employees)
  assert.equal(naive.get('A'), 1)
  assert.equal(correct.get('A'), 2)
})

test('❌ 若关键字只匹配姓名不匹配工号 ⇒ 按工号搜索会搜不到（判据有区分力）', () => {
  // ❌ **错误实现**：只查姓名
  function naiveFilter(employees: DepartmentEmployeeRow[], keyword: string) {
    const key = keyword.trim().toLowerCase()
    return employees.filter((item) => item.employeeName.toLowerCase().includes(key))
  }

  const employees = [
    employee({ ownerId: '1', employeeName: '张三', employeeNo: 'E001' }),
    employee({ ownerId: '2', employeeName: '李四', employeeNo: 'E002' }),
  ]

  assert.equal(naiveFilter(employees, 'E002').length, 0)
  assert.equal(filterDepartmentEmployees(employees, { keyword: 'E002' }).length, 1)
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 项目组完成度
// ═══════════════════════════════════════════════════════════════════════════

test('❌ 若成员数为 0 时仍做除法 ⇒ 会得到 NaN（判据有区分力）', () => {
  // ❌ **错误实现**：不判空直接除
  function naivePercentage(approvedCount: number, totalMemberCount: number) {
    return Math.round((approvedCount / totalMemberCount) * 100)
  }

  assert.ok(Number.isNaN(naivePercentage(0, 0)))
  assert.equal(computePackagePercentage({ approvedCount: 0, totalMemberCount: 0 }), 0)
})

test('❌ 若完成度不四舍五入 ⇒ 2/3 会显示成 66.666…（判据有区分力）', () => {
  // ❌ **错误实现**：不取整
  function naivePercentage(approvedCount: number, totalMemberCount: number) {
    return (approvedCount / totalMemberCount) * 100
  }

  assert.notEqual(naivePercentage(2, 3), 67)
  assert.equal(computePackagePercentage({ approvedCount: 2, totalMemberCount: 3 }), 67)
})
