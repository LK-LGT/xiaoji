import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (path: string) => readFileSync(fileURLToPath(new URL(path, import.meta.url)), 'utf8')
const ledger = read('../src/views/leader/TeamLedger.vue')
const adapters = read('../src/views/performanceAdapters.ts')

test('team ledger starts with no personnel, organization, time, or status filters', () => {
  assert.match(ledger, /periodType:\s*''/)
  assert.match(ledger, /periodRange:\s*\[\]/)
  assert.match(ledger, /employee:\s*''/)
  assert.doesNotMatch(ledger, /status:/)
  assert.match(ledger, /placeholder="全部直属范围"/)
  assert.match(ledger, /placeholder="全部周期"/)
})

test('team ledger omits empty filters and still loads an empty organization scope', () => {
  assert.doesNotMatch(ledger, /if \(scopeOrgId\.value == null\)/)
  assert.match(ledger, /scopeOrgId:\s*scopeOrgId\.value/)
  assert.match(ledger, /employeeName:\s*query\.employee\.trim\(\) \|\| undefined/)
  assert.match(ledger, /periodType:\s*query\.periodType \? periodTypeCode\(query\.periodType\) : undefined/)
  assert.doesNotMatch(ledger, /status:/)
})

test('team ledger supports all organizations and periods while export requires an explicit date range', () => {
  assert.match(ledger, /if \(!hasExportPeriod\(\)\)/)
  assert.match(ledger, /createExportTask\(scopeOrgId\.value == null \? undefined : String\(scopeOrgId\.value\)/)
  assert.match(ledger, /periodType:\s*query\.periodType \? periodTypeCode\(query\.periodType\) : 'CUSTOM'/)
  assert.match(adapters, /item\.periodType === 'ALL'/)
  assert.match(adapters, /return '全部'/)
})
