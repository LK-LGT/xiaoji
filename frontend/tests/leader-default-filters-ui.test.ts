import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const sourceRoot = new URL('../src/', import.meta.url)

async function readSource(path: string) {
  return readFile(new URL(path, sourceRoot), 'utf8')
}

test('leader organization scope is empty by default and can opt into selecting the first organization', async () => {
  const source = await readSource('composables/useLeaderOrgScope.ts')

  assert.match(source, /useLeaderOrgScope\(selectFirstByDefault = false\)/)
  assert.match(source, /selectFirstByDefault \? orgOptions\.value\[0\]\?\.value : undefined/)
})

test('leader list pages start without date month status or period filters', async () => {
  const [daily, result, adjustment, ledger, week, month] = await Promise.all([
    readSource('views/leader/DailyReview.vue'),
    readSource('views/leader/ResultSuggest.vue'),
    readSource('views/leader/PlanAdjust.vue'),
    readSource('views/leader/TeamLedger.vue'),
    readSource('views/leader/WeekPlanApproval.vue'),
    readSource('views/department/MonthPlanApproval.vue'),
  ])

  assert.match(daily, /dateRange: routeDate \? \[routeDate, routeDate\] : \[\]/)
  assert.match(result, /dateRange: \[\] as string\[\]/)
  assert.match(adjustment, /month: ''[\s\S]*status: ''/)
  assert.match(ledger, /periodType: ''[\s\S]*periodRange: \[\] as string\[\]/)
  assert.match(week, /status = ref\(''\)[\s\S]*weekStart = ref\(''\)/)
  assert.match(month, /year: ''[\s\S]*month: ''[\s\S]*status: ''[\s\S]*keyword: ''/)
})

test('empty organization filters are sent as unrestricted authorized-scope queries', async () => {
  const pages = await Promise.all([
    readSource('views/leader/DailyReview.vue'),
    readSource('views/leader/ResultSuggest.vue'),
    readSource('views/leader/PlanAdjust.vue'),
    readSource('views/leader/TeamLedger.vue'),
  ])

  for (const source of pages) {
    assert.doesNotMatch(source, /if \(scopeOrgId\.value == null\)[\s\S]{0,180}tableRows\.value = \[\]/)
    assert.match(source, /scopeOrgId: scopeOrgId\.value/)
  }
})

test('team ledger explicitly requests all periods when its filters are empty', async () => {
  const [ledger, adapter] = await Promise.all([
    readSource('views/leader/TeamLedger.vue'),
    readSource('views/performanceAdapters.ts'),
  ])

  assert.match(ledger, /periodType: query\.periodType \? periodTypeCode\(query\.periodType\) : undefined/)
  assert.match(adapter, /item\.periodType === 'ALL'\) return '全部'/)
})
