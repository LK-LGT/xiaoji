export type ResultType = 'MONTH' | 'WEEK'
export type ResultDisplayStatus = 'TODO' | 'IN_REVIEW' | 'DONE' | 'RETURNED'
export type ResultAction = 'EDIT' | 'PROGRESS' | 'DETAIL'

export interface ResultStatusMeta {
  label: string
  type: 'info' | 'primary' | 'success' | 'warning' | 'danger'
  editable: boolean
  approvable: boolean
  action: ResultAction
}

export interface ResultStatusCounts {
  todo: number
  returned: number
  inReview: number
  done: number
}

const META: Record<ResultDisplayStatus, ResultStatusMeta> = {
  TODO: { label: '待填写', type: 'warning', editable: true, approvable: false, action: 'EDIT' },
  IN_REVIEW: { label: '审批中', type: 'info', editable: false, approvable: false, action: 'PROGRESS' },
  DONE: { label: '已完成', type: 'success', editable: false, approvable: false, action: 'DETAIL' },
  RETURNED: { label: '已退回', type: 'danger', editable: true, approvable: false, action: 'EDIT' },
}

export function normalizeResultStatus(status: unknown): ResultDisplayStatus {
  const value = String(status || '').toUpperCase()
  if (['REJECTED', 'RETURNED'].includes(value)) return 'RETURNED'
  if (['APPROVED', 'CONFIRMED', 'COMPLETED', 'LOCKED'].includes(value)) return 'DONE'
  if (['SUBMITTED', 'PENDING', 'REVIEWING'].includes(value)) return 'IN_REVIEW'
  return 'TODO'
}

export function resultStatusMeta(status: unknown): ResultStatusMeta {
  return META[normalizeResultStatus(status)]
}

export function resultPrimaryAction(status: unknown) {
  const action = resultStatusMeta(status).action
  if (action === 'EDIT') return '填写成果'
  if (action === 'PROGRESS') return '查看进度'
  return '查看详情'
}

export function resultStatusRank(status: unknown) {
  return ({ RETURNED: 0, TODO: 1, IN_REVIEW: 2, DONE: 3 })[normalizeResultStatus(status)]
}

export function resultStatusCounts(statuses: unknown[]): ResultStatusCounts {
  return statuses.reduce<ResultStatusCounts>((counts, status) => {
    const key = normalizeResultStatus(status)
    if (key === 'RETURNED') counts.returned += 1
    else if (key === 'TODO') counts.todo += 1
    else if (key === 'IN_REVIEW') counts.inReview += 1
    else counts.done += 1
    return counts
  }, { todo: 0, returned: 0, inReview: 0, done: 0 })
}
