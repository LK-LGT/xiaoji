import { http } from './http'

/** 同步批次状态。 */
export type SyncBatchStatus = 'pending' | 'running' | 'ok' | 'partial_failed' | 'failed'

/** 同步模式。 */
export type SyncMode = 'full' | 'incremental'

/** 错误分级：决定前端展示什么操作。 */
export type SyncErrorCategory = 'retryable' | 'config' | 'manual' | 'permanent' | 'unknown'

export interface SyncErrorCategoryInfo {
  category: SyncErrorCategory
  label: string
  autoRetryable: boolean
}

/** 同步批次。 */
export interface SyncBatch {
  id: string
  batchNo: string
  status: SyncBatchStatus
  mode: SyncMode
  triggerType: 'manual' | 'scheduled' | 'manual_retry'
  triggeredBy: string | null
  phase: string | null
  phaseDetail: string | null
  departmentsSynced: number
  departmentsSkipped: number
  departmentsFailed: number
  employeesSynced: number
  employeesSkipped: number
  employeesFailed: number
  errorMessage: string | null
  errorCategory: SyncErrorCategory | null
  errorCategoryInfo: SyncErrorCategoryInfo
  reconciliation: SyncReconciliation | null
  startedAt: string | null
  finishedAt: string | null
  durationMs: number | null
}

/** 数据质量指标。 */
export interface SyncDataQuality {
  total?: number
  employeeCount?: number
  departmentCount?: number
  no_department_count: number
  no_department_ids?: string[]
  no_manager_count: number
  no_manager_ids?: string[]
  no_login_account_count: number
  no_login_account_ids?: string[]
  disabled_count?: number
  exited_count?: number
  department_without_leader_count?: number
  cyclic_manager_count: number
  cyclic_manager_ids?: string[]
  remote_synced_count?: number
}

/** 对账结果。 */
export interface SyncReconciliation {
  remote_departments: number
  local_departments: number
  missing_local_departments: string[]
  stale_local_departments: string[]
  remote_employees: number
  local_employees: number
  missing_local_employees: string[]
  stale_local_employees: string[]
  active_remote_employees: number
  active_local_employees: number
  active_count_mismatch: boolean
  unbound_count: number
  conflict_count: number
  conflicts: Array<Record<string, unknown>>
  snapshot_complete: boolean
  failed_departments: string[]
  data_quality: SyncDataQuality
}

/** 单条同步失败记录。 */
export interface SyncErrorItem {
  id: string
  batchId: string
  resourceType: 'employee' | 'department'
  resourceId: string | null
  message: string
  errorCode: string | null
  errorCategory: SyncErrorCategory | null
  errorCategoryInfo: SyncErrorCategoryInfo
  retryCount: number
  status: 'pending' | 'retrying' | 'resolved' | 'failed' | 'dead_letter'
  nextRetryAt: string | null
  resolvedAt: string | null
  createdAt: string
}

/** 同步游标。 */
export interface SyncCursor {
  resourceType: string
  checkpointValue: string | null
  cursorValue: string | null
  pageToken: string | null
  snapshotComplete: boolean
  lastBatchId: string | null
  updatedAt: string | null
}

/** 同步链路状态。 */
export interface SyncStatus {
  configured: boolean
  appIdConfigured: boolean
  appSecretConfigured: boolean
  tenantIdConfigured: boolean
  scheduledEnabled: boolean
  scheduleIntervalSeconds: number
  nextScheduledAt: string | null
  running: boolean
  runningBatchId: string | null
  lastSuccessAt: string | null
  lastBatchId: string | null
  lastBatchStatus: SyncBatchStatus | null
  lastErrorMessage: string | null
  lastErrorCategory: SyncErrorCategory | null
  cursors: SyncCursor[]
}

/** 同步审计记录。 */
export interface SyncAudit {
  id: string
  batchId: string | null
  actorId: string | null
  action: string
  resourceType: string | null
  resourceId: string | null
  result: string
  detail: Record<string, unknown> | null
  createdAt: string
}

/** 未绑定身份的员工。 */
export interface UnboundIdentity {
  employee_id: string
  name: string
  feishu_user_id: string | null
  feishu_open_id: string | null
  feishu_union_id: string | null
}

