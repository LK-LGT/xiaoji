export interface MonthPlanPeriodItem {
  estimatedStartDate?: string | null
  estimatedEndDate?: string | null
}

import { periodMonthKey, toDashedPeriod } from './period.ts'

function compactDate(value?: string | null) {
  const text = String(value ?? '').trim()
  const match = /^(?:\d{4}-)?(\d{1,2})-(\d{1,2})$/.exec(text)
  return match ? `${Number(match[1])}.${Number(match[2])}` : text
}

export function formatMonthPlanPeriod(
  start?: string | null,
  end?: string | null,
  emptyText = '—',
) {
  const startText = compactDate(start)
  const endText = compactDate(end)
  if (!startText && !endText) return emptyText
  if (startText && endText && startText !== endText) return `${startText}-${endText}`
  return startText || endText
}

export function formatMonthPlanStandard(
  deliverable?: string | null,
  quality?: string | null,
  emptyText = '—',
) {
  const deliverableText = String(deliverable ?? '').trim()
  const qualityText = String(quality ?? '').trim()
  if (!deliverableText && !qualityText) return emptyText
  return [
    deliverableText ? `交付物：${deliverableText}` : '',
    qualityText ? `质量：${qualityText}` : '',
  ].filter(Boolean).join('\n')
}

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function localToday() {
  const date = new Date()
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function validDate(value: unknown) {
  const text = String(value ?? '')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return ''
  const date = new Date(text)
  return Number.isNaN(date.getTime()) ? '' : text
}

/** Keeps valid AI dates and deterministically fills missing or invalid periods by task order. */
export function normalizeMonthPlanPeriods<T extends MonthPlanPeriodItem>(
  planMonth: string,
  items: T[],
  today = localToday(),
): Array<T & { estimatedStartDate: string; estimatedEndDate: string }> {
  // ⚠️ **BUG-R6-26**：统一走 `toDashedPeriod`，同时兼容紧凑 `202611` 与带横杠 `2026-11`。
  const dashedPlanMonth = toDashedPeriod(planMonth)
  if (!dashedPlanMonth || !items.length) {
    return items.map((item) => ({
      ...item,
      estimatedStartDate: String(item.estimatedStartDate ?? ''),
      estimatedEndDate: String(item.estimatedEndDate ?? ''),
    }))
  }

  const [year, month] = dashedPlanMonth.split('-').map(Number)
  const lastDay = new Date(year, month, 0).getDate()
  // ⚠️ **BUG-R6-26**：统一走 `periodMonthKey`（不再手写 `slice(0, 7)`）。
  const firstDay = dashedPlanMonth === periodMonthKey(today) ? Math.min(Math.max(Number(today.slice(8, 10)) || 1, 1), lastDay) : 1
  const availableDays = lastDay - firstDay + 1

  return items.map((item, index) => {
    const fallbackStartDay = firstDay + Math.floor((index * availableDays) / items.length)
    const fallbackEndDay = Math.max(fallbackStartDay, firstDay + Math.floor(((index + 1) * availableDays) / items.length) - 1)
    const fallbackStart = `${planMonth}-${pad(fallbackStartDay)}`
    const fallbackEnd = `${planMonth}-${pad(fallbackEndDay)}`
    const start = validDate(item.estimatedStartDate)
    const end = validDate(item.estimatedEndDate)

    if (start && end && start <= end) return { ...item, estimatedStartDate: start, estimatedEndDate: end }
    if (start && !end) return { ...item, estimatedStartDate: start, estimatedEndDate: start > fallbackEnd ? start : fallbackEnd }
    if (!start && end) return { ...item, estimatedStartDate: end < fallbackStart ? end : fallbackStart, estimatedEndDate: end }
    return { ...item, estimatedStartDate: fallbackStart, estimatedEndDate: fallbackEnd }
  })
}
