/**
 * P2-1 / P2-2 / P2-3 前端 API 客户端。
 *
 * ⚠️ 路径约定：``http`` 的 baseURL 是 ``/api``，Vite 代理把 ``/api/xxx``
 * 重写成 ``/v1/xxx``。所以这里写 ``/semantic-qc/...`` 而不是 ``/v1/semantic-qc/...``。
 *
 * ⚠️ 语义提醒：这一组接口**全部是「建议」或「只读统计」**。
 * 前端呈现时必须保留后端返回的 disclaimer —— 尤其面谈提纲与薪酬导出，
 * 不能让用户把 AI 的建议误读成结论。
 */
import { http } from './http'

// ── P2-1 语义质检 / 风险识别 / 面谈提纲 ──────────────────────────────────────

export interface SemanticIssue {
  code: string
  source: string
  severity: string
  field: string
  title: string
  ruleId: string
  quote: string
  basis: string
  suggestion: string
  confidence: number
  references: string[]
}

export interface RiskSignal {
  code: string
  severity: string
  title: string
  ruleId: string
  basis: string
  evidence: string[]
  confidence: number
  suggestion: string
}

export interface InterviewQuestion {
  id: string
  dimension: string
  question: string
  purpose: string
  followUp: string
  basis: string[]
  priority: 'HIGH' | 'MEDIUM' | 'LOW'
}

export interface InterviewOutline {
  employeeName: string
  purpose: string
  disclaimer: string
  questionCount: number
  questions: InterviewQuestion[]
  summary: string
}

export interface SemanticQcResult {
  overallRisk: string
  semanticIssues: SemanticIssue[]
  risks: RiskSignal[]
  interviewOutline: InterviewOutline
  disclaimer: string
  recordId: string
  employeeId: string
  period: string | null
}

export const semanticQcApi = {
  analyzeRecord: (recordId: string) =>
    http.get<unknown, SemanticQcResult>(`/semantic-qc/records/${recordId}`),
}

// ── P2-2 趋势复盘报表 ───────────────────────────────────────────────────────

export interface PeriodTrendPoint {
  period: string
  total: number | null
  rating: string | null
}

export interface PeriodTrend {
  employeeId: string
  periodCount: number
  points: PeriodTrendPoint[]
  trend: 'UP' | 'DOWN' | 'STABLE'
  average: number | null
  min: number | null
  max: number | null
  note: string
}

export interface PositionMember {
  employee_id: string
  employee_name: string
  total: number | null
  rating: string | null
  rank: number | null
  deviation: number | null
}

export interface PositionComparison {
  position: string
  period: string | null
  memberCount: number
  smallSample: boolean
  note: string
  members: PositionMember[]
  distribution: Record<string, number | null>
}

export interface RiskRosterItem {
  employee_id: string
  employee_name: string
  department_id: string | null
  position: string | null
  record_id: string
  total: number | null
  confirmed_rating: string | null
  severity: string
  signals: Array<{ code: string; severity: string; basis: string }>
}

export interface RiskRoster {
  departmentId: string | null
  period: string | null
  scannedCount: number
  riskCount: number
  items: RiskRosterItem[]
  disclaimer: string
}

export interface PayrollExport {
  period: string | null
  departmentId: string | null
  rowCount: number
  /** 已确认评级但**没有分数**的条数（算分未完成）。 */
  unscoredCount: number
  rows: Array<{
    record_id: string
    employee_key: string
    department_id: string | null
    position: string | null
    total: number | null
    rating: string | null
    coefficient: number | null
    /** false = 有等级但没分数（算分未完成），不可直接当依据用。 */
    scored: boolean
    employee_name?: string
  }>
  disclaimer: string
  privacyNote: string
}

export const trendReportApi = {
  trend: (employeeId: string) =>
    http.get<unknown, PeriodTrend>(`/trend-report/trend/${employeeId}`),
  // ⚠️ 查询参数名必须与后端 FastAPI 的 `Query(...)` 名一致（snake_case）。
  //    FastAPI 不会自动把 camelCase 映射成 snake_case，写错就是「参数被静默忽略」。
  position: (params: { position: string; period?: string; taskId?: string }) =>
    http.get<unknown, PositionComparison>('/trend-report/position', {
      params: {
        position: params.position,
        period: params.period,
        task_id: params.taskId,
      },
    }),
  riskRoster: (params: { departmentId?: string; period?: string; taskId?: string }) =>
    http.get<unknown, RiskRoster>('/trend-report/risk-roster', {
      params: {
        department_id: params.departmentId,
        period: params.period,
        task_id: params.taskId,
      },
    }),
  // ⚠️ 仅 HR。响应含 disclaimer —— 导出文件里必须带上。
  payrollExport: (params: {
    period?: string
    taskId?: string
    departmentId?: string
    includeNames?: boolean
  }) =>
    http.get<unknown, PayrollExport>('/trend-report/payroll-export', {
      params: {
        period: params.period,
        task_id: params.taskId,
        department_id: params.departmentId,
        include_names: params.includeNames,
      },
    }),
}

