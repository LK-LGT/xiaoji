import { http } from './http'

export interface SchemeMetric {
  code: string; name: string; type: string; weight: number; max_score?: number; dimension?: string; source?: string; unit?: string; target?: number | null
  scoring_method: string; direction?: string; scoring_description: string; evidence_required?: boolean
  rules: { level: string; min_ratio: number | null; max_ratio: number | null; score: number; description?: string }[]
}
export interface SchemePayload {
  code: string; name: string; output_mode: string
  scoring_mode?: string
  document?: { blocks: { source: string; text?: string; rows?: string[][] }[]; grade_rules: { level: string; condition: string; description: string; source: string }[]; adjustment_rules: { source: string; cells: string[] }[]; issues: string[]; review_confirmed: boolean; review_note: string; adjustment_min: number; adjustment_max: number; grading_mode: string; base_score: number }
  binding: { department_id: string; position: string; period_type: string; effective_from: string; effective_to: string }
  metrics: SchemeMetric[]
  grades: { level: string; min_score: number; max_score: number; description?: string }[]
  fields: { metric_code: string; key: string; label: string; stage: string; type: string; required: boolean }[]
}
export interface SchemeVersion {
  id: string; scheme_id: string; version_no: number; status: string; revision: number; payload: SchemePayload
}
export interface ImportBatch {
  id: string; status: string; revision: number; file_name: string; mapping: Record<string, Record<string, string>>
  preview: { schemes?: SchemePayload[]; errors?: { sheet: string; row: number; field: string; message: string }[]; warnings?: string[] }
}
export interface SchemeOptions {
  can_manage: boolean; departments: { id: string; name: string }[]; positions: string[]
  columns: Record<string, Record<string, string>>
}
export interface Coverage {
  employees: { employee_id: string; name: string; department_id?: string; position?: string; status: string; scheme_name?: string; message?: string }[]
  empty_departments: { department_id: string; name: string }[]
}
const root = '/performance'
export const listSchemes = () => http.get<unknown, SchemeVersion[]>(`${root}/schemes`)
export const schemeOptions = () => http.get<unknown, SchemeOptions>(`${root}/schemes/options`)
export const schemeCoverage = (period: string, period_type: string) => http.get<unknown, Coverage>(`${root}/scheme-coverage`, { params: { period, period_type } })
export const uploadScheme = (file: File) => { const data = new FormData(); data.append('file', file); return http.post<unknown, ImportBatch>(`${root}/scheme-imports`, data) }
export const mapScheme = (b: ImportBatch, mapping: ImportBatch['mapping']) => http.put<unknown, ImportBatch>(`${root}/scheme-imports/${b.id}/mapping`, { mapping, revision: b.revision })
export const validateScheme = (id: string) => http.post<unknown, ImportBatch>(`${root}/scheme-imports/${id}/validate`)
export const commitScheme = (b: ImportBatch) => http.post<unknown, SchemeVersion[]>(`${root}/scheme-imports/${b.id}/commit`, { revision: b.revision })
export const createScheme = (payload: SchemePayload) => http.post<unknown, SchemeVersion>(`${root}/schemes`, { payload })
export const saveScheme = (v: SchemeVersion, payload: SchemePayload) => http.patch<unknown, SchemeVersion>(`${root}/scheme-versions/${v.id}`, { payload, revision: v.revision })
export const schemeAction = (id: string, action: string) => http.post<unknown, SchemeVersion>(`${root}/scheme-versions/${id}/${action}`)
export const schemeAudit = (id: string) => http.get<unknown, { action: string; actor_id: string; created_at: string }[]>(`${root}/scheme-versions/${id}/audit`)
export const startSchemeTasks = (data: unknown) => http.post<unknown, { id: string; employee_count: number }[]>(`${root}/scheme-tasks`, data)
export async function downloadSchemeTemplate() {
  const blob = await http.get<unknown, Blob>(`${root}/schemes/import-template`, { responseType: 'blob' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = '考核方案导入模板.xlsx'; a.click(); URL.revokeObjectURL(url)
}
