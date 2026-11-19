import { http } from './http'

export interface ReviewCase {
  id: string; caseNo: string; employeeName: string; ownerId: string; title: string; reason: string
  status: string; decision?: string; decisionComment?: string; revision: number; recordId?: string
  deadline?: string; overdue: boolean; canManage: boolean; isOwner: boolean
  snapshot?: Record<string, any>; policy?: Record<string, any>
  evidence?: Array<{ id: string; name: string; digest: string }>
  events?: Array<{ id: string; sequence: number; action: string; actorId: string; payload: Record<string, any>; digest: string; createdAt: string }>
  reports?: Array<{ id: string; version: number; content: Record<string, any> }>
  reviewers?: Array<{ userId: string; status: string }>
  opinions?: Array<{ opinion: string; comment: string; version: number }>
  adjustments?: Adjustment[]
}
export interface Adjustment {
  id: string; recordId: string; caseId?: string; meetingId?: string; expectedVersion: number
  before: Record<string, any>; proposed: Record<string, any>; reason: string; status: string
  requestedBy: string; approvedBy?: string; approvalComment?: string; revision: number
}
export interface Meeting {
  id: string; title: string; period: string; status: string; revision: number
  snapshot?: Record<string, any>; participants?: string[]
  resolutions?: Array<{ id: string; recordId?: string; action: string; basis: string; ownerId: string; dueAt: string; content: Record<string, any> }>
}
const root = '/review-center'
export const reviewApi = {
  capabilities: () => http.get<unknown, { userId: string; canManage: boolean; isHr: boolean; departments: Array<{ id: string; name: string }>; storage: string; integrity: string }>(`${root}/capabilities`),
  options: () => http.get<unknown, Array<{ resultId: string; label: string }>>(`${root}/options`),
  people: () => http.get<unknown, Array<{ id: string; name: string }>>(`${root}/people`),
  cases: () => http.get<unknown, { items: ReviewCase[]; total: number }>(`${root}/cases`, { params: { limit: 200 } }),
  create: (body: unknown) => http.post<unknown, ReviewCase>(`${root}/cases`, body),
  detail: (id: string) => http.get<unknown, ReviewCase>(`${root}/cases/${id}`),
  action: (id: string, body: unknown) => http.post<unknown, ReviewCase>(`${root}/cases/${id}/actions`, body),
  opinion: (id: string, body: unknown) => http.post<unknown, ReviewCase>(`${root}/cases/${id}/opinions`, body),
  upload: (id: string, body: FormData) => http.post(`${root}/cases/${id}/evidence`, body),
  evidence: (id: string, artifactId: string) => http.get<unknown, Blob>(`${root}/cases/${id}/evidence/${artifactId}`, { responseType: 'blob' }),
  package: (id: string) => http.get<unknown, Blob>(`${root}/cases/${id}/package`, { responseType: 'blob' }),
  report: (id: string, revision: number) => http.post(`${root}/cases/${id}/reports`, { revision }),
  integrity: (id: string) => http.get<unknown, { ok: boolean; issues: string[]; guarantee: string }>(`${root}/cases/${id}/integrity`),
  versions: (id: string) => http.get<unknown, Array<Record<string, any>>>(`${root}/records/${id}/versions`),
  adjustmentContext: (id: string) => http.get<unknown, { version: number; total: number; fields: Array<{ code: string; name: string; min: number; max: number; score: number; comment: string }>; grade: string | null; grade_options: string[]; quantitative: Array<{ code: string; name: string; actual: number; target: number; comment: string }> }>(`${root}/records/${id}/adjustment-context`),
  adjustments: () => http.get<unknown, Adjustment[]>(`${root}/adjustments`),
  requestAdjustment: (body: unknown) => http.post(`${root}/adjustments`, body),
  approve: (id: string, body: unknown) => http.post(`${root}/adjustments/${id}/approval`, body),
  meetings: () => http.get<unknown, Meeting[]>(`${root}/meetings`),
  meeting: (id: string) => http.get<unknown, Meeting>(`${root}/meetings/${id}`),
  createMeeting: (body: unknown) => http.post<unknown, { id: string }>(`${root}/meetings`, body),
  resolution: (id: string, body: unknown) => http.post(`${root}/meetings/${id}/resolutions`, body),
  closeMeeting: (id: string, revision: number) => http.post(`${root}/meetings/${id}/close`, { revision }),
  outbox: () => http.get<unknown, Array<{ id: string; topic: string; status: string; attempts: number; lastError?: string }>>(`${root}/outbox`),
}
