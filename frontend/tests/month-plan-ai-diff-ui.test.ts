import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { normalizeMonthPlanPeriods as normalizePcPeriods } from '../src/utils/monthPlanPeriod.ts'
import { normalizeMonthPlanPeriods as normalizeMiniPeriods } from '../../企业计划成果管理系统/utils/month-plan-period.js'

const diffPath = fileURLToPath(new URL('../src/views/employee/components/MonthPlanAiDiff.vue', import.meta.url))
const workspacePath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanAiWorkspace.vue', import.meta.url))
const editorPath = fileURLToPath(new URL('../src/views/employee/EmployeeMonthPlanEdit.vue', import.meta.url))
const miniEditorPath = fileURLToPath(new URL('../../企业计划成果管理系统/pages/employee/month/edit.vue', import.meta.url))
const miniDraftSheetPath = fileURLToPath(new URL('../../企业计划成果管理系统/components/plan/MonthAiDraftSheet.vue', import.meta.url))
const diff = readFileSync(diffPath, 'utf8')
const workspace = readFileSync(workspacePath, 'utf8')
const editor = readFileSync(editorPath, 'utf8')
const miniEditor = readFileSync(miniEditorPath, 'utf8')
const miniDraftSheet = readFileSync(miniDraftSheetPath, 'utf8')

test('month plan AI result preview keeps only the back action above selectable changes', () => {
  assert.match(diff, /返回助手/)
  assert.doesNotMatch(diff, /class="change-summary"/)
  assert.doesNotMatch(diff, /AI 建议已生成/)
  assert.doesNotMatch(diff, /当前计划优化建议/)
  assert.doesNotMatch(diff, /选择需要的内容，再应用到当前计划/)
  assert.match(diff, /应用到当前计划/)
  assert.match(diff, /selectedIndexes/)
  assert.match(diff, /operationMode/)
  assert.doesNotMatch(diff, /class="compare-workspace"/)
  assert.doesNotMatch(diff, /v-model="draft\.summary"/)
  assert.doesNotMatch(diff, /<el-table/)
  assert.doesNotMatch(diff, /append-to-body/)
})

test('month plan AI warnings are collapsed so they do not dominate the preview', () => {
  assert.match(diff, /<details v-if="suggestion\?\.warnings\?\.length" class="warning-summary">/)
  assert.match(diff, /AI 参考提醒/)
  assert.match(diff, /\.warning-list\s*{\s*display:grid;[^}]*max-height:140px;[^}]*overflow:auto;/)
  assert.doesNotMatch(diff, /el-alert v-for="warning/)
})

test('month plan AI replacement uses fresh items while append keeps current ids', () => {
  assert.match(workspace, /:current-items="currentItems"/)
  assert.match(workspace, /function mapCurrentItems\(\)/)
  assert.match(workspace, /id:\s*item\.id/)
  assert.match(workspace, /function stripIds\(items: AiPlanDraftItem\[\]\)/)
  assert.match(diff, /export interface AiPlanDraftItem extends AiPlanItem/)
  assert.match(workspace, /const proposedItems = value\.mode === 'append' \? \[\.\.\.currentItems\.value, \.\.\.value\.items\] : value\.items/)
  assert.doesNotMatch(diff, /normalizeItem\(item,\s*append \? undefined : props\.currentItems\[index\]\)/)
})

test('month plan AI preview selects suggestions when it opens', () => {
  assert.match(diff, /props\.suggestion\?\.items\.map/)
  assert.match(diff, /if \(visible\) resetSelection\(\)/)
})

test('month plan AI tasks without dates are scheduled in order inside the current month', () => {
  const tasks = Array.from({ length: 3 }, (_, index) => ({ taskName: `任务${index + 1}` }))
  const expected = [
    ['2026-08-06', '2026-08-13'],
    ['2026-08-14', '2026-08-22'],
    ['2026-08-23', '2026-08-31'],
  ]
  const pc = normalizePcPeriods('2026-08', tasks, '2026-08-06')
  const mini = normalizeMiniPeriods('2026-08', tasks, '2026-08-06')

  assert.deepEqual(pc.map((item) => [item.estimatedStartDate, item.estimatedEndDate]), expected)
  assert.deepEqual(mini.map((item) => [item.estimatedStartDate, item.estimatedEndDate]), expected)
})

test('month plan scheduling preserves valid AI dates and repairs partial dates', () => {
  const result = normalizePcPeriods('2026-09', [
    { estimatedStartDate: '2026-09-03', estimatedEndDate: '2026-09-08' },
    { estimatedStartDate: '2026-09-20', estimatedEndDate: '' },
    { estimatedStartDate: '', estimatedEndDate: '2026-09-28' },
  ], '2026-08-06')

  assert.deepEqual([result[0].estimatedStartDate, result[0].estimatedEndDate], ['2026-09-03', '2026-09-08'])
  assert.deepEqual([result[1].estimatedStartDate, result[1].estimatedEndDate], ['2026-09-20', '2026-09-20'])
  assert.deepEqual([result[2].estimatedStartDate, result[2].estimatedEndDate], ['2026-09-21', '2026-09-28'])
})

test('PC and mini month plan flows retain dates and repair old empty drafts before submit', () => {
  assert.match(workspace, /normalizeMonthPlanPeriods\(plan\.value\.planMonth, proposedItems\)/)
  assert.match(editor, /normalizeMonthPlanPeriods\(detail\.planMonth, detail\.items\.map/)
  assert.match(editor, /function validateDraft[\s\S]*?ensureFormItemPeriods\(\)/)
  assert.match(miniEditor, /estimatedStartDate:\s*item\.estimatedStartDate/)
  assert.match(miniEditor, /estimatedEndDate:\s*item\.estimatedEndDate/)
  assert.match(miniEditor, /normalizeMonthPlanPeriods\(form\.planMonth/)
  assert.doesNotMatch(miniEditor, /deadline:\s*item\.deadline\s*\|\|\s*null/)
  assert.match(miniDraftSheet, /item\.estimatedStartDate/)
  assert.match(miniDraftSheet, /item\.estimatedEndDate/)
})
