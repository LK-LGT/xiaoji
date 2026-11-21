/**
 * 网盘剪辑工作量 —— 管理 API（BP-2 / P4）。
 *
 * 数据口径约定：
 * - **条数有裁定值优先**：后端返回 `adjustedCount`（人工裁定后），
 *   为空则用 `totalCount`（机器判定原值）。
 * - ⚠️ **两个值都要展示** —— 只显示一个就说不清「系统判的是多少、谁改的」。
 * - **机器判定值永不被覆盖** —— 这是审计的前提。
 * - **指标量纲不同**：条数是「条」，比率是「%」⇒ 不合成综合分。
 *
 * ⚠️ **数据范围由后端收敛**
 * 前端权限位只管「谁能打开页面」，
 * 能看到谁的数据一律后端判定（与 `/v1/qa/tasks` 同一口径）。
 */
import { http } from './http'

/**
 * ⚠️ **路径不带 `/v1`**
 * vite 代理把 ``/api/xxx`` 重写成 ``/v1/xxx``（见 ``vite.config.ts``）。
 * ⇒ 这里写 ``/v1/xxx`` 会变成 ``/v1/v1/xxx`` ⇒ 404。
 */
const BASE = '/baidu/workload'

/** 复核工单状态。 */
export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn'

/** 裁定粒度。 */
export type ReviewScope = 'day' | 'file'

/** 争议类型。 */
export type DisputeType = 'missing' | 'overcounted' | 'weight' | 'other'

/** 单个争议项的裁定结果。 */
export type ItemResolution = 'pending' | 'upheld' | 'rejected' | 'need_more'

/**
 * 人工指标编码。
 *
 * ⚠️ **必须带 `video_` 前缀** —— 不能直接用 `completion_rate` / `retention_rate`。
 * 真实库 `metric` 表里 `retention_rate` 已被 HR 占用
 * （名称「留任率」、`data_source=hr_system`、`owner=HR`），
 * 语义是**员工留存率**，与短视频的**视频留存率**不是一回事。
 * ⇒ 同名会写进同一条 `MetricValue`，两个业务互相覆写。
 */
export type ManualMetricCode = 'video_completion_rate' | 'video_retention_rate'

/** 人工指标中文名。 */
export const MANUAL_METRIC_LABELS: Record<string, string> = {
  video_completion_rate: '完播率',
  video_retention_rate: '视频留存率',
}

/** 日汇总项。 */
export interface PanWorkloadDaily {
  id: string
  employeeId: string
  businessDate: string
  /** 机器判定条数（**原值，永不被覆盖**）。 */
  totalCount: number
  fileCount: number
  scannedCount: number
  excludedCount: number
  markerBreakdown: Record<string, number>
  anomalyCount: Record<string, number>
  /** 人工裁定后的条数（为空 = 没有人工调整）。 */
  adjustedCount: number | null
  adjustReason: string
  status: string
  lastSyncedAt: string | null
}

/** 文件明细项（审计根）。 */
export interface PanWorkloadFile {
  id: string
  employeeId: string
  panPath: string
  fileName: string
  extension: string
  sizeBytes: number
  contentDigest: string
  businessDate: string | null
  counted: boolean
  countWeight: number
  markers: string[]
  excludeReason: string
  isDuplicate: boolean
  duplicateKind: string
  bindType: string
}

/** 复核工单。 */
export interface PanWorkloadReview {
  id: string
  employeeId: string
  businessDate: string
  status: ReviewStatus
  scope: ReviewScope
  claimedCount: number
  systemCount: number
  finalCount: number | null
  reason: string
  resolution: string
  dailySnapshot: Record<string, unknown>
  createdBy: string
  createdAt: string | null
  resolvedAt: string | null
}

/** 争议项。 */
export interface PanWorkloadReviewItem {
  id: string
  panWorkloadFileId: string | null
  manualName: string
  manualDescription: string
  disputeType: DisputeType
  claimedWeight: number
  resolution: ItemResolution
  resolutionNote: string
  finalWeight: number | null
}

