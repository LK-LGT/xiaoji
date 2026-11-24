/**
 * 🧩 **部门工作台的纯逻辑**（CI-5 阶段 2 第三轮 —— 从重页面抽纯函数）。
 *
 * ⚠️ **为什么要有这个文件**
 * ------------------------------------------------------------------
 * ``DepartmentDashboard.vue`` 有 **1375 行**，其中「组织层级过滤 / 员工分页 /
 * 项目组完成度」全是**纯计算**——不需要挂载组件就能测。
 *
 * ⚠️ **为什么不整页挂载测**
 *   1375 行的组件依赖组织树、路由查询、多个 API，
 *   挂载一旦失败**很难定位是哪个逻辑错了**。
 *
 * ⇒ **方案**：把纯逻辑抽到这里，组件改成「调用这些函数」。
 *
 * ⚠️ **边界**：这里只放**不依赖 Vue / DOM / 网络**的计算。
 *   需要 ``reactive``、``ref``、``watch`` 的留在组件里。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 类型（结构化子集，避免和 API 类型耦合过深）
// ═══════════════════════════════════════════════════════════════════════════

/** 组织树节点（与 ``OrgNode`` 对齐，但只保留过滤需要的字段）。 */
export interface OrgScopeNode {
  id: string
  children?: OrgScopeNode[]
}

/** 员工摘要（与 ``DepartmentEmployeeSummary`` 对齐的子集）。 */
export interface DepartmentEmployeeRow {
  ownerId: string
  employeeNo: string
  employeeName: string
  orgId: string
  monthPlanCount: number
  resultCount: number
}

/** 项目组成员的进度（与 ``ProjectMonthPlanMemberProgress`` 对齐）。 */
export interface PackageMemberProgress {
  employeeName: string
  statusLabel: string
}

