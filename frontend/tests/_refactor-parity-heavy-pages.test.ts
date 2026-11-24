import assert from 'node:assert/strict'
import test from 'node:test'
import {
  buildOptimizePreview,
  collectMonthPlanIssues,
  computeMonthPlanReadiness,
  countHiddenMonthPlanIssues,
  displayOptimizeValue,
  editableNumber,
  groupMonthPlanIssues,
  isEmptyMonthPlanItem,
  normalizeOptimizeValue,
  numberForSave,
  previewMonthPlanIssueGroups,
  resolveAutoSaveState,
  sameOptimizableValues,
  sumRegularWeights,
  taskNameForSave,
  type MonthPlanEditableItem,
} from '../src/utils/monthPlanEditor.ts'
import {
  computePackagePercentage,
  countEmployeesByOrganization,
  filterDepartmentEmployees,
  filterByOrganizationScope,
  formatPendingMembers,
  hasActiveFilter,
  paginateRows,
  previewPendingMembers,
  resolveEmptyText,
  resolvePackageStatusLabel,
  resolvePackageStatusType,
  resolveSelectedOrgIds,
  sumEmployeeMetric,
  type DepartmentEmployeeRow,
  type OrgScopeNode,
} from '../src/utils/departmentDashboard.ts'

/**
 * 🔴 **重构等价性自检**（CI-5 阶段 2 第三轮）
 *
 * ⚠️ **这个文件是干什么的**
 * ------------------------------------------------------------------
 * 把重页面里的逻辑抽成纯函数时，最大的风险是「**抽的时候改掉了行为**」——
 * 比如把 ``>`` 写成 ``>=``、把 ``toFixed(2)`` 漏掉、把某个条件合并了。
 *
 * ⚠️ **这里的做法**
 *   在**测试内**逐字复刻**重构前的原始实现**（从 git 历史里抄的），
 *   然后对**同一批输入**断言「原实现 == 新实现」。
 *
 *   ⇒ 只要重构动了任何一处语义，这批断言立刻红。
 *
 * ⚠️ **为什么不直接改产品源码做实验**
 *   这里复刻逻辑、只动测试文件 ⇒ **零产品代码风险**。
 *
 * ⚠️ **注意**：这些用例**永远通过** ——
 *   它们验证的是「重构没有改变语义」这个事实。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 夹具
// ═══════════════════════════════════════════════════════════════════════════

interface LegacyItem {
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  estimatedStartDate: string
  estimatedEndDate: string
  performanceWeight: number | null
  estimatedValue: number | null
}

function makeItem(overrides: Partial<LegacyItem> = {}): LegacyItem {
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

/** 覆盖各种边界的条目组合。 */
const ITEM_CASES: LegacyItem[] = [
  makeItem(),
  makeItem({ taskContent: '', deliverable: '', quality: '' }),
  makeItem({ estimatedStartDate: '', estimatedEndDate: '' }),
  makeItem({ performanceWeight: null, estimatedValue: null }),
  makeItem({ performanceWeight: 0 }),
  makeItem({ performanceWeight: 50 }),
  makeItem({ performanceWeight: 50.5 }),
  makeItem({ performanceWeight: 100.01 }),
  makeItem({ performanceWeight: -5 }),
  makeItem({ estimatedValue: -1 }),
  makeItem({ estimatedValue: 9999999999.99 }),
  makeItem({ estimatedValue: 10000000000 }),
  makeItem({ estimatedStartDate: '2026-11-30', estimatedEndDate: '2026-11-01' }),
  makeItem({ estimatedStartDate: '2026-11-01', estimatedEndDate: '2026-11-01' }),
  makeItem({ taskContent: '   ' }),
  makeItem({ taskName: '' }),
  makeItem({ taskName: '   ', taskContent: '  很长的一段内容  '.repeat(5) }),
]

const MONTH_CASES = ['2026-11', '2026-01', '2026-12', '2027-01', '']
const CURRENT_MONTH_CASES = ['2026-11', '2026-01', '2027-06']