/** 证据附件（**不返回二进制**，只返回元信息 + 下载端点）。 */
export interface PanWorkloadArtifact {
  id: string
  reviewItemId?: string | null
  metricId?: string | null
  name: string
  mediaType: string
  digest: string
  sizeBytes: number
}

/** 事件流条目（hash 链）。 */
export interface PanWorkloadReviewEvent {
  seq: number
  eventType: string
  actorId: string
  payload: Record<string, unknown>
  prevHash: string
  eventHash: string
  createdAt: string | null
}

/** 链校验结果。 */
export interface ChainVerification {
  ok: boolean
  issues: string[]
  eventCount: number
  headHash: string
  guarantee: string
}

/** 人工指标。 */
export interface PanManualMetric {
  id: string
  employeeId: string
  metricDate: string
  metricCode: ManualMetricCode
  metricValue: number
  scope: 'day' | 'file'
  targetKey: string
  source: 'frontend' | 'feishu' | 'import'
  remark: string
  createdBy: string
  createdAt: string | null
}

/** 目录绑定。 */
export interface PanAccountBinding {
  id: string
  employeeId: string
  folderPath: string
  folderName: string
  active: boolean
  createdAt: string | null
}

/** 面板总览里按人的汇总。 */
export interface PanEmployeeSummary {
  employeeId: string
  employeeName: string
  totalCount: number
  days: number
  adjustedDays: number
  disputedDays: number
  anomalyCount: number
}

/** 面板总览。 */
export interface PanWorkloadSummary {
  employees: PanEmployeeSummary[]
  manualMetrics: PanManualMetric[]
  pendingReviews: PanWorkloadReview[]
  reviewCount: number
  anomalies: unknown[]
}

/** 工单详情。 */
export interface PanWorkloadReviewDetail {
  review: PanWorkloadReview
  items: PanWorkloadReviewItem[]
  artifacts: PanWorkloadArtifact[]
  events: PanWorkloadReviewEvent[]
  chain: ChainVerification
}

// ─────────────────────────────────────────────────────────────────────────
// 查询
// ─────────────────────────────────────────────────────────────────────────