/** 项目组月计划包（与 ``ProjectMonthPlanPackage`` 对齐）。 */
export interface MonthPlanPackageRow {
  groupId: string
  approvedCount: number
  totalMemberCount: number
  status: 'EMPTY' | 'COLLECTING' | 'OPEN'
  pendingMembers: PackageMemberProgress[]
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. ⭐ 组织树查找与 ID 收集
// ═══════════════════════════════════════════════════════════════════════════

/** 在组织树里递归查找某个节点。 */
export function findOrgNode(nodes: OrgScopeNode[], id: string): OrgScopeNode | undefined {
  for (const node of nodes) {
    if (node.id === id) return node
    const child = findOrgNode(node.children || [], id)
    if (child) return child
  }
  return undefined
}

/** 收集某个节点及其全部后代的 ID（含自身）。 */
export function collectOrgNodeIds(node: OrgScopeNode, result: Set<string> = new Set()): Set<string> {
  result.add(node.id)
  for (const child of node.children || []) {
    collectOrgNodeIds(child, result)
  }
  return result
}

/**
 * 选中组织对应的可见组织 ID 集合。
 *
 * ⚠️ **返回 ``null`` 表示「不按组织过滤」**（未选组织 = 看全部）。
 *   返回空 ``Set`` 表示「选了但不包含任何组织」⇒ 结果必然为空。
 */
export function resolveSelectedOrgIds(
  orgTree: OrgScopeNode[],
  selectedOrgId: string | null | undefined,
): Set<string> | null {
  if (selectedOrgId == null) return null
  const selected = findOrgNode(orgTree, selectedOrgId)
  if (!selected) return new Set<string>()
  return collectOrgNodeIds(selected)
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. ⭐ 组织人数统计（含下级汇总）
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 每个组织节点的员工数（**含下级组织汇总**）。
 *
 * ⚠️ **语义**：父组织的人数 = 直属人数 + 所有后代组织的直属人数。
 *   所以在树上从叶子往根累加。
 */
export function countEmployeesByOrganization(
  orgTree: OrgScopeNode[],
  employees: Array<Pick<DepartmentEmployeeRow, 'orgId'>>,
): Map<string, number> {
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
  orgTree.forEach(countNode)
  return totals
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. ⭐ 员工过滤与分页
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 按「组织范围 + 关键字」过滤员工。
 *
 * ⚠️ **关键字匹配姓名或工号**，大小写不敏感；两边都 ``trim``。
 * ⚠️ **组织范围为空集合时**（选了不存在的组织）⇒ 一条都不剩。
 */
export function filterDepartmentEmployees(
  employees: DepartmentEmployeeRow[],
  options: { organizationIds?: Set<string> | null; keyword?: string } = {},
): DepartmentEmployeeRow[] {
  const { organizationIds = null, keyword = '' } = options
  const normalizedKeyword = keyword.trim().toLowerCase()

  return employees.filter((employee) => {
    if (organizationIds && !organizationIds.has(employee.orgId)) return false
    if (!normalizedKeyword) return true
    return employee.employeeName.toLowerCase().includes(normalizedKeyword)
      || (employee.employeeNo || '').toLowerCase().includes(normalizedKeyword)
  })
}

/** 按组织范围过滤任意带 ``groupId`` 的集合。 */
export function filterByOrganizationScope<T extends { groupId: string }>(
  rows: T[],
  organizationIds: Set<string> | null,
): T[] {
  if (!organizationIds) return rows
  return rows.filter((item) => organizationIds.has(item.groupId))
}

/**
 * 员工列表分页。
 *
 * ⚠️ **页码越界时不回退** —— 组件依赖「空页」来提示用户改页码。
 */
export function paginateRows<T>(rows: T[], page: number, pageSize: number): T[] {
  const start = (page - 1) * pageSize
  return rows.slice(start, start + pageSize)
}

/** 汇总可见员工的月计划数。 */
export function sumEmployeeMetric(
  employees: DepartmentEmployeeRow[],
  metric: 'monthPlanCount' | 'resultCount',
): number {
  return employees.reduce((total, employee) => total + Number(employee[metric] || 0), 0)
}

/** 是否有下一页（可见人数超过一页容量）。 */
export function hasMorePages(total: number, pageSize: number): boolean {
  return total > pageSize
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. ⭐ 项目组完成度
// ═══════════════════════════════════════════════════════════════════════════

/**
 * 项目组的完成百分比。
 *
 * ⚠️ **成员数为 0 ⇒ 0%**（避免 ``NaN``）。
 */
export function computePackagePercentage(row: Pick<MonthPlanPackageRow, 'approvedCount' | 'totalMemberCount'>): number {
  if (!row.totalMemberCount) return 0
  return Math.round((row.approvedCount / row.totalMemberCount) * 100)
}

/** 项目组状态文案。 */
export function resolvePackageStatusLabel(status: MonthPlanPackageRow['status']): string {
  if (status === 'OPEN') return '完整开放'
  if (status === 'EMPTY') return '暂无成员'
  return '等待集齐'
}

/** 项目组状态的标签色板。 */
export function resolvePackageStatusType(
  status: MonthPlanPackageRow['status'],
): 'success' | 'warning' | 'info' {
  if (status === 'OPEN') return 'success'
  if (status === 'EMPTY') return 'info'
  return 'warning'
}

/** 待提交成员名单（完整）。 */
export function formatPendingMembers(members: PackageMemberProgress[]): string {
  return members.map((member) => `${member.employeeName}（${member.statusLabel}）`).join('、')
}

/** 待提交成员名单（预览前 3 个 + 「等 N 人」）。 */
export function previewPendingMembers(members: PackageMemberProgress[], limit = 3): string {
  const names = members
    .slice(0, limit)
    .map((member) => `${member.employeeName}（${member.statusLabel}）`)
    .join('、')
  return members.length > limit ? `${names} 等 ${members.length} 人` : names
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. ⭐ 展示格式化
// ═══════════════════════════════════════════════════════════════════════════

/** 项目组名首字（空则用「组」）。 */
export function projectGroupInitial(name?: string): string {
  return name?.trim().slice(0, 1) || '组'
}

/** 员工名末字（空则用「员」）。 */
export function employeeInitial(name?: string): string {
  return name?.trim().slice(-1) || '员'
}

/** 时间戳格式化（``2026-01-02T03:04:05`` ⇒ ``2026-01-02 03:04``）。 */
export function formatDateTime(value?: string): string {
  if (!value) return '暂无工作记录'
  return value.replace('T', ' ').slice(0, 16)
}

/** 金额格式化。 */
export function formatCurrency(value?: number): string {
  return `¥${Number(value || 0).toLocaleString('zh-CN')}`
}

/** 当前月份（``YYYY-MM``）。 */
export function currentMonthKey(date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. ⭐ 筛选状态
// ═══════════════════════════════════════════════════════════════════════════

/** 是否处于筛选状态（选了组织 **或** 填了关键字）。 */
export function hasActiveFilter(
  selectedOrgId: string | null | undefined,
  keyword: string,
  appliedKeyword = '',
): boolean {
  return selectedOrgId != null || Boolean(keyword.trim()) || Boolean(appliedKeyword)
}

/** 空列表提示文案（区分「筛没了」和「本来就没有」）。 */
export function resolveEmptyText(hasFilter: boolean): string {
  return hasFilter
    ? '当前筛选条件下没有员工'
    : '当前部门及下级组织暂无员工，请先在员工管理中配置所属组织'
}
