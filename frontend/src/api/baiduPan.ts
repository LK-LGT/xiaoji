import { http } from './http'

/**
 * 百度网盘绩效校验 —— 前端 API。
 *
 * ⚠️ **只提示，不判定，不自动改分**
 * 网盘计数是**部门级**总量（文件不带"谁剪的"信息），
 * 差异仅供主管人工核实 —— 与后端 `compare_with_report` 口径一致。
 */

export interface PanCompareItem {
  metric_code: string
  metric_name: string
  reported: number | null
  actual: number | null
  comparison: {
    status: 'match' | 'mismatch' | 'no_actual' | 'no_reported' | 'no_data'
    reported: number | null
    actual: number | null
    diff: number | null
    message: string
  }
}

export interface PanVerification {
  record_id: string
  is_own: boolean
  snapshot: {
    window_start: string
    window_end: string
    total: number
    daily_counts: Record<string, number>
    as_of: string | null
    scanned_path: string
  } | null
  items: PanCompareItem[]
  notice: string
}

export interface PanStatus {
  enabled: boolean
  configured: boolean
  authorized: boolean
  appName: string
  watchPath: string
  redirectUri: string
  countExtensions: string
  excludeKeywords: string
  probe: {
    ok: boolean
    path: string
    scanned?: number
    hint?: string
    error?: string
    errno?: number
  } | null
}

export interface PanCount {
  id: string
  sourceCode: string
  windowStart: string
  windowEnd: string
  total: number
  dailyCounts: Record<string, number>
  scannedFiles: number
  excludedFiles: number
  scannedPath: string
  asOf: string | null
  createdAt: string | null
}

export interface PanBatch {
  id: string
  triggerType: string
  status: string
  scannedFiles: number
  countedFiles: number
  excludedFiles: number
  errorMessage: string
  detail: Record<string, unknown>
  startedAt: string | null
  finishedAt: string | null
}

const root = '/baidu/pan'

export const panStatus = () => http.get<unknown, PanStatus>(`${root}/status`)
export const panAuthorizeUrl = () =>
  http.get<unknown, { authorizeUrl: string; watchPath: string; hint: string }>(`${root}/authorize-url`)
export const panAuthorize = (code: string) =>
  http.post<unknown, { authorized: boolean; expires_in: number; scope: string; probe: PanStatus['probe'] }>(
    `${root}/authorize`,
    { code }
  )
export const panSync = (data?: { window_start?: string; window_end?: string }) =>
  http.post<unknown, PanBatch>(`${root}/sync`, data || {})
export const panCounts = () => http.get<unknown, PanCount[]>(`${root}/counts`)
export const panBatches = () => http.get<unknown, PanBatch[]>(`${root}/batches`)
export const panSyncStatus = () =>
  http.get<unknown, { enabled: boolean; running: boolean; lastResult: string | null; lastError: string }>(
    `${root}/sync/status`
  )

/** 主管核对视图 —— 某条考核记录的「申报 vs 实测」。 */
export const schemeVerification = (recordId: string) =>
  http.get<unknown, PanVerification>(`/performance/scheme-records/${recordId}/verification`)