// ═══════════════════════════════════════════════════════════════════════════
// 1. 权重合计 —— 与重构前逐字一致
// ═══════════════════════════════════════════════════════════════════════════

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyRegularWeightTotal(items: LegacyItem[]): number {
  return Number(items
    .reduce((sum, item) => sum + (Number(item.performanceWeight) || 0), 0)
    .toFixed(2))
}

test('parity · 权重合计与重构前完全一致', () => {
  for (const items of [[], ...ITEM_CASES.map((item) => [item])]) {
    assert.equal(sumRegularWeights(items), legacyRegularWeightTotal(items))
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 校验规则 —— 与重构前逐字一致
// ═══════════════════════════════════════════════════════════════════════════

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyValidationIssues(planMonth: string, items: LegacyItem[]) {
  const issues: Array<{ id: string; itemIndex?: number; field?: string; message: string }> = []
  if (!planMonth) issues.push({ id: 'plan-month', field: 'planMonth', message: '请选择计划月份' })
  if (!items.length) issues.push({ id: 'items-empty', message: '请至少新增一项计划任务' })
  const requiredFields: Array<[keyof LegacyItem, string]> = [['taskContent', '任务内容'], ['deliverable', '交付物'], ['quality', '质量'], ['estimatedStartDate', '预估开始日期'], ['estimatedEndDate', '预估结束日期']]
  items.forEach((item, itemIndex) => {
    requiredFields.forEach(([field, label]) => { if (!String(item[field] || '').trim()) issues.push({ id: `${itemIndex}-${String(field)}`, itemIndex, field: String(field), message: `任务 ${itemIndex + 1}：请填写${label}` }) })
    if (!item.performanceWeight || item.performanceWeight <= 0 || item.performanceWeight > 100) issues.push({ id: `${itemIndex}-performanceWeight`, itemIndex, field: 'performanceWeight', message: `任务 ${itemIndex + 1}：请填写有效绩效权重` })
    if (Number(item.estimatedValue) < 0 || Number(item.estimatedValue) > 9999999999.99) issues.push({ id: `${itemIndex}-estimatedValue`, itemIndex, field: 'estimatedValue', message: `任务 ${itemIndex + 1}：预估价值需在 0 至 9999999999.99 元之间` })
    if (item.estimatedStartDate && item.estimatedEndDate && item.estimatedStartDate > item.estimatedEndDate) issues.push({ id: `${itemIndex}-estimatedEndDate-order`, itemIndex, field: 'estimatedEndDate', message: `任务 ${itemIndex + 1}：预估结束日期不能早于开始日期` })
  })
  if (items.length && legacyRegularWeightTotal(items) !== 100) issues.push({ id: 'weight-total', field: 'performanceWeight', message: `常规任务权重合计需为 100%，当前为 ${legacyRegularWeightTotal(items)}%` })
  return issues
}

test('parity · 校验问题列表与重构前完全一致（含顺序与文案）', () => {
  for (const planMonth of MONTH_CASES) {
    for (const items of [[], ...ITEM_CASES.map((item) => [item])]) {
      const actual = collectMonthPlanIssues(planMonth, items as MonthPlanEditableItem[])
      const legacy = legacyValidationIssues(planMonth, items)
      assert.deepEqual(actual, legacy, `planMonth=${planMonth}`)
    }
  }
})

test('parity · 多任务组合下校验问题列表与重构前完全一致', () => {
  const combos: LegacyItem[][] = [
    [makeItem({ performanceWeight: 50 }), makeItem({ performanceWeight: 50 })],
    [makeItem({ performanceWeight: 60 }), makeItem({ performanceWeight: 60 })],
    [makeItem({ performanceWeight: 30 }), makeItem({ performanceWeight: 30 }), makeItem({ performanceWeight: 40 })],
    [makeItem({ taskContent: '' }), makeItem({ quality: '' })],
  ]
  for (const planMonth of MONTH_CASES) {
    for (const items of combos) {
      assert.deepEqual(
        collectMonthPlanIssues(planMonth, items as MonthPlanEditableItem[]),
        legacyValidationIssues(planMonth, items),
        `planMonth=${planMonth} items=${items.length}`,
      )
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 问题分组与折叠 —— 与重构前完全一致
// ═══════════════════════════════════════════════════════════════════════════

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyIssueGroups(issues: ReturnType<typeof legacyValidationIssues>) {
  return [
    { key: 'plan', label: '计划信息', issues: issues.filter((issue) => issue.itemIndex === undefined) },
    { key: 'tasks', label: '任务内容', issues: issues.filter((issue) => issue.itemIndex !== undefined) },
  ].filter((group) => group.issues.length)
}

function legacyVisibleGroups(issues: ReturnType<typeof legacyValidationIssues>, expanded: boolean) {
  return legacyIssueGroups(issues).map((group) => ({
    ...group,
    total: group.issues.length,
    issues: expanded ? group.issues : group.issues.slice(0, group.key === 'plan' ? 3 : 4),
  }))
}

function legacyHiddenCount(issues: ReturnType<typeof legacyValidationIssues>) {
  return legacyIssueGroups(issues)
    .reduce((count, group) => count + Math.max(0, group.issues.length - (group.key === 'plan' ? 3 : 4)), 0)
}

test('parity · 问题分组与折叠计数与重构前完全一致', () => {
  for (const planMonth of MONTH_CASES) {
    for (const items of [[], ...ITEM_CASES.map((item) => [item]), [makeItem(), makeItem(), makeItem()]]) {
      const issues = legacyValidationIssues(planMonth, items)
      assert.deepEqual(groupMonthPlanIssues(issues), legacyIssueGroups(issues))
      for (const expanded of [false, true]) {
        assert.deepEqual(previewMonthPlanIssueGroups(issues, expanded), legacyVisibleGroups(issues, expanded))
      }
      assert.equal(countHiddenMonthPlanIssues(issues), legacyHiddenCount(issues))
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 准备度打分 —— 与重构前完全一致
// ═══════════════════════════════════════════════════════════════════════════

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyReadiness(planMonth: string, items: LegacyItem[], currentMonth: string): number {
  if (!items.length) return 0
  const planMonthReady = Boolean(planMonth && planMonth >= currentMonth)
  const planScore = planMonthReady ? 10 : 0
  const itemFieldCount = items.length * 7
  const completedItemFields = items.reduce((count, item) => count
    + (String(item.taskContent || '').trim() ? 1 : 0)
    + (String(item.deliverable || '').trim() ? 1 : 0)
    + (String(item.quality || '').trim() ? 1 : 0)
    + (String(item.estimatedStartDate || '').trim() ? 1 : 0)
    + (String(item.estimatedEndDate || '').trim() ? 1 : 0)
    + (Number(item.performanceWeight) > 0 ? 1 : 0)
    + (Number(item.estimatedValue) >= 0 ? 1 : 0), 0)
  const itemScore = itemFieldCount ? (completedItemFields / itemFieldCount) * 65 : 0
  const weightDistance = Math.min(Math.abs(legacyRegularWeightTotal(items) - 100), 100)
  const weightScore = legacyRegularWeightTotal(items) > 0 ? (1 - weightDistance / 100) * 25 : 0
  return Math.max(0, Math.min(100, Math.round(planScore + itemScore + weightScore)))
}

test('parity · 准备度分数与重构前完全一致', () => {
  for (const planMonth of MONTH_CASES) {
    for (const currentMonth of CURRENT_MONTH_CASES) {
      for (const items of [[], ...ITEM_CASES.map((item) => [item]), [makeItem(), makeItem()]]) {
        assert.equal(
          computeMonthPlanReadiness(planMonth, items as MonthPlanEditableItem[], currentMonth),
          legacyReadiness(planMonth, items, currentMonth),
          `planMonth=${planMonth} current=${currentMonth}`,
        )
      }
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. AI 优化字段对比 —— 与重构前完全一致
// ═══════════════════════════════════════════════════════════════════════════

const LEGACY_OPTIMIZABLE_FIELDS: Array<{ key: string; label: string }> = [
  { key: 'taskContent', label: '任务内容' },
  { key: 'deliverable', label: '交付物' }, { key: 'quality', label: '质量' },
  { key: 'estimatedStartDate', label: '预估开始日期' }, { key: 'estimatedEndDate', label: '预估结束日期' },
  { key: 'performanceWeight', label: '绩效权重' }, { key: 'estimatedValue', label: '预估价值（元）' },
]

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyNormalizeOptimizeValue(value: unknown, field: string): number | string {
  if (field === 'performanceWeight' || field === 'estimatedValue') {
    const number = Number(value)
    return Number.isFinite(number) ? number : 0
  }
  return String(value ?? '').trim()
}

function legacySameOptimizableValues(left: Record<string, unknown>, right: Record<string, unknown>): boolean {
  return LEGACY_OPTIMIZABLE_FIELDS.every(({ key }) => legacyNormalizeOptimizeValue(left[key], key) === legacyNormalizeOptimizeValue(right[key], key))
}

function legacyBuildOptimizePreview(source: Record<string, unknown>, suggestion: Record<string, unknown>) {
  return LEGACY_OPTIMIZABLE_FIELDS.map(({ key, label }) => ({
    key,
    label,
    originalValue: source[key],
    suggestedValue: suggestion[key],
    changed: legacyNormalizeOptimizeValue(source[key], key) !== legacyNormalizeOptimizeValue(suggestion[key], key),
  }))
}

function legacyDisplayOptimizeValue(value: unknown, field: string): string {
  if (field === 'performanceWeight') return value == null ? '未填写' : `${value}%`
  if (field === 'estimatedValue') return value == null ? '未填写' : `¥${Number(value ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  return String(value ?? '暂无')
}

test('parity · AI 优化字段归一化与对比与重构前完全一致', () => {
  const values: unknown[] = [0, 1, 40, '40', 'abc', null, undefined, '', '  ', '文本', 9999999999.99, NaN]
  const fields = LEGACY_OPTIMIZABLE_FIELDS.map((field) => field.key) as Array<Parameters<typeof normalizeOptimizeValue>[1]>

  for (const field of fields) {
    for (const value of values) {
      assert.equal(normalizeOptimizeValue(value, field), legacyNormalizeOptimizeValue(value, field))
    }
  }

  for (const left of ITEM_CASES) {
    for (const right of ITEM_CASES) {
      assert.equal(
        sameOptimizableValues(left as unknown as Record<string, unknown>, right as unknown as Record<string, unknown>),
        legacySameOptimizableValues(left as unknown as Record<string, unknown>, right as unknown as Record<string, unknown>),
      )
      assert.deepEqual(
        buildOptimizePreview(left as unknown as Record<string, unknown>, right as unknown as Record<string, unknown>),
        legacyBuildOptimizePreview(left as unknown as Record<string, unknown>, right as unknown as Record<string, unknown>),
      )
    }
  }
})

test('parity · 优化字段展示格式与重构前完全一致', () => {
  const values: unknown[] = [0, 1, 40, '40', null, undefined, '', 12345.6, 9999999999.99]
  const fields = LEGACY_OPTIMIZABLE_FIELDS.map((field) => field.key) as Array<Parameters<typeof displayOptimizeValue>[1]>

  for (const field of fields) {
    for (const value of values) {
      assert.equal(displayOptimizeValue(value, field), legacyDisplayOptimizeValue(value, field))
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 数值规整 / 任务名 / 空行判定 —— 与重构前完全一致
// ═══════════════════════════════════════════════════════════════════════════

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyEditableNumber(value: unknown): number | null {
  const number = Number(value ?? 0)
  return Number.isFinite(number) && number !== 0 ? number : null
}

function legacyNumberForSave(value: unknown): number {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number : 0
}

function legacyTaskNameForSave(item: Pick<LegacyItem, 'taskName' | 'taskContent'>): string {
  const existing = String(item.taskName || '').trim()
  if (existing) return existing
  return String(item.taskContent || '').replace(/\s+/g, ' ').trim().slice(0, 30)
}

function legacyIsEmptyRow(item: LegacyItem): boolean {
  return !String(item.taskName || '').trim()
    && !String(item.taskContent || '').trim()
    && !String(item.deliverable || '').trim()
    && !String(item.quality || '').trim()
    && item.performanceWeight == null
    && item.estimatedValue == null
    && !String(item.estimatedStartDate || '').trim()
    && !String(item.estimatedEndDate || '').trim()
}

test('parity · 数值规整与重构前完全一致', () => {
  const values: unknown[] = [0, 1, -1, 0.5, '40', 'abc', null, undefined, NaN, Infinity]
  for (const value of values) {
    assert.equal(editableNumber(value), legacyEditableNumber(value))
    assert.equal(numberForSave(value), legacyNumberForSave(value))
  }
})

test('parity · 任务名兜底与重构前完全一致', () => {
  for (const item of ITEM_CASES) {
    assert.equal(taskNameForSave(item), legacyTaskNameForSave(item))
  }
})

test('parity · 空行判定与重构前完全一致', () => {
  for (const item of ITEM_CASES) {
    assert.equal(isEmptyMonthPlanItem(item as MonthPlanEditableItem), legacyIsEmptyRow(item))
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 7. 自动保存状态 —— 与重构前完全一致
// ═══════════════════════════════════════════════════════════════════════════

/** ❌ 重构前的原始实现（逐字抄自 ``EmployeeMonthPlanEdit.vue``）。 */
function legacyAutoSaveState(status: string, editable: boolean, dirty: boolean) {
  if (!editable || status === 'readonly') return { text: '只读', type: 'info' as const }
  if (status === 'saving') return { text: '自动保存中', type: 'warning' as const }
  if (status === 'saved') return { text: '已自动保存', type: 'success' as const }
  if (status === 'error') return { text: '自动保存失败', type: 'danger' as const }
  return { text: dirty ? '有未保存修改' : '尚未修改', type: 'info' as const }
}

test('parity · 自动保存状态文案与重构前完全一致', () => {
  const statuses = ['idle', 'saving', 'saved', 'readonly', 'error']
  for (const status of statuses) {
    for (const editable of [true, false]) {
      for (const dirty of [true, false]) {
        assert.deepEqual(
          resolveAutoSaveState(status as 'idle', editable, dirty),
          legacyAutoSaveState(status, editable, dirty),
          `status=${status} editable=${editable} dirty=${dirty}`,
        )
      }
    }
  }
})

// ═══════════════════════════════════════════════════════════════════════════
// 8. 部门工作台 —— 与重构前完全一致
// ═══════════════════════════════════════════════════════════════════════════

const ORG_TREE: OrgScopeNode[] = [
  { id: 'A', children: [{ id: 'B', children: [{ id: 'D' }] }, { id: 'C' }] },
  { id: 'E' },
]

const EMPLOYEES: DepartmentEmployeeRow[] = [
  { ownerId: '1', employeeNo: 'E001', employeeName: '张三', orgId: 'A', monthPlanCount: 3, resultCount: 1 },
  { ownerId: '2', employeeNo: 'E002', employeeName: '李四', orgId: 'B', monthPlanCount: 0, resultCount: 5 },
  { ownerId: '3', employeeNo: 'E003', employeeName: '王五', orgId: 'D', monthPlanCount: 2, resultCount: 0 },
  { ownerId: '4', employeeNo: 'E004', employeeName: '赵六', orgId: 'E', monthPlanCount: 7, resultCount: 2 },
  { ownerId: '5', employeeNo: 'E005', employeeName: '赵六', orgId: 'ZZZ', monthPlanCount: 1, resultCount: 1 },
]

/** ❌ 重构前的原始实现（逐字抄自 ``DepartmentDashboard.vue``）。 */
function legacyFindOrganization(nodes: OrgScopeNode[], id: string): OrgScopeNode | undefined {
  for (const node of nodes) {
    if (node.id === id) return node
    const child = legacyFindOrganization(node.children || [], id)
    if (child) return child
  }
  return undefined
}

function legacyCollectOrganizationIds(node: OrgScopeNode, result: Set<string>) {
  result.add(node.id)
  for (const child of node.children || []) {
    legacyCollectOrganizationIds(child, result)
  }
}

function legacyOrganizationCounts(tree: OrgScopeNode[], employees: DepartmentEmployeeRow[]) {
  const directCounts = new Map<string, number>()
  employees.forEach((employee) => {
    directCounts.set(employee.orgId, (directCounts.get(employee.orgId) || 0) + 1)
  })
  const totals = new Map<string, number>()
  const countNode = (node: OrgScopeNode): number => {
    const total = (directCounts.get(node.id) || 0)
      + (node.children || []).reduce((sum, child) => sum + countNode(child), 0)
    totals.set(node.id, total)
    return total
  }
  tree.forEach(countNode)
  return totals
}

test('parity · 组织树查找与 ID 收集与重构前完全一致', () => {
  for (const id of ['A', 'B', 'C', 'D', 'E', 'NOPE']) {
    const actualNode = resolveSelectedOrgIds(ORG_TREE, id)
    const legacyNode = legacyFindOrganization(ORG_TREE, id)
    if (!legacyNode) {
      assert.notEqual(actualNode, null)
      assert.equal(actualNode!.size, 0)
      continue
    }
    const legacyIds = new Set<string>()
    legacyCollectOrganizationIds(legacyNode, legacyIds)
    assert.deepEqual([...(actualNode as Set<string>)].sort(), [...legacyIds].sort())
  }
})

test('parity · 组织人数统计与重构前完全一致', () => {
  const actual = countEmployeesByOrganization(ORG_TREE, EMPLOYEES)
  const legacy = legacyOrganizationCounts(ORG_TREE, EMPLOYEES)
  assert.equal(actual.size, legacy.size)
  for (const [key, value] of legacy) {
    assert.equal(actual.get(key), value, `组织 ${key}`)
  }
})

test('parity · 员工过滤与重构前完全一致', () => {
  const keywords = ['', '  ', '张', 'E002', 'e003', '赵六', '不存在']
  const orgIds = [null, 'A', 'B', 'E', 'NOPE']
  for (const orgId of orgIds) {
    for (const keyword of keywords) {
      // ❌ 重构前的原始实现
      const legacyIds = orgId == null
        ? null
        : (() => {
            const selected = legacyFindOrganization(ORG_TREE, orgId)
            if (!selected) return new Set<string>()
            const ids = new Set<string>()
            legacyCollectOrganizationIds(selected, ids)
            return ids
          })()
      const normalizedKeyword = keyword.trim().toLowerCase()
      const legacyVisible = EMPLOYEES.filter((employee) => {
        if (legacyIds && !legacyIds.has(employee.orgId)) return false
        if (!normalizedKeyword) return true
        return employee.employeeName.toLowerCase().includes(normalizedKeyword)
          || (employee.employeeNo || '').toLowerCase().includes(normalizedKeyword)
      })

      const actual = filterDepartmentEmployees(EMPLOYEES, {
        organizationIds: resolveSelectedOrgIds(ORG_TREE, orgId),
        keyword,
      })
      assert.deepEqual(actual, legacyVisible, `orgId=${orgId} keyword=${keyword}`)
    }
  }
})

test('parity · 项目组过滤 / 分页 / 汇总与重构前完全一致', () => {
  const packages = [{ groupId: 'A' }, { groupId: 'B' }, { groupId: 'E' }]
  for (const orgId of [null, 'A', 'NOPE']) {
    const legacyIds = orgId == null
      ? null
      : (() => {
          const selected = legacyFindOrganization(ORG_TREE, orgId)
          if (!selected) return new Set<string>()
          const ids = new Set<string>()
          legacyCollectOrganizationIds(selected, ids)
          return ids
        })()
    const legacyVisible = packages.filter((item) => (
      !legacyIds || legacyIds.has(item.groupId)
    ))
    assert.deepEqual(
      filterByOrganizationScope(packages, resolveSelectedOrgIds(ORG_TREE, orgId)),
      legacyVisible,
      `orgId=${orgId}`,
    )
  }

  for (const page of [1, 2, 3, 99]) {
    for (const pageSize of [1, 2, 10]) {
      const start = (page - 1) * pageSize
      assert.deepEqual(paginateRows(EMPLOYEES, page, pageSize), EMPLOYEES.slice(start, start + pageSize))
    }
  }

  const legacyMonthPlanCount = EMPLOYEES.reduce(
    (total, employee) => total + Number(employee.monthPlanCount || 0), 0,
  )
  assert.equal(sumEmployeeMetric(EMPLOYEES, 'monthPlanCount'), legacyMonthPlanCount)
})

test('parity · 项目组完成度与状态文案与重构前完全一致', () => {
  const statuses = ['OPEN', 'EMPTY', 'COLLECTING'] as const
  for (const approvedCount of [0, 1, 2, 3, 5, 10]) {
    for (const totalMemberCount of [0, 1, 2, 3, 5, 10]) {
      const legacyPercentage = totalMemberCount
        ? Math.round((approvedCount / totalMemberCount) * 100)
        : 0
      assert.equal(
        computePackagePercentage({ approvedCount, totalMemberCount }),
        legacyPercentage,
      )
    }
  }

  for (const status of statuses) {
    const legacyLabel = status === 'OPEN' ? '完整开放' : status === 'EMPTY' ? '暂无成员' : '等待集齐'
    const legacyType = status === 'OPEN' ? 'success' : status === 'EMPTY' ? 'info' : 'warning'
    assert.equal(resolvePackageStatusLabel(status), legacyLabel)
    assert.equal(resolvePackageStatusType(status), legacyType)
  }
})

test('parity · 待完成成员名单拼装与重构前完全一致', () => {
  const memberSets = [
    [],
    [{ employeeName: '张三', statusLabel: '待提交' }],
    [{ employeeName: '张三', statusLabel: '待提交' }, { employeeName: '李四', statusLabel: '待审批' }],
    Array.from({ length: 5 }, (_, index) => ({ employeeName: `员工${index}`, statusLabel: '待提交' })),
  ]
  for (const members of memberSets) {
    const legacyText = members.map((member) => `${member.employeeName}（${member.statusLabel}）`).join('、')
    assert.equal(formatPendingMembers(members), legacyText)

    const legacyNames = members
      .slice(0, 3)
      .map((member) => `${member.employeeName}（${member.statusLabel}）`)
      .join('、')
    const legacyPreview = members.length > 3 ? `${legacyNames} 等 ${members.length} 人` : legacyNames
    assert.equal(previewPendingMembers(members), legacyPreview)
  }
})

test('parity · 筛选状态判定与空列表文案与重构前完全一致', () => {
  const orgIds = [null, undefined, 'A']
  const keywords = ['', '   ', '张三']
  for (const selectedOrgId of orgIds) {
    for (const keyword of keywords) {
      for (const appliedKeyword of ['', '张三']) {
        const legacyHasFilter = selectedOrgId != null || Boolean(keyword.trim()) || Boolean(appliedKeyword)
        assert.equal(
          hasActiveFilter(selectedOrgId, keyword, appliedKeyword),
          legacyHasFilter,
        )
        const legacyEmptyText = legacyHasFilter
          ? '当前筛选条件下没有员工'
          : '当前部门及下级组织暂无员工，请先在员工管理中配置所属组织'
        assert.equal(resolveEmptyText(legacyHasFilter), legacyEmptyText)
      }
    }
  }
})
