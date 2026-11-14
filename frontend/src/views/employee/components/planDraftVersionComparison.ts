export type PlanDraftSnapshotType = 'MONTH' | 'WEEK' | 'DAY'
export type PlanDraftSnapshot = Record<string, unknown>

export interface NormalizedMonthPlanItem {
  workType: string
  taskName: string
  taskContent: string
  deliverable: string
  quality: string
  estimatedStartDate: string
  estimatedEndDate: string
  performanceWeight: number
  estimatedValue: number
}

export interface FieldChange {
  key: string
  label: string
  before: string
  after: string
}

export interface MonthPlanItemChange {
  key: string
  type: 'added' | 'modified' | 'removed'
  title: string
  before?: NormalizedMonthPlanItem
  after?: NormalizedMonthPlanItem
  fields: FieldChange[]
}

export interface MonthPlanDiff {
  items: MonthPlanItemChange[]
  counts: { added: number; modified: number; removed: number }
  unchanged: boolean
}

type MonthPlanFieldKey = keyof NormalizedMonthPlanItem

interface IndexedMonthPlanItem {
  id: number | null
  index: number
  value: NormalizedMonthPlanItem
  fingerprint: string
}

interface PositionedMonthPlanItemChange extends MonthPlanItemChange {
  sortPosition: number
}

const monthPlanFields: Array<{ key: MonthPlanFieldKey; label: string }> = [
  { key: 'workType', label: '工作类型' },
  { key: 'taskName', label: '任务名称' },
  { key: 'taskContent', label: '任务内容' },
  { key: 'deliverable', label: '交付物' },
  { key: 'quality', label: '质量标准' },
  { key: 'estimatedStartDate', label: '预估开始日期' },
  { key: 'estimatedEndDate', label: '预估结束日期' },
  { key: 'performanceWeight', label: '绩效权重' },
  { key: 'estimatedValue', label: '预估价值' },
]

const workTypeLabels: Record<string, string> = {
  TASK: '任务',
  METRIC: '指标',
  DOCUMENT: '文档',
  PROJECT: '项目',
  COMMUNICATION: '沟通协作',
  ROUTINE: '日常工作',
  UNKNOWN: '未分类',
}

