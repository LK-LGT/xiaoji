import assert from 'node:assert/strict'
import test from 'node:test'
import {
  collectOrgNodeIds,
  computePackagePercentage,
  countEmployeesByOrganization,
  currentMonthKey,
  employeeInitial,
  filterByOrganizationScope,
  filterDepartmentEmployees,
  findOrgNode,
  formatCurrency,
  formatDateTime,
  formatPendingMembers,
  hasActiveFilter,
  hasMorePages,
  paginateRows,
  previewPendingMembers,
  projectGroupInitial,
  resolveEmptyText,
  resolvePackageStatusLabel,
  resolvePackageStatusType,
  resolveSelectedOrgIds,
  sumEmployeeMetric,
  type DepartmentEmployeeRow,
  type OrgScopeNode,
} from '../src/utils/departmentDashboard.ts'

/**
 * 🧩 **部门工作台纯逻辑测试**（CI-5 阶段 2 第三轮）
 *
 * ⚠️ **为什么测纯函数而不是挂载组件**
 *   ``DepartmentDashboard.vue`` 有 1375 行，依赖组织树 / 路由 / 多个 API，
 *   挂载成本极高且失败难定位 ⇒ 把纯计算抽到 ``src/utils/departmentDashboard.ts``。
 *
 * ⚠️ **断言纪律**：每条断言都要**能区分对错**（不能恒真）。
 */

/** 组织树：一级 A（子 B、C），B 下有 D。 */
const ORG_TREE: OrgScopeNode[] = [
  {
    id: 'A',
    children: [
      { id: 'B', children: [{ id: 'D' }] },
      { id: 'C' },
    ],
  },
  { id: 'E' },
]