// ── P2-3 反馈闭环 / Prompt 灰度 / 案例库 ───────────────────────────────────

export interface FeedbackItem {
  id: string
  recordId: string
  agent: string
  promptVersion: string | null
  action: 'ACCEPT' | 'MODIFY' | 'REJECT'
  actorId: string
  diffSummary: string | null
  reason: string | null
  dimension: string | null
  createdAt: string | null
}

export interface FeedbackStats {
  agent: string
  total: number
  accept: number
  modify: number
  reject: number
  acceptRate: number | null
}

export interface PromptVersionItem {
  id: string
  agent: string
  version: string
  promptText: string
  rollout: 'off' | 'canary' | 'full'
  canaryRatio: number
  baselineVersion: string | null
  note: string | null
  createdBy: string | null
  createdAt: string | null
}

export interface RolloutComparison {
  agent: string
  sampleSize: number
  minSampleForConclusion: number
  sufficient: boolean
  versions: Array<{
    version: string
    total: number
    ACCEPT: number
    MODIFY: number
    REJECT: number
    acceptRate: number | null
    rejectRate: number | null
  }>
  recommendedVersion: string | null
  conclusion: string
  note: string
}

export interface CaseLibraryItem {
  id: string
  title: string
  scenario: string
  rating: string | null
  scoreRange: string | null
  facts: Record<string, any>
  resolution: string
  sourceRecordId: string | null
  redacted: boolean
  useCount: number
  createdBy: string | null
  createdAt: string | null
}

export const feedbackApi = {
  submit: (body: {
    record_id: string
    action: 'ACCEPT' | 'MODIFY' | 'REJECT'
    agent?: string
    prompt_version?: string | null
    diff_summary?: string | null
    reason?: string | null
    dimension?: string | null
  }) => http.post<unknown, FeedbackItem>('/feedback', body),
  list: (agent = 'comment_draft') =>
    http.get<unknown, { items: FeedbackItem[]; stats: FeedbackStats }>('/feedback', {
      params: { agent },
    }),
  listPrompts: (agent = 'comment_draft') =>
    http.get<unknown, PromptVersionItem[]>('/feedback/prompts', { params: { agent } }),
  upsertPrompt: (body: {
    agent: string
    version: string
    prompt_text: string
    rollout?: 'off' | 'canary' | 'full'
    canary_ratio?: number
    baseline_version?: string | null
    note?: string | null
  }) => http.put<unknown, PromptVersionItem>('/feedback/prompts', body),
  resolvePrompt: (agent: string, recordId: string) =>
    http.get<unknown, {
      version: string | null
      promptText: string | null
      bucket: number | null
      mode: string
      baselineVersion: string | null
    }>('/feedback/prompts/resolve', { params: { agent, record_id: recordId } }),
  compare: (agent = 'comment_draft', versions?: string[]) =>
    http.get<unknown, RolloutComparison>('/feedback/prompts/compare', {
      params: { agent, versions: versions?.join(',') },
    }),
  listCases: (params: { scenario?: string; keyword?: string; limit?: number }) =>
    http.get<unknown, CaseLibraryItem[]>('/feedback/cases', { params }),
  publishCase: (body: {
    title: string
    scenario?: string
    resolution: string
    source_record_id?: string | null
    score_range?: string | null
  }) => http.post<unknown, CaseLibraryItem>('/feedback/cases', body),
}

// ── P1-5 数据源白名单 ───────────────────────────────────────────────────────

export interface DataSourcePolicy {
  sourceCode: string
  displayName: string
  allowed: boolean
  allowedRoles: string[]
  redactedFields: string[]
  note: string
  configured: boolean
}

export const dataSourceApi = {
  list: () => http.get<unknown, DataSourcePolicy[]>('/data-sources'),
  upsert: (body: {
    source_code: string
    display_name?: string
    allowed?: boolean
    allowed_roles?: string
    redacted_fields?: string
    note?: string
  }) => http.put<unknown, DataSourcePolicy>('/data-sources', body),
}