function record(value: unknown): PlanDraftSnapshot {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as PlanDraftSnapshot
    : {}
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function text(value: unknown): string {
  return value === null || value === undefined ? '' : String(value)
}

function number(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

function nullableId(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function normalizeMonthPlanItem(value: unknown): NormalizedMonthPlanItem {
  const item = record(value)
  return {
    workType: text(item.workType) || 'UNKNOWN',
    taskName: text(item.taskName),
    taskContent: text(item.taskContent),
    deliverable: text(item.deliverable),
    quality: text(item.quality),
    estimatedStartDate: text(item.estimatedStartDate),
    estimatedEndDate: text(item.estimatedEndDate),
    performanceWeight: number(item.performanceWeight),
    estimatedValue: number(item.estimatedValue),
  }
}

function indexedMonthPlanItems(snapshot: PlanDraftSnapshot): IndexedMonthPlanItem[] {
  return list(record(snapshot).items).map((item, index) => {
    const value = normalizeMonthPlanItem(item)
    return {
      id: nullableId(record(item).id),
      index,
      value,
      fingerprint: JSON.stringify(value),
    }
  })
}

function isMeaningfulFieldValue(key: MonthPlanFieldKey, value: string | number): boolean {
  if (typeof value === 'number') return value !== 0
  if (key === 'workType') return value !== '' && value !== 'UNKNOWN'
  return value.trim() !== ''
}

function sharedMeaningfulFieldCount(left: NormalizedMonthPlanItem, right: NormalizedMonthPlanItem): number {
  return monthPlanFields.reduce((count, field) => {
    const leftValue = left[field.key]
    const rightValue = right[field.key]
    return count + (leftValue === rightValue && isMeaningfulFieldValue(field.key, leftValue) ? 1 : 0)
  }, 0)
}

function matchMonthPlanItems(
  historical: IndexedMonthPlanItem[],
  current: IndexedMonthPlanItem[],
): Map<number, number> {
  const matches = new Map<number, number>()
  const usedCurrent = new Set<number>()
  const match = (historicalIndex: number, currentIndex: number) => {
    matches.set(historicalIndex, currentIndex)
    usedCurrent.add(currentIndex)
  }

  for (const historicalItem of historical) {
    if (historicalItem.id === null) continue
    const currentItem = current.find((item) => !usedCurrent.has(item.index) && item.id === historicalItem.id)
    if (currentItem) match(historicalItem.index, currentItem.index)
  }

  for (const historicalItem of historical) {
    if (matches.has(historicalItem.index)) continue
    const currentItem = current
      .filter((item) => !usedCurrent.has(item.index) && item.fingerprint === historicalItem.fingerprint)
      .sort((left, right) => Math.abs(left.index - historicalItem.index) - Math.abs(right.index - historicalItem.index))[0]
    if (currentItem) match(historicalItem.index, currentItem.index)
  }

  for (const historicalItem of historical) {
    if (matches.has(historicalItem.index)) continue
    const candidates = current
      .filter((item) => !usedCurrent.has(item.index))
      .map((item) => {
        const sameName = historicalItem.value.taskName !== ''
          && historicalItem.value.taskName === item.value.taskName
        const sharedFields = sharedMeaningfulFieldCount(historicalItem.value, item.value)
        return {
          item,
          eligible: sameName || sharedFields >= 2,
          score: (sameName ? 10 : 0) + sharedFields * 2 - Math.abs(item.index - historicalItem.index) * 0.1,
        }
      })
      .filter((candidate) => candidate.eligible)
      .sort((left, right) => right.score - left.score)
    if (candidates[0]) match(historicalItem.index, candidates[0].item.index)
  }

  const unmatchedHistorical = historical.filter((item) => !matches.has(item.index))
  const unmatchedCurrent = current.filter((item) => !usedCurrent.has(item.index))
  if (unmatchedHistorical.length === unmatchedCurrent.length) {
    unmatchedHistorical.forEach((historicalItem, index) => {
      const currentItem = unmatchedCurrent[index]
      if (historicalItem.id === null && currentItem) match(historicalItem.index, currentItem.index)
    })
  }

  return matches
}

function displayMonthPlanValue(key: MonthPlanFieldKey, value: string | number): string {
  if (key === 'workType') return workTypeLabels[String(value)] ?? (String(value) || '未分类')
  if (key === 'performanceWeight') return `${Number(value)}%`
  if (key === 'estimatedValue') return `¥${Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
  return String(value) || '未填写'
}

function changedMonthPlanFields(
  before: IndexedMonthPlanItem,
  after: IndexedMonthPlanItem,
  orderChanged: boolean,
): FieldChange[] {
  const fields: FieldChange[] = []
  if (orderChanged) {
    fields.push({
      key: 'order',
      label: '任务顺序',
      before: `第 ${before.index + 1} 项`,
      after: `第 ${after.index + 1} 项`,
    })
  }
  for (const field of monthPlanFields) {
    const beforeValue = before.value[field.key]
    const afterValue = after.value[field.key]
    if (beforeValue === afterValue) continue
    fields.push({
      key: field.key,
      label: field.label,
      before: displayMonthPlanValue(field.key, beforeValue),
      after: displayMonthPlanValue(field.key, afterValue),
    })
  }
  return fields
}

function taskTitle(item?: NormalizedMonthPlanItem): string {
  return item?.taskName || '未命名任务'
}

function removedSortPosition(
  historicalIndex: number,
  matches: Map<number, number>,
  currentLength: number,
): number {
  const previous = [...matches.entries()]
    .filter(([index]) => index < historicalIndex)
    .sort((left, right) => right[0] - left[0])[0]
  if (previous) return previous[1] + 0.5
  const next = [...matches.entries()]
    .filter(([index]) => index > historicalIndex)
    .sort((left, right) => left[0] - right[0])[0]
  if (next) return Math.max(-0.5, next[1] - 0.5)
  return Math.min(historicalIndex, currentLength) + 0.5
}

function buildMonthPlanItemChanges(
  historical: IndexedMonthPlanItem[],
  current: IndexedMonthPlanItem[],
  matches: Map<number, number>,
): MonthPlanItemChange[] {
  const historicalRank = new Map([...matches.keys()].sort((left, right) => left - right).map((index, rank) => [index, rank]))
  const currentRank = new Map([...matches.values()].sort((left, right) => left - right).map((index, rank) => [index, rank]))
  const matchedCurrent = new Set(matches.values())
  const changes: PositionedMonthPlanItemChange[] = []

  for (const [historicalIndex, currentIndex] of matches) {
    const before = historical[historicalIndex]
    const after = current[currentIndex]
    if (!before || !after) continue
    const orderChanged = historicalRank.get(historicalIndex) !== currentRank.get(currentIndex)
    const fields = changedMonthPlanFields(before, after, orderChanged)
    if (!fields.length) continue
    changes.push({
      key: `modified-${before.id ?? historicalIndex}-${after.id ?? currentIndex}`,
      type: 'modified',
      title: taskTitle(after.value),
      before: before.value,
      after: after.value,
      fields,
      sortPosition: currentIndex,
    })
  }

  for (const item of current) {
    if (matchedCurrent.has(item.index)) continue
    changes.push({
      key: `added-${item.id ?? item.index}`,
      type: 'added',
      title: taskTitle(item.value),
      after: item.value,
      fields: [],
      sortPosition: item.index,
    })
  }

  for (const item of historical) {
    if (matches.has(item.index)) continue
    changes.push({
      key: `removed-${item.id ?? item.index}`,
      type: 'removed',
      title: taskTitle(item.value),
      before: item.value,
      fields: [],
      sortPosition: removedSortPosition(item.index, matches, current.length),
    })
  }

  return changes
    .sort((left, right) => left.sortPosition - right.sortPosition || left.type.localeCompare(right.type))
    .map(({ sortPosition: _sortPosition, ...change }) => change)
}

export function compareMonthPlanSnapshots(
  historicalSnapshot: PlanDraftSnapshot,
  currentSnapshot: PlanDraftSnapshot,
): MonthPlanDiff {
  const historical = indexedMonthPlanItems(historicalSnapshot)
  const current = indexedMonthPlanItems(currentSnapshot)
  const matches = matchMonthPlanItems(historical, current)
  const items = buildMonthPlanItemChanges(historical, current, matches)
  const counts = {
    added: items.filter((item) => item.type === 'added').length,
    modified: items.filter((item) => item.type === 'modified').length,
    removed: items.filter((item) => item.type === 'removed').length,
  }
  return { items, counts, unchanged: items.length === 0 }
}

function normalizeWeekPlanItem(value: unknown) {
  const item = record(value)
  return {
    monthPlanItemId: nullableId(item.monthPlanItemId),
    content: text(item.content).trim(),
    completionStatus: text(item.completionStatus).trim(),
    deliverable: text(item.deliverable).trim(),
    quality: text(item.quality).trim(),
    remark: text(item.remark).trim(),
    plannedFinishDate: text(item.plannedFinishDate),
  }
}

export function normalizeSnapshot(planType: PlanDraftSnapshotType, value: PlanDraftSnapshot) {
  const snapshot = record(value)
  if (planType === 'MONTH') return {
    planMonth: text(snapshot.planMonth),
    items: list(snapshot.items).map(normalizeMonthPlanItem),
  }
  if (planType === 'WEEK') return {
    weekStart: text(snapshot.weekStart),
    items: list(snapshot.items).map(normalizeWeekPlanItem),
  }
  return {
    planDate: text(snapshot.planDate),
    relatedWeekPlanItemIds: list(snapshot.relatedWeekPlanItemIds)
      .map(nullableId)
      .filter((id): id is number => id !== null),
    content: text(snapshot.content),
    remark: text(snapshot.remark),
  }
}

export function arePlanSnapshotsEqual(
  planType: PlanDraftSnapshotType,
  left: PlanDraftSnapshot,
  right: PlanDraftSnapshot,
) {
  return JSON.stringify(normalizeSnapshot(planType, left)) === JSON.stringify(normalizeSnapshot(planType, right))
}