/** 构造一名员工。 */
function employee(
  overrides: Partial<DepartmentEmployeeRow> & { ownerId: string },
): DepartmentEmployeeRow {
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
// 1. 组织树查找与 ID 收集
// ═══════════════════════════════════════════════════════════════════════════

test('findOrgNode · 找到根节点', () => {
  assert.equal(findOrgNode(ORG_TREE, 'A')?.id, 'A')
})

test('findOrgNode · 找到二级节点', () => {
  assert.equal(findOrgNode(ORG_TREE, 'B')?.id, 'B')
})

test('findOrgNode · 找到三级节点', () => {
  assert.equal(findOrgNode(ORG_TREE, 'D')?.id, 'D')
})

test('findOrgNode · 找不到 ⇒ undefined', () => {
  assert.equal(findOrgNode(ORG_TREE, 'NOPE'), undefined)
})

test('findOrgNode · 空树 ⇒ undefined', () => {
  assert.equal(findOrgNode([], 'A'), undefined)
})

test('collectOrgNodeIds · 收集自身 + 全部后代', () => {
  const ids = collectOrgNodeIds(ORG_TREE[0])
  assert.deepEqual([...ids].sort(), ['A', 'B', 'C', 'D'])
})

test('collectOrgNodeIds · 叶子节点只有自己', () => {
  const ids = collectOrgNodeIds({ id: 'C' })
  assert.deepEqual([...ids], ['C'])
})

test('collectOrgNodeIds · 同一 Set 可复用（幂等）', () => {
  const ids = new Set<string>()
  collectOrgNodeIds({ id: 'C' }, ids)
  collectOrgNodeIds({ id: 'C' }, ids)
  assert.equal(ids.size, 1)
})

test('resolveSelectedOrgIds · 未选组织 ⇒ null（不过滤）', () => {
  assert.equal(resolveSelectedOrgIds(ORG_TREE, null), null)
  assert.equal(resolveSelectedOrgIds(ORG_TREE, undefined), null)
})

test('resolveSelectedOrgIds · 选中根节点 ⇒ 含全部后代', () => {
  const ids = resolveSelectedOrgIds(ORG_TREE, 'A')
  assert.deepEqual([...ids!].sort(), ['A', 'B', 'C', 'D'])
})

test('resolveSelectedOrgIds · 选中二级节点 ⇒ 只含其子树', () => {
  const ids = resolveSelectedOrgIds(ORG_TREE, 'B')
  assert.deepEqual([...ids!].sort(), ['B', 'D'])
})

test('resolveSelectedOrgIds · 选中不存在的组织 ⇒ 空集合（过滤后为空）', () => {
  const ids = resolveSelectedOrgIds(ORG_TREE, 'NOPE')
  assert.notEqual(ids, null)
  assert.equal(ids!.size, 0)
})

// ═══════════════════════════════════════════════════════════════════════════
// 2. 组织人数统计
// ═══════════════════════════════════════════════════════════════════════════

test('countEmployeesByOrganization · 父组织人数含下级汇总', () => {
  const totals = countEmployeesByOrganization(ORG_TREE, [
    employee({ ownerId: '1', orgId: 'A' }),
    employee({ ownerId: '2', orgId: 'B' }),
    employee({ ownerId: '3', orgId: 'D' }),
  ])
  assert.equal(totals.get('A'), 3)
  assert.equal(totals.get('B'), 2)
  assert.equal(totals.get('D'), 1)
  assert.equal(totals.get('C'), 0)
  assert.equal(totals.get('E'), 0)
})

test('countEmployeesByOrganization · 无员工 ⇒ 全部为 0', () => {
  const totals = countEmployeesByOrganization(ORG_TREE, [])
  assert.equal(totals.get('A'), 0)
  assert.equal(totals.get('D'), 0)
})

test('countEmployeesByOrganization · 员工归属树外组织 ⇒ 不计入任何节点', () => {
  const totals = countEmployeesByOrganization(ORG_TREE, [
    employee({ ownerId: '1', orgId: 'ZZZ' }),
  ])
  assert.equal(totals.get('A'), 0)
})

test('countEmployeesByOrganization · 同组织多人累加', () => {
  const totals = countEmployeesByOrganization(ORG_TREE, [
    employee({ ownerId: '1', orgId: 'D' }),
    employee({ ownerId: '2', orgId: 'D' }),
    employee({ ownerId: '3', orgId: 'D' }),
  ])
  assert.equal(totals.get('D'), 3)
  assert.equal(totals.get('A'), 3)
})

// ═══════════════════════════════════════════════════════════════════════════
// 3. 员工过滤与分页
// ═══════════════════════════════════════════════════════════════════════════

const EMPLOYEES: DepartmentEmployeeRow[] = [
  employee({ ownerId: '1', employeeName: '张三', employeeNo: 'E001', orgId: 'A' }),
  employee({ ownerId: '2', employeeName: '李四', employeeNo: 'E002', orgId: 'B' }),
  employee({ ownerId: '3', employeeName: '王五', employeeNo: 'E003', orgId: 'D' }),
  employee({ ownerId: '4', employeeName: '赵六', employeeNo: 'E004', orgId: 'E' }),
]

test('filterDepartmentEmployees · 无组织无关键字 ⇒ 全部保留', () => {
  assert.equal(filterDepartmentEmployees(EMPLOYEES).length, 4)
})

test('filterDepartmentEmployees · 按组织范围过滤（含下级）', () => {
  const ids = resolveSelectedOrgIds(ORG_TREE, 'A')
  const result = filterDepartmentEmployees(EMPLOYEES, { organizationIds: ids })
  assert.deepEqual(result.map((item) => item.ownerId), ['1', '2', '3'])
})

test('filterDepartmentEmployees · 空组织集合 ⇒ 一条都不剩', () => {
  const result = filterDepartmentEmployees(EMPLOYEES, { organizationIds: new Set() })
  assert.equal(result.length, 0)
})

test('filterDepartmentEmployees · 关键字匹配姓名', () => {
  const result = filterDepartmentEmployees(EMPLOYEES, { keyword: '李' })
  assert.deepEqual(result.map((item) => item.ownerId), ['2'])
})

test('filterDepartmentEmployees · 关键字匹配工号', () => {
  const result = filterDepartmentEmployees(EMPLOYEES, { keyword: 'E003' })
  assert.deepEqual(result.map((item) => item.ownerId), ['3'])
})

test('filterDepartmentEmployees · 关键字大小写不敏感', () => {
  const result = filterDepartmentEmployees(EMPLOYEES, { keyword: 'e002' })
  assert.deepEqual(result.map((item) => item.ownerId), ['2'])
})

test('filterDepartmentEmployees · 关键字首尾空格被忽略', () => {
  const result = filterDepartmentEmployees(EMPLOYEES, { keyword: '  张三  ' })
  assert.deepEqual(result.map((item) => item.ownerId), ['1'])
})

test('filterDepartmentEmployees · 关键字 + 组织范围同时生效（AND）', () => {
  const ids = resolveSelectedOrgIds(ORG_TREE, 'A')
  const result = filterDepartmentEmployees(EMPLOYEES, { organizationIds: ids, keyword: '王' })
  assert.deepEqual(result.map((item) => item.ownerId), ['3'])
})

test('filterDepartmentEmployees · 组织范围外的关键字命中 ⇒ 被排除', () => {
  const ids = resolveSelectedOrgIds(ORG_TREE, 'A')
  const result = filterDepartmentEmployees(EMPLOYEES, { organizationIds: ids, keyword: '赵' })
  assert.equal(result.length, 0)
})

test('filterDepartmentEmployees · 空白关键字 ⇒ 不过滤', () => {
  const result = filterDepartmentEmployees(EMPLOYEES, { keyword: '   ' })
  assert.equal(result.length, 4)
})

test('filterByOrganizationScope · 无范围 ⇒ 原样返回', () => {
  const rows = [{ groupId: 'A' }, { groupId: 'B' }]
  assert.equal(filterByOrganizationScope(rows, null).length, 2)
})

test('filterByOrganizationScope · 有范围 ⇒ 只保留命中项', () => {
  const rows = [{ groupId: 'A' }, { groupId: 'B' }]
  const result = filterByOrganizationScope(rows, new Set(['A']))
  assert.deepEqual(result.map((item) => item.groupId), ['A'])
})

test('paginateRows · 第一页取前 N 条', () => {
  const rows = [1, 2, 3, 4, 5]
  assert.deepEqual(paginateRows(rows, 1, 2), [1, 2])
})

test('paginateRows · 第二页取接下来的 N 条', () => {
  const rows = [1, 2, 3, 4, 5]
  assert.deepEqual(paginateRows(rows, 2, 2), [3, 4])
})

test('paginateRows · 末页不足 N 条 ⇒ 只返回剩余', () => {
  const rows = [1, 2, 3, 4, 5]
  assert.deepEqual(paginateRows(rows, 3, 2), [5])
})

test('paginateRows · 页码越界 ⇒ 空数组（不回退到第一页）', () => {
  const rows = [1, 2, 3]
  assert.deepEqual(paginateRows(rows, 99, 2), [])
})

test('sumEmployeeMetric · 汇总月计划数', () => {
  const rows = [
    employee({ ownerId: '1', monthPlanCount: 3 }),
    employee({ ownerId: '2', monthPlanCount: 4 }),
  ]
  assert.equal(sumEmployeeMetric(rows, 'monthPlanCount'), 7)
})

test('sumEmployeeMetric · 汇总结果数', () => {
  const rows = [
    employee({ ownerId: '1', resultCount: 2 }),
    employee({ ownerId: '2', resultCount: 5 }),
  ]
  assert.equal(sumEmployeeMetric(rows, 'resultCount'), 7)
})

test('sumEmployeeMetric · 空数组 ⇒ 0', () => {
  assert.equal(sumEmployeeMetric([], 'monthPlanCount'), 0)
})

test('sumEmployeeMetric · undefined 计数按 0 计', () => {
  const rows = [
    employee({ ownerId: '1', monthPlanCount: undefined as unknown as number }),
  ]
  assert.equal(sumEmployeeMetric(rows, 'monthPlanCount'), 0)
})

test('hasMorePages · 人数超过页容量 ⇒ true', () => {
  assert.equal(hasMorePages(11, 10), true)
})

test('hasMorePages · 人数正好等于页容量 ⇒ false', () => {
  assert.equal(hasMorePages(10, 10), false)
})

test('hasMorePages · 人数少于页容量 ⇒ false', () => {
  assert.equal(hasMorePages(3, 10), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 4. 项目组完成度
// ═══════════════════════════════════════════════════════════════════════════

test('computePackagePercentage · 全部通过 ⇒ 100', () => {
  assert.equal(computePackagePercentage({ approvedCount: 5, totalMemberCount: 5 }), 100)
})

test('computePackagePercentage · 一半通过 ⇒ 50', () => {
  assert.equal(computePackagePercentage({ approvedCount: 3, totalMemberCount: 6 }), 50)
})

test('computePackagePercentage · 四舍五入到整数', () => {
  assert.equal(computePackagePercentage({ approvedCount: 2, totalMemberCount: 3 }), 67)
})

test('computePackagePercentage · 成员数为 0 ⇒ 0（不返回 NaN）', () => {
  assert.equal(computePackagePercentage({ approvedCount: 0, totalMemberCount: 0 }), 0)
})

test('computePackagePercentage · 无人通过 ⇒ 0', () => {
  assert.equal(computePackagePercentage({ approvedCount: 0, totalMemberCount: 4 }), 0)
})

test('resolvePackageStatusLabel · OPEN ⇒ 完整开放', () => {
  assert.equal(resolvePackageStatusLabel('OPEN'), '完整开放')
})

test('resolvePackageStatusLabel · EMPTY ⇒ 暂无成员', () => {
  assert.equal(resolvePackageStatusLabel('EMPTY'), '暂无成员')
})

test('resolvePackageStatusLabel · COLLECTING ⇒ 等待集齐', () => {
  assert.equal(resolvePackageStatusLabel('COLLECTING'), '等待集齐')
})

test('resolvePackageStatusType · OPEN ⇒ success', () => {
  assert.equal(resolvePackageStatusType('OPEN'), 'success')
})

test('resolvePackageStatusType · EMPTY ⇒ info', () => {
  assert.equal(resolvePackageStatusType('EMPTY'), 'info')
})

test('resolvePackageStatusType · COLLECTING ⇒ warning', () => {
  assert.equal(resolvePackageStatusType('COLLECTING'), 'warning')
})

test('formatPendingMembers · 完整名单用「、」连接', () => {
  const members = [
    { employeeName: '张三', statusLabel: '待提交' },
    { employeeName: '李四', statusLabel: '待审批' },
  ]
  assert.equal(formatPendingMembers(members), '张三（待提交）、李四（待审批）')
})

test('formatPendingMembers · 空名单 ⇒ 空串', () => {
  assert.equal(formatPendingMembers([]), '')
})

test('previewPendingMembers · 3 人以内全部显示', () => {
  const members = [
    { employeeName: '张三', statusLabel: '待提交' },
    { employeeName: '李四', statusLabel: '待提交' },
  ]
  assert.equal(previewPendingMembers(members), '张三（待提交）、李四（待提交）')
})

test('previewPendingMembers · 超过 3 人 ⇒ 截断并提示总人数', () => {
  const members = Array.from({ length: 5 }, (_, index) => ({
    employeeName: `员工${index}`,
    statusLabel: '待提交',
  }))
  const preview = previewPendingMembers(members)
  assert.ok(preview.includes('员工0（待提交）'))
  assert.ok(preview.includes('员工2（待提交）'))
  assert.equal(preview.includes('员工3'), false)
  assert.ok(preview.endsWith('等 5 人'))
})

test('previewPendingMembers · 恰好 3 人 ⇒ 不加「等 N 人」', () => {
  const members = Array.from({ length: 3 }, (_, index) => ({
    employeeName: `员工${index}`,
    statusLabel: '待提交',
  }))
  assert.equal(previewPendingMembers(members).includes('等'), false)
})

// ═══════════════════════════════════════════════════════════════════════════
// 5. 展示格式化
// ═══════════════════════════════════════════════════════════════════════════

test('projectGroupInitial · 取首字', () => {
  assert.equal(projectGroupInitial('剪辑组'), '剪')
})

test('projectGroupInitial · 空名 ⇒ 组', () => {
  assert.equal(projectGroupInitial(''), '组')
  assert.equal(projectGroupInitial(undefined), '组')
})

test('projectGroupInitial · 空白名 ⇒ 组', () => {
  assert.equal(projectGroupInitial('   '), '组')
})

test('employeeInitial · 取末字', () => {
  assert.equal(employeeInitial('张三'), '三')
})

test('employeeInitial · 空名 ⇒ 员', () => {
  assert.equal(employeeInitial(''), '员')
  assert.equal(employeeInitial(undefined), '员')
})

test('formatDateTime · ISO 时间戳去掉 T 并截到分钟', () => {
  assert.equal(formatDateTime('2026-11-24T18:25:30'), '2026-11-24 18:25')
})

test('formatDateTime · 空值 ⇒ 暂无工作记录', () => {
  assert.equal(formatDateTime(undefined), '暂无工作记录')
  assert.equal(formatDateTime(''), '暂无工作记录')
})

test('formatCurrency · 千分位 + 货币符号', () => {
  assert.equal(formatCurrency(1234567), '¥1,234,567')
})

test('formatCurrency · undefined ⇒ ¥0', () => {
  assert.equal(formatCurrency(undefined), '¥0')
})

test('currentMonthKey · 输出 YYYY-MM', () => {
  assert.match(currentMonthKey(new Date(2026, 10, 24)), /^2026-11$/)
})

test('currentMonthKey · 一月补零', () => {
  assert.equal(currentMonthKey(new Date(2026, 0, 15)), '2026-01')
})

test('currentMonthKey · 十二月不溢出', () => {
  assert.equal(currentMonthKey(new Date(2026, 11, 31)), '2026-12')
})

// ═══════════════════════════════════════════════════════════════════════════
// 6. 筛选状态
// ═══════════════════════════════════════════════════════════════════════════

test('hasActiveFilter · 什么都没选 ⇒ false', () => {
  assert.equal(hasActiveFilter(null, '', ''), false)
})

test('hasActiveFilter · 选了组织 ⇒ true', () => {
  assert.equal(hasActiveFilter('A', '', ''), true)
})

test('hasActiveFilter · 填了关键字 ⇒ true', () => {
  assert.equal(hasActiveFilter(null, '张三', ''), true)
})

test('hasActiveFilter · 只有空白关键字 ⇒ false', () => {
  assert.equal(hasActiveFilter(null, '   ', ''), false)
})

test('hasActiveFilter · 只填了已应用的 ⇒ true', () => {
  assert.equal(hasActiveFilter(null, '', '张三'), true)
})

test('resolveEmptyText · 筛选中 ⇒ 筛选文案', () => {
  assert.equal(resolveEmptyText(true), '当前筛选条件下没有员工')
})

test('resolveEmptyText · 未筛选 ⇒ 配置提示', () => {
  assert.equal(
    resolveEmptyText(false),
    '当前部门及下级组织暂无员工，请先在员工管理中配置所属组织',
  )
})
