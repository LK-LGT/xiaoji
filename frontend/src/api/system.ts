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

export interface PermissionNode {
  /** 权限 ID（自增数字主键，按约定保持 number）。 */
  id: number
  /** 父权限 ID（自增数字主键，按约定保持 number）。 */
  parentId: number
  name: string
  code: string
  type: string
  path?: string
  status: number
  children: PermissionNode[]
}

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

export const getRoles = () => http.get<unknown, RoleItem[]>('/system/roles')
export const createRole = (data: Record<string, unknown>) => http.post<unknown, RoleItem>('/system/roles', data)
export const updateRole = (id: number, data: Record<string, unknown>) => http.put<unknown, RoleItem>(`/system/roles/${id}`, data)

export const getPermissions = () => http.get<unknown, PermissionNode[]>('/system/permissions')
export const getRolePermissions = (id: number) => http.get<unknown, number[]>(`/system/roles/${id}/permissions`)
export const saveRolePermissions = (id: number, permissionIds: number[]) =>
  http.put(`/system/roles/${id}/permissions`, { permissionIds })

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

export interface Role {
  id: string
  name: string
  description: string
  permissions: string[]
}

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

export const listRoles = () =>
  http.get<unknown, Role[]>('/system/roles')

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
