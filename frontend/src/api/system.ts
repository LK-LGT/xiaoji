import { http } from './http'
import type { WeekPlanDetail } from './weekPlan'

export interface PageResult<T> {
  records: T[]
  total: number
  pageNo: number
  pageSize: number
}

export interface AuditItem {
  id: number
  userId?: number
  username?: string
  operatorName?: string
  action: string
  actionLabel?: string
  targetType?: string
  targetTypeLabel?: string
  targetId?: number
  targetSummary?: string
  targetRoute?: string
  summary?: string
  detailItems?: AuditDetailItem[]
  result: string
  clientIp?: string
  detail?: string
  createdAt: string
}

export interface AuditDetailItem {
  label: string
  value?: string
  beforeValue?: string
  afterValue?: string
}

export interface AuditFilterOption {
  value: string
  label: string
  group?: string
}

export interface AuditOptions {
  actions: AuditFilterOption[]
  targetTypes: AuditFilterOption[]
}

export interface DashboardData {
  departmentCount: number
  employeeCount: number
  roleCount: number
  auditCount: number
  risks: Array<{ code: string; title: string; count: number; level: string; route: string }>
  recentAudits: AuditItem[]
}

export interface HealthStatus {
  status: string
  service: string
  time: string
}

/**
 * 根节点哨兵值。
 *
 * 飞书根部门的 parent_id 就是字符串 "0"，全链路必须统一用字符串。
 * ⚠️ 不能写成数字 0：前端若用 `===` 严格比较，number 与 string 永不相等，
 * 会导致树节点选中 / 父级判断静默失效。
 */
export const ROOT_DEPARTMENT_ID = '0'

export interface DeptNode {
  /** 飞书部门 ID（字符串）。 */
  id: string
  parentId: string
  name: string
  code: string
  orgType: 'DEPARTMENT' | 'GROUP' | 'PROJECT_GROUP'
  leaderUserId?: string
  leaderName?: string
  sortNo: number
  status: number
  employeeCount: number
  children: DeptNode[]
}

export interface RoleItem {
  /** 角色 ID（自增数字主键，后端 role_id 为 int）。 */
  id: number
  name: string
  code: string
  description?: string
  dataScope: string
  builtIn: boolean
  status: number
  userCount: number
  permissionCount: number
}

export interface OptionItem { id: string; label: string; secondary?: string; status: number }
export interface SystemOptions { departments: DeptNode[]; leaders: OptionItem[]; roles: RoleItem[] }

export interface RegistrationResult {
  userId: string
  username: string
  employeeNo: string
  initialPassword: string
  realName: string
  positionName?: string
  departmentName: string
  directLeaderName?: string
}

export interface UserItem {
  /** 员工 ID（飞书 user_id，字符串）。 */
  id: string
  username: string
  employeeNo: string
  realName: string
  positionName?: string
  mobile: string
  email?: string
  deptId?: string
  departmentName: string
  groupId?: string
  directLeaderId?: string
  directLeaderName: string
  roleIds: string[]
  roleNames: string[]
  /** 兼容字段：1 启用 / 0 禁用（等于 accountStatus 的数字化）。 */
  status: number
  /** 账号状态：enabled / disabled。 */
  accountStatus: 'enabled' | 'disabled'
  /** 在职状态：active / inactive / exited。 */
  employmentStatus: 'active' | 'inactive' | 'exited'
  forceChangePassword: boolean
  lastLoginAt?: string
  createdAt: string
}

// ⚠️ **``PermissionNode`` 已随「权限管理」页下线一起移除**（2026-11-26）
//    那个页面对应后端返回硬编码假数据，且没有真实 ``permission`` 表。

export interface WorkdayRule {
  id: number
  ruleDate: string
  ruleType: string
  forceReport: boolean
  description?: string
  status: number
  versionNo: number
  updatedAt?: string
}

export interface WeekPlanStandardSupplementRow {
  itemId: number
  weekPlanId: number
  title: string
  weekStart: string
  weekEnd: string
  versionNo: number
  /** 归属人 ID（飞书 user_id，string）。 */
  ownerUserId: string
  employeeName: string
  /** 部门 ID（飞书 department_id，string）。 */
  deptId?: string
  departmentName: string
  content: string
  deliverable?: string
  quality?: string
  approveAt?: string
}

export interface WeekPlanStandardSupplementAction {
  id: number
  status: string
  versionNo: number
  message: string
}

export const getHealth = () => http.get<unknown, HealthStatus>('/health', { timeout: 5000 })
export const getSystemDashboard = () => http.get<unknown, DashboardData>('/system/dashboard')
export const getSystemOptions = () => http.get<unknown, SystemOptions>('/system/options')
export const registerEmployee = (data: { realName: string; mobile: string; deptId: string; directLeaderId?: string; roleId: number; positionName?: string }) =>
  http.post<unknown, RegistrationResult>('/system/users/register', data)
export const getUsers = (params: Record<string, unknown>) => http.get<unknown, PageResult<UserItem>>('/system/users', { params })
export const updateUser = (id: string, data: Record<string, unknown>) => http.put<unknown, UserItem>(`/system/users/${id}`, data)
export const changeUserStatus = (id: string, status: number) => http.post(`/system/users/${id}/status`, { status })
export const resetUserPassword = (id: string) => http.post<unknown, { initialPassword: string }>(`/system/users/${id}/reset-password`)
export const importUsers = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return http.post<unknown, { total: number; success: number; failed: number; errors: string[] }>('/system/users/import', form)
}
export const downloadUserImportTemplate = () => http.get<unknown, Blob>('/system/users/import-template', { responseType: 'blob' })
export const exportUsers = () => http.get<unknown, Blob>('/system/users/export', { responseType: 'blob' })

