import type { AuditItem } from '@/api/system'

export function formatAuditResult(result?: string) {
  if (result === 'SUCCESS') return '成功'
  if (result === 'FAILED') return '失败'
  return result || '未知'
}

export function formatRawDetail(detail?: string) {
  if (!detail) return '无原始明细'
  try {
    return JSON.stringify(JSON.parse(detail), null, 2)
  } catch {
    return detail
  }
}

export function operatorCaption(item: Pick<AuditItem, 'operatorName' | 'username'>) {
  if (item.operatorName && item.username && item.operatorName !== item.username) return `账号：${item.username}`
  return item.username ? `账号：${item.username}` : '系统操作'
}
