import type { KnowledgeUploadMetadata, KnowledgeUploadResult } from '@/api/knowledge'

export type KnowledgeBatchStatus = 'pending' | 'uploading' | 'success' | 'failed'

export interface KnowledgeBatchFile {
  name: string
  size: number
  lastModified?: number
}

export interface KnowledgeBatchItem<TFile extends KnowledgeBatchFile = File> {
  id: string
  fingerprint: string
  file: TFile
  metadata: KnowledgeUploadMetadata
  status: KnowledgeBatchStatus
  error: string
  result: KnowledgeUploadResult | null
}

export interface KnowledgeBatchAppendResult<TFile extends KnowledgeBatchFile> {
  items: KnowledgeBatchItem<TFile>[]
  rejected: Array<{ file: TFile; reason: string }>
}

export interface KnowledgeBatchRunResult<TFile extends KnowledgeBatchFile> {
  started: boolean
  invalid: Array<{ item: KnowledgeBatchItem<TFile>; reason: string }>
  succeeded: KnowledgeBatchItem<TFile>[]
  failed: KnowledgeBatchItem<TFile>[]
}

const DEFAULT_EXTENSIONS = ['.doc', '.docx', '.md', '.markdown']

export function emptyKnowledgeUploadMetadata(): KnowledgeUploadMetadata {
  return { audienceCodes: [], scopeType: 'GLOBAL', knowledgeType: '', appliesTo: [], bindingLevel: '' }
}

export function cloneKnowledgeUploadMetadata(metadata: KnowledgeUploadMetadata): KnowledgeUploadMetadata {
  return {
    ...metadata,
    audienceCodes: [...metadata.audienceCodes],
    appliesTo: [...metadata.appliesTo],
  }
}

export function knowledgeFileFingerprint(file: KnowledgeBatchFile) {
  return `${file.name.trim().toLowerCase()}::${Number(file.size || 0)}`
}

export function appendKnowledgeBatchFiles<TFile extends KnowledgeBatchFile>(
  existing: KnowledgeBatchItem<TFile>[],
  files: TFile[],
  defaultMetadata: KnowledgeUploadMetadata,
  options: { maxFiles?: number; maxSize?: number; allowedExtensions?: string[] } = {},
): KnowledgeBatchAppendResult<TFile> {
  const maxFiles = options.maxFiles ?? 20
  const maxSize = options.maxSize ?? 10 * 1024 * 1024
  const allowedExtensions = options.allowedExtensions ?? DEFAULT_EXTENSIONS
  const fingerprints = new Set(existing.map((item) => item.fingerprint))
  const items = [...existing]
  const rejected: Array<{ file: TFile; reason: string }> = []

  files.forEach((file, index) => {
    const lowerName = file.name.trim().toLowerCase()
    const fingerprint = knowledgeFileFingerprint(file)
    let reason = ''
    if (!allowedExtensions.some((extension) => lowerName.endsWith(extension))) reason = '仅支持 Word 或 Markdown 文档'
    else if (file.size > maxSize) reason = '单个文件不能超过 10 MB'
    else if (fingerprints.has(fingerprint)) reason = '同批次中已存在同名且同大小的文档'
    else if (items.length >= maxFiles) reason = `单批最多上传 ${maxFiles} 份文档`

    if (reason) {
      rejected.push({ file, reason })
      return
    }

    fingerprints.add(fingerprint)
    items.push({
      id: `${Date.now()}-${file.lastModified || 0}-${index}-${Math.random().toString(36).slice(2, 8)}`,
      fingerprint,
      file,
      metadata: cloneKnowledgeUploadMetadata(defaultMetadata),
      status: 'pending',
      error: '',
      result: null,
    })
  })

  return { items, rejected }
}

export function applyMetadataToBatchItems<TFile extends KnowledgeBatchFile>(
  items: KnowledgeBatchItem<TFile>[],
  itemIds: string[],
  metadata: KnowledgeUploadMetadata,
) {
  const targets = new Set(itemIds)
  items.forEach((item) => {
    if (targets.has(item.id) && item.status !== 'uploading' && item.status !== 'success') {
      item.metadata = cloneKnowledgeUploadMetadata(metadata)
      item.error = ''
    }
  })
}

export async function runKnowledgeBatchUpload<TFile extends KnowledgeBatchFile>(
  items: KnowledgeBatchItem<TFile>[],
  upload: (file: TFile, metadata: KnowledgeUploadMetadata) => Promise<KnowledgeUploadResult>,
  options: {
    concurrency?: number
    itemIds?: string[]
    validate: (metadata: KnowledgeUploadMetadata) => string
  },
): Promise<KnowledgeBatchRunResult<TFile>> {
  const requestedIds = options.itemIds ? new Set(options.itemIds) : null
  const targets = items.filter((item) => (
    (!requestedIds || requestedIds.has(item.id)) && (item.status === 'pending' || item.status === 'failed')
  ))
  const metadataSnapshots = new Map(targets.map((item) => [item.id, cloneKnowledgeUploadMetadata(item.metadata)]))
  const invalid = targets
    .map((item) => ({ item, reason: options.validate(metadataSnapshots.get(item.id)!) }))
    .filter((entry) => Boolean(entry.reason))

  if (!targets.length || invalid.length) return { started: false, invalid, succeeded: [], failed: [] }

  const concurrency = Math.max(1, Math.min(options.concurrency ?? 3, targets.length))
  let cursor = 0
  async function worker() {
    while (cursor < targets.length) {
      const item = targets[cursor++]
      item.status = 'uploading'
      item.error = ''
      item.result = null
      try {
        item.result = await upload(item.file, metadataSnapshots.get(item.id)!)
        item.status = 'success'
      } catch (error) {
        item.status = 'failed'
        item.error = error instanceof Error ? error.message : '文档入库失败'
      }
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker))
  return {
    started: true,
    invalid: [],
    succeeded: targets.filter((item) => item.status === 'success'),
    failed: targets.filter((item) => item.status === 'failed'),
  }
}