/** 身份冲突。 */
export interface IdentityConflict {
  conflict: string
  identity_type?: string
  identity_value?: string
  employee_id?: string
}

export interface PageResult<T> {
  records: T[]
  total: number
  page: number
  pageSize: number
}

export interface TriggerSyncResult {
  batchId: string
  batchNo: string
  status: SyncBatchStatus
  mode: SyncMode
  created: boolean
}

/** 触发同步。同步在后台执行，返回批次 ID。 */
export const triggerOrganizationSync = (mode: SyncMode = 'full', idempotencyKey?: string) =>
  http.post<unknown, TriggerSyncResult>('/feishu/sync-organization', {
    mode,
    ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
  })

/** 查询同步链路状态。 */
export const getOrganizationSyncStatus = () =>
  http.get<unknown, SyncStatus>('/feishu/sync-organization/status')

/** 查询同步批次列表。 */
export const listSyncBatches = (params: {
  page?: number
  pageSize?: number
  status?: string
  mode?: string
} = {}) =>
  http.get<unknown, PageResult<SyncBatch>>('/feishu/sync-organization/batches', { params })

/** 查询单个批次详情（用于轮询进度）。 */
export const getSyncBatch = (batchId: string) =>
  http.get<unknown, SyncBatch>(`/feishu/sync-organization/batches/${batchId}`)

/** 查询同步失败记录。 */
export const listSyncErrors = (params: {
  page?: number
  pageSize?: number
  status?: string
  resourceType?: string
  errorCategory?: string
  batchId?: string
} = {}) =>
  http.get<unknown, PageResult<SyncErrorItem>>('/feishu/sync-organization/errors', { params })

/** 查询同步审计。 */
export const listSyncAudits = (params: {
  page?: number
  pageSize?: number
  action?: string
  result?: string
  actorId?: string
  batchId?: string
} = {}) =>
  http.get<unknown, PageResult<SyncAudit>>('/feishu/sync-organization/audits', { params })

/** 查询同步游标。 */
export const listSyncCursors = () =>
  http.get<unknown, SyncCursor[]>('/feishu/sync-organization/cursors')

/** 查询最近一次对账结果。 */
export const getLatestReconciliation = () =>
  http.get<unknown, {
    batchId: string
    batchNo: string
    finishedAt: string | null
    reconciliation: SyncReconciliation
  } | null>('/feishu/sync-organization/reconciliation/latest')

/** 重试失败记录。 */
export const retryFailedSyncItems = () =>
  http.post<unknown, {
    retried: number
    resolved: number
    failed: number
    dead_lettered: number
  }>('/feishu/sync-organization/retry')

/** 恢复超时批次。 */
export const recoverStaleSyncBatches = () =>
  http.post<unknown, { recovered: number }>('/feishu/sync-organization/recover-stale')

/** 查询未绑定身份的员工。 */
export const listUnboundIdentities = () =>
  http.get<unknown, UnboundIdentity[]>('/feishu/identities/unbound')

/** 查询身份冲突。 */
export const listIdentityConflicts = () =>
  http.get<unknown, IdentityConflict[]>('/feishu/identities/conflicts')

/** 人工绑定飞书身份。 */
export const bindIdentity = (data: {
  employee_id: string
  identity_type: 'user_id' | 'open_id' | 'union_id'
  identity_value: string
  reason: string
  allow_rebind?: boolean
}) => http.post<unknown, { id: string; employee_id: string }>('/feishu/identities/bind', data)

/** 解绑飞书身份。 */
export const unbindIdentity = (identityId: string, reason: string) =>
  http.post<unknown, { id: string }>(`/feishu/identities/${identityId}/unbind`, { reason })

/** 禁用员工账号。 */
export const disableIdentityAccount = (identityId: string, reason: string) =>
  http.post<unknown, { id: string }>(`/feishu/identities/${identityId}/disable-account`, { reason })

/** 设置登录账号。 */
export const setLoginAccount = (employeeId: string, loginAccount: string, reason: string) =>
  http.post<unknown, { id: string }>(
    `/feishu/identities/employee/${employeeId}/login-account`,
    { login_account: loginAccount, reason },
  )
