/**
 * 考勤展示相关 API（阶段三 B-3）。
 *
 * 数据口径约定：
 * - 考勤证据**只作参考**，不自动扣分（后端硬约束：禁止进入 MetricValue 算分集）。
 * - 六个异常类别**全部展示**，没有真实数据时显示 0 并标注【暂无数据】，不隐藏。
 * - 周期格式：后端紧凑格式 `202611` 与带横杠格式 `2026-11` 等价。
 */
import { http } from './http'
import { toDashedPeriod as toDashedPeriodUtil } from '@/utils/period'

/** 六类异常（与后端 `AttendanceAnomalyType` 一一对应）。 */
export type AttendanceAnomalyType =
  | 'late'
  | 'early_leave'
  | 'missing_punch'
  | 'outside'
  | 'leave'
  | 'overtime'

/** 展示顺序固定为六类全量。 */
export const ATTENDANCE_ANOMALY_TYPES: AttendanceAnomalyType[] = [
  'late',
  'early_leave',
  'missing_punch',
  'leave',
  'overtime',
  'outside',
]

/** 六类异常的中文名。 */
export const ATTENDANCE_ANOMALY_LABELS: Record<AttendanceAnomalyType, string> = {
  late: '迟到',
  early_leave: '早退',
  missing_punch: '缺卡',
  leave: '请假',
  overtime: '加班',
  outside: '外勤',
}

/**
 * 暂无真实数据的类别（当前阶段飞书未返回这两类数据源）。
 * 仍然展示，值为 0，并标注【暂无数据】。
 */
export const ATTENDANCE_NO_DATA_TYPES: AttendanceAnomalyType[] = ['overtime', 'outside']

/** 考勤异常不自动扣分，仅作参考。 */
export const ATTENDANCE_DISCLAIMER = '考勤异常不自动扣分，仅作参考'

/** 考勤统计汇总。 */
export interface AttendanceSummary {
  employee_id: string
  period: string
  total_records: number
  worked_days: number
  absent_days: number
  anomaly_counts: Record<string, number>
  anomaly_days: Record<string, number>
  as_of: string | null
  by_period: Record<string, Record<string, unknown>>
}

/** 记录详情里的参考型考勤证据（后端 `build_attendance_view`）。 */
export interface AttendanceReferenceEvidence {
  evidence_id: string
  type: AttendanceAnomalyType | string
  count: number
  summary: string | null
  approved: boolean | null
  as_of: string | null
  disclaimer: string
}

/** 待评记录列表项。 */
export interface PendingAssessmentRecord {
  record_id: string
  task_id: string
  task_name: string | null
  period: string | null
  period_display: string | null
  employee: {
    id: string
    name: string
    position: string | null
    department_id: string | null
  } | null
  status: string
  confirmed_rating: string | null
  manager_comment: string | null
  created_at: string
  updated_at: string
}

/** 记录详情里的自评内容（后端 `AssessmentRecord.self_review_content`）。 */
export interface SelfReviewContent {
  performance_summary?: string
  achievements?: string[] | string
  challenges?: string[] | string
  improvements?: string
  submitted_at?: string
}

/** 考核记录详情（`GET /v1/assessment/records/{record_id}`）。 */
export interface AssessmentRecordDetail {
  record_id: string
  task_id: string
  status: string
  period: string | null
  period_display: string | null
  employee: {
    id: string
    name: string
    position: string | null
    department_id: string | null
  } | null
  submit_source: string | null
  confirmed_rating: string | null
  manager_comment: string | null
  self_review: SelfReviewContent | null
  score: {
    total: number
    per_metric: unknown
    computed_at: string
  } | null
  rating_draft: unknown
  comment_draft: unknown
  evidence: {
    scoring: Array<{ id: string; summary: string; impact: unknown; source: string }>
    reference_only: AttendanceReferenceEvidence[]
  }
  created_at: string
  updated_at: string
}

/** 待评记录列表响应。 */
export interface PendingRecordsResponse {
  items: PendingAssessmentRecord[]
  total: number
  pending_total: number
  period: string | null
  period_display: string | null
}

/** 可见周期列表项。 */
export interface VisiblePeriod {
  period: string
  period_display: string
  task_ids: string[]
  record_count: number
  pending_count: number
}

/**
 * 紧凑周期 `202611` → 带横杠 `2026-11`。
 * 后端两种格式都接受，这里统一输出带横杠用于展示和日期区间计算。
 *
 * ⚠️ **BUG-R6-26**：实现已收敛到 `@/utils/period`（唯一权威实现），
 * 这里只保留一个薄封装，避免前端出现两套周期换算逻辑。
 */
export function toDashedPeriod(period: string | null | undefined): string | null {
  const dashed = toDashedPeriodUtil(period)
  return dashed || null
}

/**
 * 周期 → 该月的起止日期（含首尾）。
 * 用于调用考勤统计接口。
 */
export function periodDateRange(period: string | null | undefined) {
  const dashed = toDashedPeriod(period)
  if (!dashed) return null
  const [yearText, monthText] = dashed.split('-')
  const year = Number(yearText)
  const month = Number(monthText)
  if (!year || !month || month < 1 || month > 12) return null
  const lastDay = new Date(year, month, 0).getDate()
  return {
    startDate: `${yearText}-${monthText}-01`,
    endDate: `${yearText}-${monthText}-${String(lastDay).padStart(2, '0')}`,
  }
}

/** 待评记录列表。 */
export async function fetchPendingRecords(params: {
  period?: string | null
  taskId?: string | null
  includeAllStatuses?: boolean
} = {}) {
  const query: Record<string, string> = {}
  if (params.period) query.period = params.period
  if (params.taskId) query.task_id = params.taskId
  if (params.includeAllStatuses) query.include_all_statuses = 'true'
  return http.get<PendingRecordsResponse, PendingRecordsResponse>(
    '/assessment/records/pending',
    { params: query },
  )
}

/** 可见考核周期列表。 */
export async function fetchVisiblePeriods() {
  return http.get<{ items: VisiblePeriod[]; total: number }, { items: VisiblePeriod[]; total: number }>(
    '/assessment/records/periods',
  )
}

/** 考勤统计汇总。 */
export async function fetchAttendanceSummary(params: {
  employeeId: string
  startDate: string
  endDate: string
}) {
  return http.get<AttendanceSummary, AttendanceSummary>('/attendance/summary', {
    params: {
      employee_id: params.employeeId,
      start_date: params.startDate,
      end_date: params.endDate,
    },
  })
}

/** 考核记录详情（评分页主数据源）。 */
export async function fetchAssessmentRecord(recordId: string) {
  return http.get<AssessmentRecordDetail, AssessmentRecordDetail>(
    `/assessment/records/${recordId}`,
  )
}
