/**
 * P0 / P1 前端 API 客户端。
 *
 * ⚠️ 路径约定：`http` 的 baseURL 是 `/api`，Vite 代理把 `/api/xxx`
 * 重写成 `/v1/xxx`。所以这里写 `/performance/...` 而不是 `/v1/performance/...`。
 *
 * ⚠️ 语义提醒（贯穿本文件）：
 *   1. **分布质检只提示不改分** —— 前端不得提供任何"一键改分"入口。
 *   2. **核验报告是中立材料，不作裁决** —— 前端不得显示"申诉成立/不成立"。
 *   3. **执行类指令必须两阶段确认** —— prepare 只出计划，confirm 才产生副作用。
 *   4. **互评意见在提交后不可改**（后端幂等返回已提交结果）。
 *   5. **代理授权到期自动失效** —— 前端只做展示与提前撤销，不做"续期绕过"。
 */
import { http } from './http'

// ── P0-1 互评（Peer Review） ───────────────────────────────────────────────

export interface PeerReviewItem {
  id: string
  record_id: string
  reviewee_id: string
  reviewer_id: string
  status: string
  score: number | null
  comment: string | null
  due_at: string | null
  submitted_at: string | null
}

export interface PeerReviewList {
  record_id: string
  total: number
  submitted: number
  pending: number
  complete: boolean
  items: PeerReviewItem[]
  /** 当前登录者是否可以代填互评（主管/HR 视角）。 */
  can_review: boolean
}

export interface PeerProgressRow {
  record_id: string
  total: number
  submitted: number
  pending: number
  overdue: number
}

export const peerReviewApi = {
  /** 为某条考核记录分派互评人。 */
  assign: (recordId: string, payload: { reviewer_ids: string[]; due_at?: string | null }) =>
    http.post<unknown, unknown>(`/performance/scheme-records/${recordId}/peers`, payload),
  /** 查询某条考核记录的互评清单（当前登录者视角：分数可能对普通员工隐藏）。 */
  list: (recordId: string) => http.get<unknown, PeerReviewList>(`/performance/scheme-records/${recordId}/peers`),
  /** 互评人提交互评（分数 + 意见，至少 5 字）。 */
  submit: (peerId: string, payload: { score: number | null; comment: string }) =>
    http.put<unknown, PeerReviewItem>(`/performance/peers/${peerId}`, payload),
  /** 主管回收进度：按被评人聚合。 */
  progress: (taskId: string) => http.get<unknown, PeerProgressRow[]>(`/performance/scheme-tasks/${taskId}/peer-progress`),
  /** HR 对逾期互评批量催办。 */
  remind: (taskId: string) => http.post<unknown, { reminded: number }>(`/performance/scheme-tasks/${taskId}/peer-remind`),
}

// ── P0-2 分布质检（Distribution QC） ────────────────────────────────────────

export interface DistributionGroup {
  department_id: string
  position: string
  rule_group: string
  rating_counts: Record<string, number>
  count: number
  mean: number | null
  median: number | null
  stddev: number | null
  cv: number | null
  small_sample?: boolean
  rating_distribution: Record<string, { count: number; percentage: number | null }>
  outliers?: DistributionOutlier[]
  distribution_gaps?: { level: string; target: number; actual: number; gap: number }[]
}

export interface DistributionAlert {
  department_id: string
  position: string
  rule_group: string
  type: string
  severity: string
  message: string
  metrics?: Record<string, unknown>
}

export interface DistributionOutlier {
  record_id?: string
  employee_id?: string
  employee_name?: string
  total: number
  z: number
  direction?: string
}

export interface DistributionReport {
  period: string
  groups: DistributionGroup[]
  alerts: DistributionAlert[]
  records?: Record<string, unknown>[]
  captured_at: string | null
  limitations: string[]
}

export const distributionQcApi = {
  /**
   * 周期分数分布质检。
   * @param period 紧凑周期，如 `202611`
   * @param departmentIds 逗号分隔部门 ID；留空表示全部（后端会按权限收窄）
   * @param record 是否写入 AI 复核审计记录
   */
  distribution: (period: string, departmentIds = '', record = false) =>
    http.get<unknown, DistributionReport>('/ai/reviews/distribution', {
      params: { period, departmentIds, record },
    }),
}

// ── P0-3 智能核验（Verification Report） ────────────────────────────────────

export interface VerificationFact {
  type: string
  source?: string
  rule_version?: string
  original?: number
  recomputed?: number
  [k: string]: unknown
}

export interface VerificationHistoryItem {
  version: number
  total: number
  source: string
  created_at: string
}

export interface VerificationReportContent {
  scope: string
  snapshot_hash: string
  engine: string
  ai_status: string
  facts: VerificationFact[]
  differences: { type: string; delta: number }[]
  missing: string[]
  history: VerificationHistoryItem[]
  limitations: string[]
  recomputed?: Record<string, unknown>
}

