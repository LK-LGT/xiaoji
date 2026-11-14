export interface MonthGroup<T> {
  key: string
  label: string
  rows: T[]
}

const MONTH_PREFIX = /^(\d{4})-(0[1-9]|1[0-2])(?:-|$)/

export function currentMonthKey(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}

export function groupRowsByMonth<T>(rows: T[], dateOf: (row: T) => string | undefined): MonthGroup<T>[] {
  const rowsByMonth = new Map<string, T[]>()
  for (const row of rows) {
    const match = dateOf(row)?.match(MONTH_PREFIX)
    if (!match) continue
    const key = `${match[1]}-${match[2]}`
    const monthRows = rowsByMonth.get(key)
    if (monthRows) monthRows.push(row)
    else rowsByMonth.set(key, [row])
  }

  return [...rowsByMonth.entries()]
    .sort(([left], [right]) => right.localeCompare(left))
    .map(([key, monthRows]) => {
      const [year, month] = key.split('-')
      return { key, label: `${year}年${Number(month)}月`, rows: monthRows }
    })
}

export function paginateMonthGroups<T>(groups: MonthGroup<T>[], page: number, pageSize = 10) {
  const normalizedPage = Math.max(1, Math.trunc(page) || 1)
  const normalizedSize = Math.max(1, Math.trunc(pageSize) || 10)
  const start = (normalizedPage - 1) * normalizedSize
  return groups.slice(start, start + normalizedSize)
}