export const getDepartments = () => http.get<unknown, DeptNode[]>('/system/departments')
export const createDepartment = (data: Record<string, unknown>) => http.post<unknown, DeptNode>('/system/departments', data)
export const updateDepartment = (id: string, data: Record<string, unknown>) => http.put<unknown, DeptNode>(`/system/departments/${id}`, data)
export const deleteDepartment = (id: string) => http.delete(`/system/departments/${id}`)

// ⚠️⚠️ **角色/权限的读写接口已下线**（2026-11-26）
//
// ❌ **为什么删**
//   后端 ``/system/roles``、``/system/permissions`` 返回的是**硬编码假数据**，
//   库里**没有 `role` 表** ⇒ 写操作是 no-op（存不进去）。
//   对应的两个前端页面（``RoleManagement.vue`` / ``PermissionManagement.vue``）
//   已一并删除 ⇒ 这些 API 无人调用 ⇒ 删掉避免误用。
//
// ⚠️ **后端端点仍保留** —— 契约测试覆盖了它们，且老链接要能打开。
//
// ⚠️ **注意：``RoleItem`` / ``SystemOptions.roles`` 仍在使用** ——
//    员工注册 / 员工管理页的「角色」下拉框用的是
//    ``GET /system/options``，与这里下线的 ``/system/roles`` **不是同一个东西**。
//
// ⚠️⚠️ **但 ``/system/options`` 的 ``code`` 字段也是历史遗留**（2026-11-26）
//    ``RoleItem.code`` 返回的是**大写自造码**（``ADMIN`` / ``EMPLOYEE`` /
//    ``DEPARTMENT_LEADER`` / ``LEADER``），而后端真实角色码是小写
//    （``admin`` / ``employee`` / ``department_leader`` / ``manager``）。
//    ⚠️ **真正与后端角色体系对齐的是 ``RoleItem.id``**
//    （值就是 ``admin`` / ``employee`` / ``department_leader`` / ``leader``）。
//    ⇒ 用 ``code`` 做角色判断的地方（如 ``EmployeeRegister.vue`` 找默认角色）
//      是在匹配**自造码**，与「真实角色码」是两套东西。
//    ⇒ 本轮**未改** ``/system/options`` 的返回结构 —— 员工注册是真实业务，
//      改 ``code`` 会连带改契约测试与注册流程的默认角色匹配；
//      留待「角色码口径统一」那一轮专门处理。
//    ⇒ 详见 ``docs/11-待完善清单.md`` 中的角色码统一条目。

export const getWorkdayRules = (params: Record<string, unknown>) =>
  http.get<unknown, PageResult<WorkdayRule>>('/system/workday-rules', { params })
export const createWorkdayRule = (data: Record<string, unknown>) => http.post<unknown, WorkdayRule>('/system/workday-rules', data)
export const updateWorkdayRule = (id: number, data: Record<string, unknown>) => http.put<unknown, WorkdayRule>(`/system/workday-rules/${id}`, data)
export const changeWorkdayStatus = (id: number, status: number) => http.post(`/system/workday-rules/${id}/status`, { status })

export const getWeekPlanStandardSupplements = (params: Record<string, unknown>) =>
  http.get<unknown, PageResult<WeekPlanStandardSupplementRow>>('/system/week-plan-standard-supplements', { params })
export const getWeekPlanStandardSupplementDetail = (weekPlanId: number) =>
  http.get<unknown, WeekPlanDetail>(`/system/week-plan-standard-supplements/${weekPlanId}`)
export const supplementWeekPlanStandard = (itemId: number, data: { versionNo: number; deliverable: string; quality: string }) =>
  http.put<unknown, WeekPlanStandardSupplementAction>(`/system/week-plan-standard-supplements/${itemId}`, data)

export const getAudits = (params: Record<string, unknown>) => http.get<unknown, PageResult<AuditItem>>('/system/audits', { params })
export const getAuditOptions = () => http.get<unknown, AuditOptions>('/system/audits/options')
export const exportAudits = (params: Record<string, unknown>) => http.get<unknown, Blob>('/system/audits/export', { params, responseType: 'blob' })

// 员工管理 API
export interface Employee {
  userId: string
  username: string
  realName: string
  email: string
  mobile: string
  departmentId: string
  departmentName: string
  position: string
  manager_id?: string
  status: 'active' | 'inactive'
  joinDate: string
  roles: string[]
}

export interface EmployeeListResult {
  list: Employee[]
  total: number
  page: number
  pageSize: number
}

export interface Department {
  id: string
  name: string
  manager_id?: string
  parentId?: string
}

// ⚠️ **``Role`` 接口类型已随「角色管理」页下线一起移除**（2026-11-26）

export const listEmployees = (skip: number = 0, limit: number = 20, filters?: Record<string, unknown>) =>
  http.get<unknown, EmployeeListResult>('/system/employees', { params: { skip, limit, ...filters } })

export const getEmployee = (userId: string) =>
  http.get<unknown, Employee>(`/system/employees/${userId}`)

export const createEmployee = (data: Partial<Employee>) =>
  http.post<unknown, Employee>('/system/employees', data)

export const updateEmployee = (userId: string, data: Partial<Employee>) =>
  http.put<unknown, Employee>(`/system/employees/${userId}`, data)

export const deleteEmployee = (userId: string) =>
  http.delete(`/system/employees/${userId}`)

export const listDepartments = () =>
  http.get<unknown, Department[]>('/system/departments')

// ⚠️ **``listRoles`` 已随「角色管理」页下线一起移除**（2026-11-26）

export function flattenDepartments(nodes: DeptNode[], depth = 0): Array<DeptNode & { depth: number }> {
  return nodes.flatMap((node) => [{ ...node, depth }, ...flattenDepartments(node.children || [], depth + 1)])
}
    
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
