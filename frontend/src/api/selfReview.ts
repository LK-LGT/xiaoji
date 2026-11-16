/**
 * 员工自评 API（绩效管理 · 员工自评页）。
 *
 * ⚠️ BUG-R6-41 修复背景：
 *   `EmployeeSelfReview.vue` 之前**完全没调后端** ——
 *   `allRecords` 是页面里写死的 mock 数组（`R001` / `T001`），
 *   `loadRecords` / `saveDraft` / `submitReview` 里只有
 *   "这里应该调用后端 API ..." 的 TODO 注释。
 *   后果：员工在页面上填完自评点「提交」，弹「自评已提交」，
 *   但**数据库里什么都没变** —— 主管那边永远等不到自评，
 *   绩效闭环在第一步就断了。
 *
 * 现在本页通过本模块走真实接口：
 * - 列表：`GET  /v1/assessment/records/pending`（只取自己的记录）
 * - 提交：`POST /v1/assessment/records/{recordId}/self-review`
 */
import { http } from './http'
import type { PendingAssessmentRecord } from './attendance'

/** 自评表单的五个语义字段（与后端 `answers` 一一对应）。 */
export const SELF_REVIEW_FIELDS = {
  performanceSummary: 'performance_summary',
  achievements: 'achievements',
  challenges: 'challenges',
  improvements: 'improvements',
  remarks: 'remarks',
} as const

/** 自评正文 + 结构化答案。 */
export interface SelfReviewDraft {
  performanceSummary: string
  achievements: string
  challenges: string
  improvements: string
  remarks: string
}

/** 自评提交请求体。 */
export interface SelfReviewSubmitPayload {
  /** 自评分 1–5。页面没采集分数时按「全部达成」给 4。 */
  self_score: number
  /** 自评正文。 */
  self_comment: string
  /** 亮点。 */
  highlights?: string | null
  /** 改进方向。 */
  improvement_areas?: string | null
  /** 结构化答案（五个表单字段）。 */
  answers?: Array<{ field_key: string; text: string }>
  /** 幂等键：同一份自评重复提交不产生重复数据。 */
  dedup_key?: string | null
}

/** 员工自己的待自评 / 已自评记录列表。 */
export async function fetchMySelfReviewRecords(params: {
  period?: string | null
  includeAllStatuses?: boolean
} = {}) {
  const query: Record<string, string> = {}
  if (params.period) query.period = params.period
  if (params.includeAllStatuses !== false) query.include_all_statuses = 'true'
  const res = await http.get<
    { items: PendingAssessmentRecord[]; total: number },
    { items: PendingAssessmentRecord[]; total: number }
  >('/assessment/records/pending', { params: query })
  return res.items || []
}

/** 提交自评。 */
export async function submitSelfReview(
  recordId: string,
  payload: SelfReviewSubmitPayload,
) {
  return http.post(`/assessment/records/${recordId}/self-review`, payload)
}

/** 自评详情（后端 `GET /v1/performance/records/{record_id}/self-review`）。 */
export interface SelfReviewDetail {
  record_id: string
  employee_id: string
  status: string
  /**
   * 五段自评内容。
   *
   * ⚠️ **字段形态不统一**：后端历史数据里
   * ``achievements`` / ``challenges`` 是**字符串数组**
   * （``["实现了新的 RAG 架构设计", ...]``），
   * 而 ``performance_summary`` 是**字符串**。
   * 页面渲染必须同时兼容两种形态，否则会显示成
   * ``[object Object]`` 或空白。
   */
  self_review: Record<string, unknown> | null
  submitted_at: string | null
}

/** 把自评正文的任意形态（字符串 / 数组 / 对象）归一成可展示文本。 */
export function normalizeSelfReviewText(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'string') return value
  if (Array.isArray(value)) {
    return value
      .map((item) => normalizeSelfReviewText(item))
      .filter(Boolean)
      .join('\n')
  }
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>)
      .map((item) => normalizeSelfReviewText(item))
      .filter(Boolean)
      .join('\n')
  }
  return String(value)
}

/**
 * 拉取某条记录的自评详情。
 *
 * ⚠️ BUG-R6-43 修复背景：
 *   页面「查看」按钮过去 `router.push('/performance/self-reviews/{id}')`，
 *   但**这个路由在 ``router/index.ts`` 里根本没注册** ——
 *   Vue Router 匹配不到任何路由 → 整页渲染成空白。
 *   用户点「查看」看到的就是**一片空白**，没有任何报错提示。
 *
 *   现在改为：详情在当前页用弹窗展示，数据走真实接口。
 *
 * ⚠️ 端点路径说明：自评**详情**是 ``GET /v1/performance/records/{id}/self-review``
 *   （挂在 performance 路由下），而**提交**是
 *   ``POST /v1/assessment/records/{id}/self-review``（挂在 assessment 路由下）。
 *   两者分属不同前缀，之前一度写成同一个 → 405 Method Not Allowed。
 *
 * ⚠️ 解包说明：``http.ts`` 的响应拦截器已经把 ``{code, data}``
 *   **解包成 ``data``** 了，所以这里拿到的**就是** SelfReviewDetail，
 *   不能再取一次 ``.data``（会得到 undefined → 页面显示「未找到自评内容」）。
 */
export async function fetchSelfReviewDetail(recordId: string) {
  return http.get<
    SelfReviewDetail,
    SelfReviewDetail
  >(`/performance/records/${recordId}/self-review`)
}

/** 把五段表单文本拼成自评正文（后端要求正文 >= 10 字）。 */
export function buildSelfReviewComment(draft: SelfReviewDraft): string {
  const parts: string[] = []
  if (draft.performanceSummary.trim()) parts.push(`【业绩总结】${draft.performanceSummary.trim()}`)
  if (draft.achievements.trim()) parts.push(`【主要成就】${draft.achievements.trim()}`)
  if (draft.challenges.trim()) parts.push(`【遇到的挑战】${draft.challenges.trim()}`)
  if (draft.improvements.trim()) parts.push(`【改进方向】${draft.improvements.trim()}`)
  if (draft.remarks.trim()) parts.push(`【其他说明】${draft.remarks.trim()}`)
  return parts.join('\n')
}

/** 生成幂等键：同一记录 + 同一份内容 → 同一个 key。 */
export function buildDedupKey(recordId: string, draft: SelfReviewDraft): string {
  const fingerprint = [
    draft.performanceSummary,
    draft.achievements,
    draft.challenges,
    draft.improvements,
    draft.remarks,
  ]
    .map((s) => s.trim())
    .join('|')
  let hash = 0
  for (let i = 0; i < fingerprint.length; i += 1) {
    hash = (hash * 31 + fingerprint.charCodeAt(i)) >>> 0
  }
  return `self-review:${recordId}:${hash}`
}
