import { http } from './http'

export interface KnowledgeSyncResult {
  id: number | null
  triggerSource: string
  status: 'RUNNING' | 'SUCCESS' | 'PARTIAL' | 'FAILED'
  vaultPath: string
  scannedCount: number
  publishedCount: number
  failedCount: number
  errors: string[]
  startedAt: string
  finishedAt: string | null
}

export interface KnowledgeStatus {
  enabled: boolean
  vaultPath: string
  vaultAvailable: boolean
  documentCounts: Record<string, number>
  chunkCount: number
  relationCount: number
  vectorEngine: string
  vectorAvailable: boolean
  vectorCollections: string[]
  vectorPointCount: number
  vectorError: string | null
  lastSync: KnowledgeSyncResult | null
}

export interface KnowledgeDocument {
  id: number
  knowledgeId: string
  title: string
  knowledgeType: string
  status: string
  version: string
  owner: string
  reviewer: string | null
  binding: string
  classification: string
  departmentIds: number[]
  roleCodes: string[]
  appliesTo: string[]
  audienceCodes: KnowledgeAudience[]
  scopeType: KnowledgeScopeType
  scopeOrgId: string | null
  scopeOrgName: string | null
  effectiveDate: string | null
  expiryDate: string | null
  sourcePath: string
  sourceType: 'VAULT' | 'UPLOAD'
  originalFileName: string | null
  checksum: string
  manuallyDisabled: boolean
  chunkCount: number
  relationCount: number
  indexStatus: string
  vectorCount: number
  embeddingModels: string[]
  indexedAt: string | null
  syncedAt: string
}

export interface KnowledgeChunk {
  chunkNo: number
  headingPath: string
  content: string
  checksum: string
}

export interface KnowledgeRelation {
  type: string
  target: string
}

export interface KnowledgeDocumentDetail {
  document: KnowledgeDocument
  chunks: KnowledgeChunk[]
  relations: KnowledgeRelation[]
}

export interface KnowledgeDocumentHistory {
  id: number | null
  documentId: number
  knowledgeId: string
  title: string
  version: string
  status: string
  sourceType: 'VAULT' | 'UPLOAD'
  originalFileName: string | null
  sourcePath: string
  checksum: string
  chunkCount: number
  vectorCount: number
  operation: 'CURRENT' | 'VERSION_REPLACED' | 'DISABLED' | 'ENABLED' | 'DELETED'
  changeNote: string | null
  operatorName: string
  createdAt: string
}

export interface KnowledgeActionResult {
  documentId: number
  knowledgeId: string
  action: 'DISABLE' | 'ENABLE' | 'DELETE'
  vectorCleanupSucceeded: boolean
  message: string
  operatedAt: string
}

export interface KnowledgeHit {
  knowledgeId: string
  title: string
  knowledgeType: string
  version: string
  binding: string
  headingPath: string
  content: string
  citation: string
  checksum: string
  score: number
}

export interface KnowledgeUploadResult {
  documentId: number
  knowledgeId: string
  title: string
  fileName: string
  fileType: string
  chunkCount: number
  vectorCount: number
  embeddingModels: string[]
  uploadedAt: string
}

export interface UploadedKnowledgeDocument {
  documentId: number
  knowledgeId: string
  title: string
  fileName: string
  chunkCount: number
  vectorCount: number
  embeddingModels: string[]
  uploadedAt: string
}

export function getKnowledgeStatusApi() {
  return http.get<unknown, KnowledgeStatus>('/system/knowledge/status')
}

export function listKnowledgeDocumentsApi() {
  return http.get<unknown, KnowledgeDocument[]>('/system/knowledge/documents')
}

export function getKnowledgeDocumentApi(id: number) {
  return http.get<unknown, KnowledgeDocumentDetail>(`/system/knowledge/documents/${id}`)
}

export type KnowledgeAudience = 'MANAGEMENT' | 'EMPLOYEE'
export type KnowledgeScopeType = 'GLOBAL' | 'DEPARTMENT' | 'PROJECT'

export interface KnowledgeUploadMetadata {
  audienceCodes: KnowledgeAudience[]
  scopeType: KnowledgeScopeType
  scopeOrgId?: string
  knowledgeType: string
  appliesTo: string[]
  bindingLevel: string
}

export function validateKnowledgeUploadMetadata(metadata: KnowledgeUploadMetadata) {
  if (!metadata.audienceCodes.length) return '请选择至少一个适用角色'
  if (metadata.scopeType !== 'GLOBAL' && !metadata.scopeOrgId) return '请选择指定的部门或项目'
  if (!metadata.knowledgeType) return '请选择文档类型'
  if (!metadata.appliesTo.length) return '请选择至少一个适用业务'
  if (!metadata.bindingLevel) return '请选择约束等级'
  return ''
}

export function getKnowledgeDocumentHistoryApi(id: number) {
  return http.get<unknown, KnowledgeDocumentHistory[]>(`/system/knowledge/documents/${id}/history`)
}

export function updateKnowledgeDocumentVersionApi(id: number, file: File, version: string, changeNote: string) {
  const data = new FormData()
  data.append('file', file)
  data.append('version', version)
  data.append('changeNote', changeNote)
  return http.post<FormData, KnowledgeUploadResult>(`/system/knowledge/documents/${id}/versions`, data, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180_000,
  })
}

export function disableKnowledgeDocumentApi(id: number) {
  return http.post<undefined, KnowledgeActionResult>(`/system/knowledge/documents/${id}/disable`)
}

export function enableKnowledgeDocumentApi(id: number) {
  return http.post<undefined, KnowledgeActionResult>(`/system/knowledge/documents/${id}/enable`)
}

export function deleteKnowledgeDocumentApi(id: number) {
  return http.delete<unknown, KnowledgeActionResult>(`/system/knowledge/documents/${id}`)
}

export function syncKnowledgeApi() {
  return http.post<undefined, KnowledgeSyncResult>('/system/knowledge/sync')
}

export function previewKnowledgeApi(params: { appliesTo: string; query: string; deptId?: string }) {
  return http.get<unknown, KnowledgeHit[]>('/system/knowledge/preview', { params })
}

export function uploadLeaderKnowledgeApi(file: File, metadata: KnowledgeUploadMetadata) {
  const data = new FormData()
  data.append('file', file)
  data.append('audienceCodes', metadata.audienceCodes.join(','))
  data.append('scopeType', metadata.scopeType)
  if (metadata.scopeOrgId) data.append('scopeOrgId', String(metadata.scopeOrgId))
  data.append('knowledgeType', metadata.knowledgeType)
  data.append('appliesTo', metadata.appliesTo.join(','))
  data.append('bindingLevel', metadata.bindingLevel)
  return http.post<FormData, KnowledgeUploadResult>('/leader/knowledge/upload', data, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 180_000,
  })
}

export function listLeaderKnowledgeUploadsApi(limit = 12) {
  return http.get<unknown, UploadedKnowledgeDocument[]>('/leader/knowledge/uploads', { params: { limit } })
}
