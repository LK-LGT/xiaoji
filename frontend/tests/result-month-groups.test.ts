import assert from 'node:assert/strict'
import test from 'node:test'
import { currentMonthKey, groupRowsByMonth, paginateMonthGroups } from '../src/utils/monthGroups.ts'

test('groups rows into descending non-empty result months', () => {
  const rows = [
    { id: 1, resultDate: '2025-12-31' },
    { id: 2, resultDate: '2026-02-02' },
    { id: 3, resultDate: 'invalid' },
    { id: 4, resultDate: '2026-02-16' },
    { id: 5, resultDate: '2026-01-01' },
  ]

  const groups = groupRowsByMonth(rows, (row) => row.resultDate)

  assert.deepEqual(groups.map((group) => group.key), ['2026-02', '2026-01', '2025-12'])
  assert.equal(groups[0].label, '2026年2月')
  assert.deepEqual(groups[0].rows.map((row) => row.id), [2, 4])
  assert.equal(groups.flatMap((group) => group.rows).some((row) => row.id === 3), false)
})

test('paginates by 10 month groups rather than row count', () => {
  const rows = Array.from({ length: 12 }, (_, index) => ({
    id: index + 1,
    resultDate: `2025-${String(index + 1).padStart(2, '0')}-01`,
  }))
  const groups = groupRowsByMonth(rows, (row) => row.resultDate)

  assert.equal(paginateMonthGroups(groups, 1).length, 10)
  assert.deepEqual(paginateMonthGroups(groups, 2).map((group) => group.key), ['2025-02', '2025-01'])
})

test('formats the current local month key', () => {
  assert.equal(currentMonthKey(new Date(2026, 6, 31)), '2026-07')
})