export const panWorkloadApi = {
  /** 面板总览。 */
  getSummary: () => http.get<PanWorkloadSummary, PanWorkloadSummary>(`${BASE}/summary`),

  /** 日汇总。 */
  getDaily: (params?: {
    employee_id?: string
    start?: string
    end?: string
  }) => http.get<{ items: PanWorkloadDaily[] }, { items: PanWorkloadDaily[] }>(`${BASE}/daily`, { params }),

  /** 文件明细（审计根）。 */
  getFiles: (params: { employee_id: string; business_date: string; counted_only?: boolean }) =>
    http.get<{ items: PanWorkloadFile[] }, { items: PanWorkloadFile[] }>(`${BASE}/files`, { params }),

  /** 人工指标。 */
  getManualMetrics: (params?: {
    employee_id?: string
    start?: string
    end?: string
    metric_code?: string
  }) => http.get<{ items: PanManualMetric[] }, { items: PanManualMetric[] }>(`${BASE}/manual-metrics`, { params }),

  /** 目录绑定。 */
  getBindings: () => http.get<{ items: PanAccountBinding[] }, { items: PanAccountBinding[] }>(`${BASE}/bindings`),

  /** 工单列表。 */
  getReviews: (params?: { employee_id?: string; status?: string }) =>
    http.get<{ items: PanWorkloadReview[] }, { items: PanWorkloadReview[] }>(`${BASE}/reviews`, { params }),

  /** 工单详情（含事件流与链校验）。 */
  getReview: (reviewId: string) =>
    http.get<PanWorkloadReviewDetail, PanWorkloadReviewDetail>(`${BASE}/reviews/${reviewId}`),

  /** 链校验。 */
  getChain: (reviewId: string) =>
    http.get<ChainVerification, ChainVerification>(`${BASE}/reviews/${reviewId}/chain`),

  /**
   * 下载证据附件。
   *
   * ⚠️ **用 blob 而不是裸 URL**
   * - 裸 ``<a href>`` **不会自动带 Authorization 头** ⇒ 要么 401，要么把 token 拼进 URL（泄露）。
   * - blob 方式走 axios ⇒ 自动带 token，且**不暴露** token。
   */
  downloadArtifact: async (reviewId: string, artifactId: string, filename: string) => {
    const resp = await http.get(`${BASE}/reviews/${reviewId}/artifacts/${artifactId}`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    // ⚠️ **必须 revoke** —— 否则 blob 一直占内存
    URL.revokeObjectURL(url)
  },
}

// ─────────────────────────────────────────────────────────────────────────
// 写操作
// ─────────────────────────────────────────────────────────────────────────

export const panWorkloadActions = {
  /** 发起工单。 */
  createReview: (payload: {
    business_date: string
    claimed_count: number
    reason: string
    scope: ReviewScope
  }) => http.post<{ item: PanWorkloadReview }, { item: PanWorkloadReview }>(`${BASE}/reviews`, payload),

  /** 加争议项。 */
  addItem: (
    reviewId: string,
    payload: {
      pan_workload_file_id?: string
      manual_name?: string
      manual_description?: string
      dispute_type: DisputeType
      claimed_weight?: number
    },
  ) =>
    http.post<{ item: PanWorkloadReviewItem }, { item: PanWorkloadReviewItem }>(`${BASE}/reviews/${reviewId}/items`, payload),

  /** 上传证据。 */
  uploadArtifact: (reviewId: string, file: File, reviewItemId?: string) => {
    const form = new FormData()
    form.append('file', file)
    if (reviewItemId) form.append('review_item_id', reviewItemId)
    return http
      .post<{ item: PanWorkloadArtifact }, { item: PanWorkloadArtifact }>(
        `${BASE}/reviews/${reviewId}/artifacts`,
        form,
      )
  },

  /** 裁定单个争议项（主管）。 */
  resolveItem: (
    reviewId: string,
    itemId: string,
    payload: { resolution: ItemResolution; final_weight?: number | null; resolution_note?: string },
  ) =>
    http
      .post<{ item: PanWorkloadReviewItem }, { item: PanWorkloadReviewItem }>(
        `${BASE}/reviews/${reviewId}/items/${itemId}/resolve`,
        payload,
      ),

  /** 裁定工单（主管）。 */
  resolveReview: (
    reviewId: string,
    payload: { resolution: 'approved' | 'rejected'; final_count?: number | null; resolution_note?: string },
  ) => http.post<{ item: PanWorkloadReview }, { item: PanWorkloadReview }>(`${BASE}/reviews/${reviewId}/resolve`, payload),

  /** 人工填报（前端上传通路）。 */
  submitManualMetric: (payload: {
    metric_date: string
    metric_code: ManualMetricCode
    metric_value: number
    scope?: 'day' | 'file'
    target_key?: string
    remark?: string
  }) => http.post<{ item: PanManualMetric }, { item: PanManualMetric }>(`${BASE}/manual-metrics`, payload),

  /** 上传人工指标的证据。 */
  uploadMetricArtifact: (metricId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http
      .post<{ item: PanWorkloadArtifact }, { item: PanWorkloadArtifact }>(
        `${BASE}/manual-metrics/${metricId}/artifacts`,
        form,
      )
  },

  /** 配目录绑定（HR/管理员）。 */
  createBinding: (payload: { employee_id: string; folder_path: string; folder_name?: string }) =>
    http.post<{ item: PanAccountBinding }, { item: PanAccountBinding }>(`${BASE}/bindings`, payload),

  /** 手动触发采集（HR/管理员）。 */
  collect: () => http.post(`${BASE}/collect`),

  /** 月度聚合（HR/管理员）。 */
  aggregate: (payload: { period?: string; employee_ids?: string[] }) =>
    http.post(`${BASE}/aggregate`, payload),
}