export interface VerificationReportRow {
  id: string
  version: number
  /**
   * 报告正文。
   * ⚠️ 后端 `VerificationReport.content` 是自由结构的 JSON，
   * 前端只**读取**已知的几个字段（facts / differences / missing /
   * history / limitations），其余字段原样保留、不做解释。
   */
  content: Record<string, any> & Partial<VerificationReportContent>
}

export const verificationReportApi = {
  /** 为申诉单生成/读取中立核验报告。 */
  reports: (caseId: string, revision?: number) =>
    http.post<unknown, VerificationReportRow>(`/review-center/cases/${caseId}/reports`, { revision }),
  /** 案件快照完整性校验。 */
  integrity: (caseId: string) => http.get<unknown, unknown>(`/review-center/cases/${caseId}/integrity`),
  /** 记录的分数版本历史（核验报告的历史对比来源）。 */
  versions: (recordId: string) => http.get<unknown, unknown[]>(`/review-center/records/${recordId}/versions`),
}

// ── P1-2 周期调度（Scheduler） ─────────────────────────────────────────────

export interface SchedulerStatus {
  enabled: boolean
  running: boolean
  intervalSeconds: number
  started: boolean
  lastRunAt: string | null
  lastResult: unknown
  consecutiveFailures: number
  nextRunAt: string | null
  runCount: number
}

export const schedulerApi = {
  /** 查看周期调度运行态（仅 HR）。 */
  status: () => http.get<unknown, SchedulerStatus>('/performance/scheduler/status'),
  /** 手动触发一轮周期调度（仅 HR，会推进任务状态并写通知）。 */
  run: () => http.post<unknown, Record<string, unknown>>('/performance/scheduler/run'),
}

// ── P1-3 执行类指令（Exec Tools，两阶段确认） ───────────────────────────────

export interface ExecPlan {
  tool: string
  summary: string
  impact: Record<string, unknown>
  confirmToken: string
  expiresAt: string
}

export type ExecToolName = 'exec_remind' | 'exec_export' | 'exec_stats'

export interface ExecToken {
  token: string
  tool: string
  scope: string
  task_id: string
}

export const execToolsApi = {
  /**
   * 换取执行类令牌（会话鉴权）。
   *
   * ⚠️ 为什么必须有这一步：`/v1/exec-tools/*` 网关只认**工具令牌**
   * （`Authorization: Bearer <tool_token>`），且令牌必须带 `exec:<tool>`
   * 授权键 —— 问答链路的只读令牌调不动写操作。
   * 所以前端先用登录会话换一个**短时效、只含本次工具授权**的令牌。
   */
  token: (tool: ExecToolName, taskId: string, scope: 'self' | 'department' = 'self') =>
    http.post<unknown, ExecToken>('/exec-tools/token', {
      tool,
      task_id: taskId,
      scope,
    }),
  /**
   * 第一阶段：生成执行计划 + 一次性确认令牌。**无副作用。**
   * ⚠️ 令牌是一次性、5 分钟时效、绑定工具名与用户。
   */
  prepare: (
    tool: ExecToolName,
    payload: { task_id: string; targets?: string[]; message?: string; scope?: string },
    bearerToken: string,
  ) =>
    http.post<unknown, ExecPlan>(`/exec-tools/${tool}/prepare`, payload, {
      headers: { Authorization: `Bearer ${bearerToken}` },
    }),
  /**
   * 第二阶段：携带令牌真正执行，产生副作用。
   */
  confirm: (
    tool: ExecToolName,
    payload: { task_id: string; confirm_token: string },
    bearerToken: string,
  ) =>
    http.post<unknown, Record<string, unknown>>(`/exec-tools/${tool}/confirm`, payload, {
      headers: { Authorization: `Bearer ${bearerToken}` },
    }),
}

// ── P1-4 代理授权（Delegation） ────────────────────────────────────────────

export interface DelegationRow {
  id: string
  delegatorId: string
  delegateId: string
  scopeType: string
  scopeValue: string
  reason: string
  startsAt: string
  endsAt: string
  revokedAt: string | null
  createdBy: string
  createdAt: string
}

export interface DelegationScope {
  employeeIds: string[]
  count: number
  activeDelegations: number
}

export const delegationApi = {
  /** 委托查询：all / delegator / delegate。 */
  list: (role: 'all' | 'delegator' | 'delegate' = 'all') =>
    http.get<unknown, DelegationRow[]>('/delegations', { params: { role } }),
  /** 创建委托（委托人 = 当前登录者）。 */
  create: (payload: {
    delegate_id: string
    scope_type?: string
    scope_value?: string
    reason?: string
    starts_at?: string | null
    ends_at?: string | null
  }) => http.post<unknown, DelegationRow>('/delegations', payload),
  /** 撤销委托（委托人本人或 HR）。 */
  revoke: (delegationId: string) => http.delete<unknown, DelegationRow>(`/delegations/${delegationId}`),
  /** 我当前通过委托获得的员工范围（到期自动缩小）。 */
  myScope: () => http.get<unknown, DelegationScope>('/delegations/my-scope'),
}
