export interface WeekPlanFieldSource {
  content?: string | null
  completionStatus?: string | null
  deliverable?: string | null
  quality?: string | null
  remark?: string | null
}

const WORK_POINT_PREFIX = /^\s*(?:\d+\s*[.、)]|[-*•])\s*/

function nonEmptyLines(value: string | null | undefined) {
  return String(value || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
}

export function mergeWeekWorkContent(values: Array<string | null | undefined>) {
  const points = values
    .flatMap(nonEmptyLines)
    .map((line) => line.replace(WORK_POINT_PREFIX, '').trim())
    .filter(Boolean)
  return points.map((point, index) => `${index + 1}. ${point}`).join('\n')
}

export function mergeWeekTextField(values: Array<string | null | undefined>) {
  const seen = new Set<string>()
  return values
    .map((value) => String(value || '').trim())
    .filter((value) => value && !seen.has(value) && !!seen.add(value))
    .join('\n')
}

export function mergeWeekPlanFields(items: WeekPlanFieldSource[]) {
  return {
    content: mergeWeekWorkContent(items.map((item) => item.content)),
    completionStatus: mergeWeekTextField(items.map((item) => item.completionStatus)),
    deliverable: mergeWeekTextField(items.map((item) => item.deliverable)),
    quality: mergeWeekTextField(items.map((item) => item.quality)),
    remark: mergeWeekTextField(items.map((item) => item.remark)),
  }
}

export function countWeekWorkPoints(content: string | null | undefined) {
  return nonEmptyLines(content).length
}
